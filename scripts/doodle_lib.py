#!/usr/bin/env python3
"""photo-doodle engine: hand-drawn marker doodles over a photo.

Strokes look like a real person drew them: Catmull-Rom paths with arc-length
sine wobble, round caps, tapered tips, a dark offset underlay for legibility
on bright areas, and a soft glow. Five brushes: marker (solid / gradient),
water (translucent wet wash), crayon (grainy), glitter (line + specks),
highlight (wide translucent). Authoring records draw-ops; save() replays all
of them into a JPG and save_video() replays them along a timeline into an
MP4 so the viewer watches every stroke being drawn.

Usage (from a theme script):
    from doodle_lib import *
    d = Doodle('/path/photo.jpg', seed=7)
    d.stroke([(0.3, 0.2), (0.5, 0.18), (0.7, 0.2)], color=PINK, w=5)
    d.text('hello', 0.5, 0.9, 40)
    d.save('/out/v1.jpg')
    d.save_video('/out/v1.mp4')

All coordinates are normalized (0..1) relative to image width/height.
"""
import math, random, os, subprocess, bisect
from PIL import Image, ImageDraw, ImageFont, ImageFilter

S = 2  # supersample factor

# ---- palette (RGBA). Pastel colors read well on most photos. ----
WHITE = (255, 255, 255, 240)
PINK = (255, 168, 190, 238)
HOTPINK = (255, 120, 170, 240)
CORAL = (255, 145, 125, 240)
PEACH = (255, 205, 165, 240)
YELLOW = (255, 224, 120, 240)
LEMON = (255, 240, 150, 240)
MINT = (185, 240, 205, 238)
SKY = (150, 205, 255, 240)
BLUE = (168, 214, 255, 238)
LAVENDER = (205, 185, 255, 240)
RED = (245, 105, 95, 240)
INK = (60, 56, 52, 235)          # soft black, for dark-marker looks
UNDER = (42, 62, 46, 78)         # offset underlay for contrast on bright areas

PEN_SPEED = 1300.0  # px/s at final resolution
FPS = 30

def dk(c, f, a=None):
    """darken color by factor f, optionally override alpha"""
    return (int(c[0]*f), int(c[1]*f), int(c[2]*f), a if a is not None else c[3])

def al(c, a):
    """same color, new alpha"""
    return (c[0], c[1], c[2], a)

# ---- fonts: handwriting-style, with CJK fallback ----
FONT_DIRS = ['/System/Library/Fonts/Supplemental/', '/System/Library/Fonts/', '/Library/Fonts/']
LATIN_FONTS = {  # casual -> fancy
    'marker': 'MarkerFelt.ttc', 'chalk': 'ChalkboardSE.ttc', 'hand': 'Bradley Hand Bold.ttf',
    'note': 'Noteworthy.ttc', 'script': 'SnellRoundhand.ttc', 'savoye': 'Savoye LET.ttc',
    'comic': 'Comic Sans MS Bold.ttf',
}
CJK_FONTS = ['Hiragino Sans GB.ttc', 'STHeiti Light.ttc', 'PingFang.ttc']

def font(name, size):
    """name: a LATIN_FONTS key, a filename, or 'cjk' for Chinese/Japanese text."""
    names = CJK_FONTS if name == 'cjk' else [LATIN_FONTS.get(name, name)]
    for nm in names:
        for d in FONT_DIRS:
            p = os.path.join(d, nm)
            if os.path.exists(p):
                return ImageFont.truetype(p, int(size))
    raise FileNotFoundError(name)

def has_cjk(s):
    return any('一' <= ch <= '鿿' or '぀' <= ch <= 'ヿ' for ch in s)

# ---- geometry helpers ----

def catmull(pts, n=160):
    if len(pts) == 2:
        return [(pts[0][0] + (pts[1][0]-pts[0][0])*t, pts[0][1] + (pts[1][1]-pts[0][1])*t)
                for t in [i/(n-1) for i in range(n)]]
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    segs = len(p) - 3
    m = max(3, n // segs)
    for i in range(segs):
        p0, p1, p2, p3 = p[i], p[i+1], p[i+2], p[i+3]
        for j in range(m):
            t = j / m
            t2, t3 = t*t, t*t*t
            x = 0.5*((2*p1[0]) + (-p0[0]+p2[0])*t + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
            y = 0.5*((2*p1[1]) + (-p0[1]+p2[1])*t + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
            out.append((x, y))
    out.append(p[-2])
    return out

def add_wobble(path, amp, rng):
    if amp <= 0 or len(path) < 3:
        return path
    ph1, ph2 = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    f1, f2 = rng.uniform(1.5, 2.6), rng.uniform(4.0, 6.5)
    d = [0.0]
    for i in range(1, len(path)):
        d.append(d[-1] + math.hypot(path[i][0]-path[i-1][0], path[i][1]-path[i-1][1]))
    L = max(d[-1], 1e-6)
    out = []
    for i, (x, y) in enumerate(path):
        t = d[i] / L
        if i == 0:
            dx, dy = path[1][0]-x, path[1][1]-y
        else:
            dx, dy = x-path[i-1][0], y-path[i-1][1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy/n, dx/n
        off = amp * (0.7*math.sin(2*math.pi*f1*t + ph1) + 0.3*math.sin(2*math.pi*f2*t + ph2))
        out.append((x + nx*off, y + ny*off))
    return out

def arclens(path):
    d = [0.0]
    for i in range(1, len(path)):
        d.append(d[-1] + math.hypot(path[i][0]-path[i-1][0], path[i][1]-path[i-1][1]))
    return d, max(d[-1], 1e-6)

def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i]-c1[i])*t) for i in range(4))

def smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t*t*(3 - 2*t)


# ---- cartoon hand-holding-marker sprite (nib anchored at canvas center) ----

def build_hand_sprite(pen_color=(255, 120, 170), extent=300):
    """Cartoon fist gripping a marker; the nib tip sits exactly at the
    sprite's center so pasting at (pen_x - w/2, pen_y - h/2) anchors it.
    extent: sprite reach from nib in px (arm runs off toward lower-right)."""
    B = 2
    sc = extent / 250.0          # geometry authored at 250px reach
    cw = extent * 2 * B
    img = Image.new('RGBA', (cw, cw), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    cx, cy = cw // 2, cw // 2
    ux, uy = 0.66, 0.751         # pen axis: nib -> butt (lower-right)
    nx, ny = -uy, ux
    def U(t, n=0.0):
        return (cx + (ux*t + nx*n) * B * sc, cy + (uy*t + ny*n) * B * sc)

    SKIN = (255, 219, 187, 255)
    SKIN_DK = (231, 180, 143, 255)
    LINE = (146, 98, 70, 255)

    def capsule(p1, p2, w, fill, outline=None, ow=2.4):
        w = w * sc
        if outline:
            dr.line([p1, p2], fill=outline, width=int(w + ow*2*B*sc))
            for p in (p1, p2):
                r = (w + ow*2*B*sc) / 2
                dr.ellipse([p[0]-r, p[1]-r, p[0]+r, p[1]+r], fill=outline)
        dr.line([p1, p2], fill=fill, width=int(w))
        for p in (p1, p2):
            r = w/2
            dr.ellipse([p[0]-r, p[1]-r, p[0]+r, p[1]+r], fill=fill)

    pc = tuple(pen_color[:3]) + (255,)
    pc_dk = tuple(int(c*0.72) for c in pen_color[:3]) + (255,)

    # arm + cuff (bottom layer, runs off lower-right)
    capsule(U(165, 40), U(230, 60), 62*B, SKIN, outline=LINE)
    capsule(U(228, 60), U(250, 68), 74*B, (250, 248, 244, 255), outline=(170, 162, 152, 255))
    # pen
    dr.polygon([U(0, 0), U(22, 10), U(22, -10)], fill=(70, 64, 60, 255))
    capsule(U(22, 0), U(36, 0), 20*B, (215, 211, 206, 255))
    capsule(U(36, 0), U(150, 0), 30*B, pc, outline=(96, 90, 86, 255), ow=1.4)
    dr.line([U(44, 10), U(144, 10)], fill=pc_dk, width=int(5*B*sc))
    dr.line([U(44, -9), U(140, -9)], fill=(255, 255, 255, 130), width=int(4*B*sc))
    capsule(U(150, 0), U(162, 0), 26*B, pc_dk)
    # palm below barrel, four fingers wrapping over it, thumb along it
    px, py = U(112, 34)
    r1, r2 = 46*B*sc, 40*B*sc
    dr.ellipse([px-r1-2.4*B*sc, py-r2-2.4*B*sc, px+r1+2.4*B*sc, py+r2+2.4*B*sc], fill=LINE)
    dr.ellipse([px-r1, py-r2, px+r1, py+r2], fill=SKIN)
    for i, t in enumerate((76, 96, 116, 136)):
        w_ = (21, 22, 22, 20)[i]
        tip_n = (-16, -19, -18, -14)[i]
        tip = U(t - 6, tip_n)
        capsule(U(t, 34), tip, w_*B, SKIN, outline=LINE)
        rr = 5*B*sc
        dr.ellipse([tip[0]-rr, tip[1]-rr*0.8, tip[0]+rr, tip[1]+rr*0.8], fill=SKIN_DK)
    capsule(U(92, 22), U(52, 8), 23*B, SKIN, outline=LINE)
    return img.resize((cw//B, cw//B), Image.LANCZOS)


class Doodle:
    def __init__(self, src, seed=7, max_side=None):
        base = Image.open(src).convert('RGB')
        if max_side and max(base.size) > max_side:
            sc = max_side / max(base.size)
            base = base.resize((int(base.width*sc), int(base.height*sc)), Image.LANCZOS)
        self.W, self.H = base.size
        self.img = base.resize((self.W*S, self.H*S), Image.LANCZOS).convert('RGBA')
        self.rng = random.Random(seed)
        self.ops = []

    def P(self, x, y):
        return (x*self.W*S, y*self.H*S)

    # aspect-corrected x-offset: dx expressed as fraction of HEIGHT so circles stay round
    def ax(self, d):
        return d * self.H / self.W

    # ================= authoring: primitives =================

    def stroke(self, pts, color=WHITE, w=5, wob=1.8, n=160, taper=None, under=True,
               closed=False, kind='stroke', brush='marker', color2=None):
        """pts: normalized points. w: width px. wob: hand-shake amplitude px.
        taper: None|'tip'|'both'. brush: marker|water|crayon|glitter|highlight.
        color2: gradient end color (marker only). under: dark offset underlay."""
        P = [self.P(*p) for p in pts]
        if closed:
            P = P + [P[0]]
        path = add_wobble(catmull(P, n), wob*S, self.rng)
        d, L = arclens(path)
        op = {'kind': kind, 'path': path, 'd': d, 'L': L, 'color': color, 'color2': color2,
              'w': w, 'taper': taper, 'under': under, 'brush': brush,
              'seed': self.rng.randrange(1 << 30)}
        if brush == 'glitter':
            rr = random.Random(op['seed'])
            specks = []
            for _ in range(max(3, int(L/S/9))):
                u = rr.random()
                idx = min(bisect.bisect_left(d, u*L), len(path)-1)
                x, y = path[idx]
                jx, jy = rr.uniform(-1, 1)*w*1.4*S, rr.uniform(-1, 1)*w*1.4*S
                typ = 'plus' if rr.random() < 0.3 else 'dot'
                sz = rr.uniform(2.6, 4.6)*S if typ == 'plus' else rr.uniform(0.8, 1.9)*S
                col = rr.choice([WHITE, color, al(LEMON, 235)])
                specks.append((u, x+jx, y+jy, typ, sz, col))
            op['specks'] = sorted(specks)
        self.ops.append(op)
        return path

    def dashed(self, pts, color=WHITE, w=3.5, dash=0.012, gap=0.010, under=True):
        P = add_wobble(catmull([self.P(*p) for p in pts], 300), 1.2*S, self.rng)
        dp, gp = dash*self.H*S, gap*self.H*S
        seg, acc, on = [], 0.0, True
        for i, p in enumerate(P):
            if i:
                acc += math.hypot(p[0]-P[i-1][0], p[1]-P[i-1][1])
            seg.append(p)
            lim = dp if on else gp
            if acc >= lim:
                if on and len(seg) > 1:
                    d, L = arclens(seg)
                    self.ops.append({'kind': 'dash', 'path': list(seg), 'd': d, 'L': L,
                                     'color': color, 'color2': None, 'w': w, 'taper': None,
                                     'under': under, 'brush': 'marker', 'seed': 0})
                seg, acc, on = [p], 0.0, not on

    def dot(self, x, y, r, color=WHITE, under=True):
        self.ops.append({'kind': 'dot', 'c': self.P(x, y), 'r': r*self.H*S, 'color': color, 'under': under})

    def poly(self, pts_px, color, under=True, layer='main'):
        cx = sum(p[0] for p in pts_px)/len(pts_px)
        cy = sum(p[1] for p in pts_px)/len(pts_px)
        self.ops.append({'kind': 'poly', 'pts': pts_px, 'ctr': (cx, cy), 'color': color,
                         'under': under, 'layer': layer})

    def arc(self, cx, cy, rx, ry, a0, a1, color=WHITE, w=4, wob=1.2, n=90, taper=None,
            under=True, brush='marker', color2=None):
        """rx expressed as fraction of height (aspect-corrected); a0/a1 degrees."""
        pts = []
        steps = 14
        for i in range(steps+1):
            a = math.radians(a0 + (a1-a0)*i/steps)
            pts.append((cx + rx*math.cos(a)*self.H/self.W, cy + ry*math.sin(a)))
        return self.stroke(pts, color=color, w=w, wob=wob, n=n, taper=taper, under=under,
                           brush=brush, color2=color2)

    def text(self, s, x, y, size, fname=None, color=WHITE, rot=0, under=True):
        """fname: LATIN_FONTS key or filename; auto-falls back to CJK font for Chinese."""
        if fname is None:
            fname = 'marker'
        if has_cjk(s):
            fname = 'cjk'
        f = font(fname, size*S)
        probe = ImageDraw.Draw(Image.new('RGBA', (4, 4)))
        b = probe.textbbox((0, 0), s, font=f)
        self.ops.append({'kind': 'text', 's': s, 'font': f, 'bbox': b, 'color': color,
                         'rot': rot, 'under': under, 'center': self.P(x, y),
                         'wpx': (b[2]-b[0])/S, '_cache': {}})

    # ================= authoring: motif library =================

    def sparkle(self, x, y, r, color=WHITE, rot=0.0, w=None):
        """4-point twinkle (two crossing tapered strokes)."""
        a = math.radians(rot)
        ww = w or max(2.8, r*self.H*0.24)
        for ang in (a, a+math.pi/2):
            p1 = (x - r*math.cos(ang)*self.H/self.W, y - r*math.sin(ang))
            p2 = (x + r*math.cos(ang)*self.H/self.W, y + r*math.sin(ang))
            self.stroke([p1, (x, y), p2], color=color, w=ww, wob=0.4, n=40, taper='both')

    def star5(self, x, y, r, color=YELLOW, rot=-90, fill=True):
        pts = []
        for i in range(10):
            ang = math.radians(rot + i*36)
            rad = r if i % 2 == 0 else r*0.45
            pts.append((x + rad*math.cos(ang)*self.H/self.W, y + rad*math.sin(ang)))
        if fill:
            self.poly([self.P(*p) for p in pts], color)
        self.stroke(pts + [pts[0]], color=WHITE, w=3.2, wob=0.8, n=140, under=not fill)

    def heart(self, x, y, s, color=PINK, rot=0, fill=True, w=3.4, outline=WHITE):
        pts = []
        a = math.radians(rot)
        for i in range(40):
            t = i/39 * 2*math.pi
            hx = 16*math.sin(t)**3
            hy = -(13*math.cos(t) - 5*math.cos(2*t) - 2*math.cos(3*t) - math.cos(4*t))
            hx, hy = hx*math.cos(a) - hy*math.sin(a), hx*math.sin(a) + hy*math.cos(a)
            pts.append((x + s*hx/16*self.H/self.W, y + s*hy/16))
        if fill:
            self.poly([self.P(*p) for p in pts], color)
            self.stroke(pts + [pts[0]], color=outline, w=w, wob=0.7, n=120, under=False)
        else:
            self.stroke(pts + [pts[0]], color=color, w=w, wob=0.9, n=120)

    def crown(self, x, y, w_=0.09, h_=0.05, color=YELLOW, jewel=HOTPINK, tilt=-0.012):
        pts = [(x - w_/2, y + tilt), (x - w_/2 + 0.004, y - h_*0.52 + tilt),
               (x - w_*0.17, y - h_*0.18), (x, y - h_ - 0.004),
               (x + w_*0.17, y - h_*0.20 - tilt), (x + w_/2 - 0.004, y - h_*0.55 - tilt),
               (x + w_/2, y - tilt)]
        self.stroke(pts, color=color, w=4.6, wob=1.1, n=200)
        self.stroke([(x - w_/2, y + tilt), (x + w_/2, y - tilt)], color=color, w=4.6, wob=1.0, n=40)
        for (sx, sy) in [(x - w_/2 + 0.003, y - h_*0.56), (x, y - h_ - 0.010), (x + w_/2 - 0.004, y - h_*0.60)]:
            self.dot(sx, sy, 0.004, color=jewel)

    def halo(self, x, y, rx=0.075, color=WHITE):
        """double-stroked ellipse ring floating above a head"""
        self.arc(x, y, rx, rx*0.27, -180, 178, color=color, w=6, wob=1.0)
        self.arc(x, y + 0.007, rx, rx*0.27, -178, 180, color=color, w=4.5, wob=1.2, under=False)

    def speech_bubble(self, x, y, rx=0.055, ry=0.033, tail='ll', color=WHITE):
        """ellipse bubble + tail. tail: 'll' lower-left or 'lr' lower-right."""
        self.arc(x, y, rx, ry, 0, 360, color=color, w=4.5, wob=1.1)
        s_ = 1 if tail == 'll' else -1
        tx = x - s_*rx*0.8*self.H/self.W
        self.stroke([(tx, y + ry*0.85), (tx - s_*0.025, y + ry*0.85 + 0.030), (tx + s_*0.007, y + ry*0.95)],
                    color=color, w=3.6, wob=0.6, taper='tip', n=40)

    def arrow(self, pts, color=WHITE, w=3.8):
        """curved annotation arrow; head drawn at the LAST point"""
        path = self.stroke(pts, color=color, w=w, wob=1.0, n=80)
        (x1, y1), (x0, y0) = path[-1], path[-min(8, len(path)-1)]
        ang = math.atan2(y1-y0, x1-x0)
        hl = 11*S
        for da in (2.6, -2.6):
            ex, ey = x1 + hl*math.cos(ang+da), y1 + hl*math.sin(ang+da)
            self.ops.append({'kind': 'stroke', 'path': [(x1, y1), (ex, ey)],
                             'd': [0, hl], 'L': hl, 'color': color, 'color2': None,
                             'w': w, 'taper': None, 'under': False, 'brush': 'marker', 'seed': 0})

    def rainbow(self, x, y, r=0.05, colors=(CORAL, LEMON, SKY), w=5.2):
        for i, col in enumerate(colors):
            rr_ = r * (1 - 0.22*i)
            self.arc(x, y, rr_, rr_*1.35, -178, -2, color=col, w=w, wob=0.7)

    def sun(self, x, y, r=0.030, color=YELLOW, face=True, blush=PEACH):
        self.arc(x, y, r*1.3, r, 0, 360, color=color, w=4.6, wob=0.9)
        for k in range(7):
            a = math.radians(115 + k*22)
            self.stroke([(x + r*1.6*math.cos(a)*self.H/self.W, y + r*1.3*math.sin(a)),
                         (x + r*2.3*math.cos(a)*self.H/self.W, y + r*1.9*math.sin(a))],
                        color=color, w=3.4, wob=0.5, taper='tip', n=40)
        if face:
            for ox in (-0.010, 0.012):
                self.arc(x + ox, y + 0.002, 0.004, 0.004, 20, 160, w=2.4, wob=0.3, under=False)
            self.arc(x + 0.001, y + 0.011, 0.007, 0.005, 20, 160, w=2.4, wob=0.3, under=False)
            if blush:
                self.dot(x - 0.015, y + 0.008, 0.0035, color=blush, under=False)
                self.dot(x + 0.016, y + 0.008, 0.0035, color=blush, under=False)

    def music_note(self, x, y, s=1.0, color=WHITE):
        self.dot(x, y, 0.0085*s, color=color)
        stem = x + self.ax(0.0105*s)
        self.stroke([(stem, y), (stem, y - 0.052*s)], color=color, w=3.6, wob=0.5, n=40)
        self.stroke([(stem, y - 0.052*s), (x + self.ax(0.030*s), y - 0.040*s)],
                    color=color, w=3.6, wob=0.5, taper='tip', n=30)

    def paw_print(self, x, y, s=1.0, color=PINK):
        r = 0.011*s
        self.dot(x, y, r, color=color)
        for k in range(3):
            ang = math.radians(200 + k*70)
            self.dot(x + 1.55*r*math.cos(ang)*self.H/self.W, y + 1.55*r*math.sin(ang), r*0.42, color=color)

    def cat_ears(self, lx, ly, rx_, ry_, s=1.0, color=WHITE, inner=HOTPINK):
        """two ears; (lx,ly)/(rx_,ry_) are the left/right ear base centers on the hair"""
        for (bx, by), sgn in [((lx, ly), -1), ((rx_, ry_), 1)]:
            tipx, tipy = bx + sgn*(-0.033*s), by - 0.070*s
            self.stroke([(bx - sgn*0.040*s, by), (tipx, tipy + 0.006), (tipx + sgn*0.008, tipy),
                         (bx + sgn*0.005, by - 0.045*s), (bx + sgn*0.048*s, by - 0.012*s)],
                        color=color, w=5.5*s, wob=0.9, n=120)
            if inner:
                self.stroke([(bx - sgn*0.008*s, by - 0.020*s), (tipx + sgn*0.014, tipy + 0.022*s),
                             (bx + sgn*0.006*s, by - 0.032*s)],
                            color=inner, w=3.6*s, wob=0.6, under=False, n=50)

    def butterfly(self, x, y, s=1.0, wing=LAVENDER):
        self.stroke([(x - self.ax(0.007*s), y), (x - self.ax(0.019*s), y - 0.017*s), (x - self.ax(0.009*s), y - 0.007*s)],
                    brush='water', color=wing, w=13*s, under=False)
        self.stroke([(x + self.ax(0.007*s), y), (x + self.ax(0.021*s), y - 0.016*s), (x + self.ax(0.010*s), y - 0.005*s)],
                    brush='water', color=wing, w=13*s, under=False)
        self.stroke([(x, y - 0.016*s), (x, y + 0.008*s)], w=3.0*s, wob=0.4, n=30)
        for sgn in (-1, 1):
            self.stroke([(x, y - 0.014*s), (x + sgn*self.ax(0.008*s), y - 0.026*s)],
                        w=2.2*s, wob=0.3, taper='tip', n=20, under=False)

    def tape(self, x, y, w_, h_, rot, color):
        """washi tape: translucent rotated rectangle (wash layer, soft edges)"""
        a = math.radians(rot)
        pp = []
        for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
            dx, dy = sx*w_/2, sy*h_/2
            rx = dx*math.cos(a) - dy*math.sin(a)
            ry = dx*math.sin(a) + dy*math.cos(a)
            pp.append(self.P(x + rx*self.H/self.W, y + ry))
        self.poly(pp, color, under=False, layer='wash')

    def frame(self, m=0.030, color=WHITE, w=4.5):
        """hand-drawn border just inside the photo edges"""
        my = m*1.35
        self.stroke([(m, my), (0.5, my - 0.004), (1-m, my)], color=color, w=w, wob=1.6, n=200)
        self.stroke([(1-m, my), (1-m+0.004, 0.5), (1-m, 1-my)], color=color, w=w, wob=1.6, n=200)
        self.stroke([(1-m, 1-my), (0.5, 1-my+0.004), (m, 1-my)], color=color, w=w, wob=1.6, n=200)
        self.stroke([(m, 1-my), (m-0.003, 0.5), (m, my)], color=color, w=w, wob=1.6, n=200)

    def underline(self, x0, x1, y, color=PINK, brush='highlight', w=14, slope=0.0):
        """swash under text; slope tilts to match rotated text"""
        xm = (x0+x1)/2
        self.stroke([(x0, y - slope), (xm, y), (x1, y + slope)], brush=brush, color=color, w=w, under=False)

    # ================= replay =================

    def _idx_range(self, op, f0, f1):
        i0 = bisect.bisect_left(op['d'], f0*op['L'])
        i1 = bisect.bisect_left(op['d'], f1*op['L'])
        return max(0, i0), min(len(op['path'])-1, i1)

    def _profile(self, op):
        if op['taper'] == 'tip':
            return lambda t: max(0.12, 1.0 - 0.85*t)
        if op['taper'] == 'both':
            return lambda t: max(0.15, math.sin(math.pi*min(max(t, 0), 1))**0.55)
        return None

    def _draw_path_part(self, dr, op, f0, f1, offset=None, color=None, w=None, caps=True):
        path, d, L = op['path'], op['d'], op['L']
        color = color or op['color']
        w = (w if w is not None else op['w'])
        i0, i1 = self._idx_range(op, f0, f1)
        if i1 <= i0 and f1 < 1.0:
            return
        i1 = max(i1, min(i0+1, len(path)-1))
        pts = path[i0:i1+1]
        if offset:
            pts = [(x+offset[0], y+offset[1]) for x, y in pts]
        if len(pts) < 2:
            return
        prof = self._profile(op)
        grad = op.get('color2')
        if prof or grad:
            c2 = grad or color
            for k in range(len(pts)):
                gi = i0 + k
                t = d[min(gi, len(d)-1)]/L
                r = (w*S/2) * (prof(t) if prof else 1.0)
                if r <= 0.2:
                    continue
                col = lerp_color(color, c2, t) if grad else color
                x, y = pts[k]
                dr.ellipse([x-r, y-r, x+r, y+r], fill=col)
        else:
            wpx = max(1, int(round(w*S)))
            dr.line(pts, fill=color, width=wpx, joint='curve')
            if caps:
                r = wpx/2
                for (x, y) in (pts[0], pts[-1]):
                    dr.ellipse([x-r, y-r, x+r, y+r], fill=color)

    def _draw_crayon_part(self, dr, op, f0, f1):
        path, d, L = op['path'], op['d'], op['L']
        prof = self._profile(op)
        step = 1.7*S
        n_st = max(2, int(L/step))
        i_a, i_b = int(f0*n_st), int(min(1.0, f1)*n_st) + (1 if f1 >= 1.0 else 0)
        base = al(op['color'], 60)
        for i in range(i_a, min(i_b, n_st)):
            s_ = i/(n_st-1) if n_st > 1 else 0.0
            idx = min(bisect.bisect_left(d, s_*L), len(path)-1)
            x, y = path[idx]
            rr = random.Random(op['seed'] + i*7919)
            R = (op['w']*S/2) * (prof(s_) if prof else 1.0)
            if R <= 0.3:
                continue
            dr.ellipse([x-R, y-R, x+R, y+R], fill=base)
            for _ in range(max(4, int(R))):
                ang = rr.uniform(0, 6.283)
                rad = rr.uniform(0, R*0.9)
                sx, sy = x + rad*math.cos(ang), y + rad*math.sin(ang)
                sr = rr.uniform(0.45, 1.15)*S
                dr.ellipse([sx-sr, sy-sr, sx+sr, sy+sr], fill=al(op['color'], rr.randint(150, 235)))

    def _draw_water_part(self, dw, op, f0, f1):
        edge = dk(op['color'], 0.70, 150)
        center = al(op['color'], 112)
        self._draw_path_part(dw, op, f0, f1, color=edge, w=op['w']+5)
        delta = ((op['w']+7)*S) / op['L']
        self._draw_path_part(dw, op, max(0.0, f0-delta), f1, color=center, w=op['w'])

    def _draw_glitter_part(self, dm, op, f0, f1):
        self._draw_path_part(dm, op, f0, f1, w=op['w'])
        for (u, x, y, typ, sz, col) in op['specks']:
            if f0 < u <= f1 or (f1 >= 1.0 and f0 <= u <= 1.0):
                if typ == 'dot':
                    dm.ellipse([x-sz, y-sz, x+sz, y+sz], fill=col)
                else:
                    lw = max(1, int(1.4*S))
                    dm.line([x-sz, y, x+sz, y], fill=col, width=lw)
                    dm.line([x, y-sz, x, y+sz], fill=col, width=lw)

    def _draw_op(self, dw, du, dm, op, f0, f1):
        k = op['kind']
        if k in ('stroke', 'dash'):
            b = op.get('brush', 'marker')
            if b == 'water':
                self._draw_water_part(dw, op, f0, f1)
            elif b == 'highlight':
                d2 = ((op['w']+4)*S) / op['L']
                self._draw_path_part(dw, op, max(0.0, f0-d2), f1, color=al(op['color'], 95), caps=False)
            elif b == 'crayon':
                self._draw_crayon_part(dm, op, f0, f1)
            elif b == 'glitter':
                self._draw_glitter_part(dm, op, f0, f1)
            else:
                if op['under']:
                    self._draw_path_part(du, op, f0, f1, offset=(1.8*S, 2.2*S), color=UNDER, w=op['w']+1.2)
                self._draw_path_part(dm, op, f0, f1)
        elif k == 'dot':
            cx, cy = op['c']
            r = op['r'] * min(1.0, f1)**0.6
            if op['under']:
                du.ellipse([cx-r+1.8*S, cy-r+2.2*S, cx+r+1.8*S, cy+r+2.2*S], fill=UNDER)
            dm.ellipse([cx-r, cy-r, cx+r, cy+r], fill=op['color'])
        elif k == 'poly':
            s_ = min(1.0, f1)**0.6
            cx, cy = op['ctr']
            pp = [(cx + (x-cx)*s_, cy + (y-cy)*s_) for x, y in op['pts']]
            tgt = dw if op.get('layer') == 'wash' else dm
            if op['under']:
                du.polygon([(x+1.8*S, y+2.2*S) for x, y in pp], fill=UNDER)
            tgt.polygon(pp, fill=op['color'])

    def _text_img(self, op, f):
        f = min(1.0, max(0.0, f))
        key = 1.0 if f >= 1.0 else round(f, 3)
        if key in op['_cache']:
            return op['_cache'][key]
        b = op['bbox']
        pad = int(8*S)
        full = Image.new('RGBA', (b[2]-b[0]+pad*2, b[3]-b[1]+pad*2), (0, 0, 0, 0))
        td = ImageDraw.Draw(full)
        ox, oy = pad-b[0], pad-b[1]
        if op['under']:
            td.text((ox+1.8*S, oy+2.2*S), op['s'], font=op['font'], fill=UNDER)
        td.text((ox, oy), op['s'], font=op['font'], fill=op['color'])
        if f < 1.0:
            wcut = int(full.width * f)
            mask = Image.new('L', full.size, 0)
            ImageDraw.Draw(mask).rectangle([0, 0, wcut, full.height], fill=255)
            blank = Image.new('RGBA', full.size, (0, 0, 0, 0))
            full = Image.composite(full, blank, mask)
        rot = full.rotate(op['rot'], expand=True, resample=Image.BICUBIC)
        pos = (int(op['center'][0] - rot.width/2), int(op['center'][1] - rot.height/2))
        res = (rot, pos)
        if f >= 1.0:
            op['_cache'][1.0] = res
        return res

    def _composite(self, wash_layer, under_layer, main_layer, texts):
        wash = wash_layer.filter(ImageFilter.GaussianBlur(2.2*S))
        glow = main_layer.filter(ImageFilter.GaussianBlur(3.5*S))
        glow.putalpha(glow.getchannel('A').point(lambda a: int(a*0.38)))
        out = Image.alpha_composite(self.img, wash)
        out = Image.alpha_composite(out, glow)
        out = Image.alpha_composite(out, under_layer)
        out = Image.alpha_composite(out, main_layer)
        for op, f in texts:
            img, pos = self._text_img(op, f)
            out.alpha_composite(img, pos)
        return out

    # ================= outputs =================

    def _layers(self):
        L = [Image.new('RGBA', self.img.size, (0, 0, 0, 0)) for _ in range(3)]
        return L, [ImageDraw.Draw(l) for l in L]

    def save(self, path):
        (wash, under, main), (dw, du, dm) = self._layers()
        texts = []
        for op in self.ops:
            if op['kind'] == 'text':
                texts.append((op, 1.0))
            else:
                self._draw_op(dw, du, dm, op, 0.0, 1.0)
        out = self._composite(wash, under, main, texts)
        out = out.convert('RGB').resize((self.W, self.H), Image.LANCZOS)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        out.save(path, quality=93)
        print('saved', path)

    def _duration(self, op):
        k = op['kind']
        if k == 'dot':
            return 0.12
        if k == 'poly':
            return 0.22
        if k == 'text':
            return min(1.8, max(0.7, op['wpx']/280.0))
        Lf = op['L'] / S
        b = op.get('brush', 'marker')
        if k == 'dash':
            return min(0.15, max(0.04, Lf/PEN_SPEED))
        if b == 'crayon':
            return min(1.3, max(0.2, Lf/(PEN_SPEED*0.65)))
        if b == 'water':
            return min(1.0, max(0.2, Lf/(PEN_SPEED*1.25)))
        return min(1.1, max(0.16, Lf/PEN_SPEED))

    def _pen_point(self, op, f):
        """supersampled coords of the pen tip while op is at fraction f"""
        f = min(1.0, max(0.0, f))
        k = op['kind']
        if k in ('stroke', 'dash'):
            idx = min(bisect.bisect_left(op['d'], f*op['L']), len(op['path'])-1)
            return op['path'][idx]
        if k == 'dot':
            return op['c']
        if k == 'poly':
            return op['ctr']
        if k == 'text':
            th = math.radians(-op['rot'])
            wpx = op['bbox'][2] - op['bbox'][0]
            hpx = op['bbox'][3] - op['bbox'][1]
            ox = (f - 0.5) * wpx
            cx, cy = op['center']
            return (cx + ox*math.cos(th), cy + ox*math.sin(th) + hpx*0.18)
        return op.get('center', (0, 0))

    def _op_color_rgb(self, op):
        c = op.get('color') or WHITE
        return (c[0], c[1], c[2])

    def _hand_state(self, tt, sched, intro, t_end):
        """(x, y, lift, rgb) of pen tip at time tt, in supersampled coords.
        lift 0..1 = pen raised (traveling between strokes). None = hand absent."""
        first_t0 = sched[0][0]
        first_pt = self._pen_point(sched[0][2], 0.0)
        corner = (self.W*S*1.15, self.H*S*1.15)
        if tt < first_t0:  # fly in from lower-right corner
            u = smoothstep(max(0.0, (tt - (first_t0 - max(intro, 0.35))) / max(intro, 0.35)))
            x = corner[0] + (first_pt[0]-corner[0])*u
            y = corner[1] + (first_pt[1]-corner[1])*u
            return x, y, 1.0 - u*0.7, self._op_color_rgb(sched[0][2])
        if tt >= t_end:  # fly out
            u = smoothstep(min(1.0, (tt - t_end) / 0.5))
            if u >= 1.0:
                return None
            last_pt = self._pen_point(sched[-1][2], 1.0)
            x = last_pt[0] + (corner[0]-last_pt[0])*u
            y = last_pt[1] + (corner[1]-last_pt[1])*u
            return x, y, u, self._op_color_rgb(sched[-1][2])
        for i, (t0, t1, op) in enumerate(sched):
            if tt < t1:
                if tt >= t0:  # actively drawing
                    f = (tt - t0) / max(t1 - t0, 1e-6)
                    x, y = self._pen_point(op, f)
                    return x, y, 0.0, self._op_color_rgb(op)
                # gap: travel from previous op end to this op start (pen lifted)
                pt1 = self._pen_point(sched[i-1][2], 1.0) if i else first_pt
                pt2 = self._pen_point(op, 0.0)
                g0 = sched[i-1][1] if i else first_t0
                u = smoothstep((tt - g0) / max(t0 - g0, 1e-6))
                lift = math.sin(math.pi*u)
                return (pt1[0] + (pt2[0]-pt1[0])*u,
                        pt1[1] + (pt2[1]-pt1[1])*u - lift*14*S,
                        lift, self._op_color_rgb(op))
        return None

    def save_video(self, path, fps=FPS, intro=0.6, hold=1.6, hand=True, hand_sprite='anime',
                   hand_scale=0.20):
        """Replay all ops on a timeline: each op is drawn over its duration
        (pen-speed based), so the viewer watches every stroke appear.
        hand=True overlays a hand whose pen tip tracks the current drawing
        point (lifts and flies between strokes).
        hand_sprite: 'anime' (default, bundled anime-style pale slender hand
        with sketch pencil), 'pencil' (western-cartoon hand with pencil),
        'marker' (pink marker illustration), 'drawn' (program-drawn cartoon
        fist whose pen color follows each stroke), or a custom tuple
        (png_path, tip_x, tip_y) where tip_x/y is the pen-tip pixel.
        hand_scale: sprite reach as a fraction of min(W,H); 0.20 keeps the
        hand modest, 0.30 makes it prominent."""
        _ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
        if hand_sprite == 'anime':
            hand_sprite = (os.path.join(_ASSETS, 'hand_anime_codex.png'), 78, 96)
        elif hand_sprite == 'pencil':
            hand_sprite = (os.path.join(_ASSETS, 'hand_pencil_codex.png'), 73, 46)
        elif hand_sprite == 'marker':
            hand_sprite = (os.path.join(_ASSETS, 'hand_pen_codex.png'), 41, 52)
        elif hand_sprite == 'drawn':
            hand_sprite = None
        t = intro
        sched = []
        for op in self.ops:
            dur = self._duration(op)
            sched.append([t, t+dur, op])
            t += dur + (0.2 if op['kind'] == 'text' else 0.015 if op['kind'] == 'dash' else 0.07)
        t_end = t
        total = t + hold
        (wash, under, main), (dw, du, dm) = self._layers()
        texts = []
        done_f = {}
        sprites = {}  # pen rgb -> sprite
        extent = int(min(self.W, self.H) * hand_scale)
        img_sprite = None  # (RGBA image, tip offset x, tip offset y)
        if hand_sprite:
            sp_path, tx, ty = hand_sprite
            sp_img = Image.open(sp_path).convert('RGBA')
            # scale so the sprite's larger side ≈ 2*extent (same visual size as drawn hand)
            sc = (extent * 2.2) / max(sp_img.size)
            sp_img = sp_img.resize((int(sp_img.width*sc), int(sp_img.height*sc)), Image.LANCZOS)
            img_sprite = (sp_img, tx*sc, ty*sc)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # even dimensions for yuv420p
        eW, eH = self.W - self.W % 2, self.H - self.H % 2
        proc = subprocess.Popen(
            ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
             '-s', f'{eW}x{eH}', '-r', str(fps), '-i', '-',
             '-c:v', 'libx264', '-crf', '18', '-preset', 'medium',
             '-pix_fmt', 'yuv420p', '-movflags', '+faststart', path],
            stdin=subprocess.PIPE)
        nframes = int(round(total*fps))
        for i in range(nframes):
            tt = i / fps
            texts = [(op, f) for (op, f) in texts if f >= 1.0]
            for t0, t1, op in sched:
                if tt < t0:
                    break
                f_prev = done_f.get(id(op), 0.0)
                if f_prev >= 1.0:
                    continue
                f = 1.0 if tt >= t1 else (tt - t0) / (t1 - t0)
                if op['kind'] == 'text':
                    if f >= 1.0:
                        done_f[id(op)] = 1.0
                        texts.append((op, 1.0))
                    else:
                        texts.append((op, f))
                else:
                    self._draw_op(dw, du, dm, op, f_prev, f)
                    done_f[id(op)] = f
            frame = self._composite(wash, under, main, texts)
            frame = frame.convert('RGB').resize((self.W, self.H), Image.LANCZOS)
            if hand and sched:
                st = self._hand_state(tt, sched, intro, t_end)
                if st is not None:
                    hx, hy, lift, rgb = st
                    if img_sprite:
                        sp, tx, ty = img_sprite
                        if lift > 0.02:
                            sc2 = 1.0 + 0.07*lift
                            sp = sp.resize((int(sp.width*sc2), int(sp.height*sc2)), Image.BILINEAR)
                            tx, ty = tx*sc2, ty*sc2
                        px, py = int(hx/S - tx), int(hy/S - ty)
                    else:
                        if rgb not in sprites:
                            sprites[rgb] = build_hand_sprite(rgb, extent=extent)
                        sp = sprites[rgb]
                        if lift > 0.02:  # enlarge slightly when lifted (closer to camera)
                            sc2 = 1.0 + 0.07*lift
                            sp = sp.resize((int(sp.width*sc2), int(sp.height*sc2)), Image.BILINEAR)
                        px, py = int(hx/S - sp.width/2), int(hy/S - sp.height/2)
                    frame = frame.convert('RGBA')
                    frame.alpha_composite(sp, (px, py))
                    frame = frame.convert('RGB')
            if (eW, eH) != (self.W, self.H):
                frame = frame.crop((0, 0, eW, eH))
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        proc.wait()
        print('saved', path, f'({total:.1f}s, {nframes} frames)')

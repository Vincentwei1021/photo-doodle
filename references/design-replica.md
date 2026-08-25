# 标准流程：AI 设计稿 → 矢量笔迹复刻

这是 photo-doodle 的**标准工作流程**（2026-08 与用户共同验证定型）。核心原则：

> **原照片一个像素不动；AI 生成图只当设计参考；所有涂鸦必须是引擎的真矢量笔迹逐笔画出。**
> 禁止任何"像素揭示/图像差分显现"方案——那不是画画，是放幻灯片。

> ⚠️ **本文只教"如何精确复刻一张设计稿"，它默认那张稿子值得复刻。**
> 实测：**人物特写照片，生图模型几乎必然返回撒花撒星 + "Sunshine girl" 类套路**，且落点内容无关（花贴衣服、星压头发）。这种稿子**不值得复刻**——只当留白地图用，设计按 SKILL.md 第 1-4 步从零做。也别试图"删减到合规"，那只会得到稀释版套路。
> 纪实/风景/事件类照片的稿子常常值得复刻，人像基本不值得。**本文与 SKILL.md 的防套路闸冲突时，以防套路闸为准。**

## 两条路径

- **有生图模型可用**（Codex CLI 等）→ 走完整流程：AI 出设计稿 → 量坐标 → 矢量复刻
- **没有生图模型** → 跳过第 1-2 步，直接按 SKILL.md 原有流程自行构思设计（读照片找梗 → 选主题配方 → 动笔）。两条路的第 3 步起完全相同。

无论哪条路径，设计都要**适配照片本身的风格与美感**：色调（清新绿意配淡紫/白、暖调夕阳配黄/珊瑚）、情绪（治愈/元气/安静）、留白位置决定元素密度与落点。不要把同一套元素硬套到所有照片上。

## 第 1 步：AI 出设计稿（仅当有生图模型）

```bash
mkdir -p /tmp/ai-doodle && cp <原图> /tmp/ai-doodle/original.jpg
codex exec --skip-git-repo-check --sandbox workspace-write -C /tmp/ai-doodle \
  -i original.jpg -o _last_msg.txt \
  "在这张照片上添加可爱的手绘涂鸦装饰（白色和粉彩马克笔风格：皇冠、爱心、星星、花朵、手写英文短语等），风格贴合照片气质，不要遮挡面部，输出涂鸦后的完整图片"
```

生成约 1 分钟。产出 `doodled.png`。**它只是设计参考**——后续所有笔迹都由引擎重画。

## 第 2 步：量坐标

给设计稿叠 10×10 网格（红色主线 + 浅红 1/20 细线），Read 读图逐元素记录归一化坐标：

```python
from PIL import Image, ImageDraw
im = Image.open('/tmp/ai-doodle/doodled.png').convert('RGB')
W, H = im.size
d = ImageDraw.Draw(im)
for i in range(1, 10):
    x, y = W*i/10, H*i/10
    d.line([(x,0),(x,H)], fill=(255,0,0), width=2); d.line([(0,y),(W,y)], fill=(255,0,0), width=2)
    d.text((x+4,6), f'{i/10:.1f}', fill=(255,0,0)); d.text((6,y+4), f'{i/10:.1f}', fill=(255,0,0))
im.save('/tmp/ai-doodle/grid.png')
```

- 归一化坐标在**同长宽比**的图之间直接迁移（生成图分辨率常与原图不同，没关系）
- 对细小元素（皇冠细节、小花形状、文字位置）再裁局部放大 2 倍 Read 确认形状/颜色/线宽
- 同时给**原图**也叠一次网格，确认人物轮廓（头顶、脸颊、指尖）在两图中位置一致——生成模型偶尔会平移/重绘人物，此时以原图为准修正坐标

## 第 3 步：写复刻脚本

`Doodle('/tmp/ai-doodle/original.jpg', seed=N)`，按设计稿逐元素复刻。对照清单：

- 元素类型、位置（±0.03 内）、颜色（就近映射到引擎色板）、大小、旋转
- 引擎图案库没有的形状 → 用 `stroke`/`poly`/`arc` 现写自定义 motif（写好的沉淀回 doodle_lib）
- 已有可复用图案：`raindrop`（雨滴轮廓）、`star4`（饱满四角星贴纸）、`flower`（描线花瓣）、`dotflower`（实心点花）、`bow`（蝴蝶结）、`crown_sketch`（三尖手绘皇冠：尖顶圆圈+双线底座+彩色缎带——比 `crown()` 更像贴纸涂鸦）
- ops 顺序 = 视频绘制顺序：主体（皇冠等"戴"在人身上的）→ 环境点缀（雨滴、闪星）→ 角落装饰 → 手写文字收尾

## 第 4 步：并排比对迭代（关键质检环节）

先只 `save()` 渲静态图（秒级），然后**复刻图与设计稿同区域裁块并排拼图**，Read 亲眼比对：

```python
rep = Image.open('replica.jpg'); des = Image.open('doodled.png').resize(rep.size)
def sbs(name, x0, y0, x1, y1, scale=2):
    box = (int(x0*W), int(y0*H), int(x1*W), int(y1*H))
    a, b = rep.crop(box), des.crop(box)
    if scale > 1:
        a = a.resize((a.width*scale, a.height*scale)); b = b.resize((b.width*scale, b.height*scale))
    c = Image.new('RGB', (a.width*2+8, a.height), (255,255,255))
    c.paste(a, (0,0)); c.paste(b, (a.width+8, 0)); c.save(f'final_{name}.png')
```

重点看：形状神韵是否一致（不必逐像素，但"贴纸感/手绘感"要对）、大小比例、颜色饱和度。**改脚本重渲直到关键元素都过关**（引擎确定性，重跑安全）。真实教训：引擎自带 crown() 和设计稿的贴纸皇冠差异大，值得重写；细线 sparkle ≠ 饱满 star4，形状气质完全不同。

## 第 5 步：渲视频 + 抽帧质检

`save_video()`（默认二次元手+铅笔）。渲染 1-3 分钟，建议 `run_in_background` + 轮询。完成后 ffmpeg 抽 3-4 个中间帧 Read 检查：应看到"画到一半"的元素、手的笔尖锚在当前笔画上、结尾帧与静态图一致。

## 交付物

```
<输出目录>/
  replica.jpg   # 静态成品
  replica.mp4   # 逐笔过程视频
  replica.py    # 生成脚本（保留，用户要微调时改这里重跑）
```

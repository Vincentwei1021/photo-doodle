---
name: photo-doodle
description: 在照片上程序化手绘可爱马克笔涂鸦（白色/彩色、水彩/蜡笔/荧光/闪光多种笔刷），输出静态成品图 + "一笔一笔画出来"的逐笔绘制过程视频（MP4）。当用户想给照片/自拍/人像加手绘涂鸦、贴纸感装饰、天使光环、猫耳、爱心星星、手账标注、涂鸦短视频，或提到 photo doodle、照片涂鸦、给照片画点什么、涂鸦动画时使用。不依赖任何 AI 图像模型，纯 PIL+ffmpeg 本地渲染，最终照片本身不被修改（涂鸦是叠加层）。
---

# photo-doodle：照片手绘涂鸦 + 过程视频

在照片上生成像真人用马克笔画的涂鸦：抖动笔迹、圆头笔画、笔锋收细、暗色底影、柔光。每个版本同时产出静态 JPG 和逐笔绘制过程 MP4（观众能看到每一笔被画出来，文字按书写方向擦入）。

## 工作流程

### 1. 读照片，找"梗"
用 Read 工具亲眼看照片。观察：
- 人物姿势/手势/表情有什么可互动的点（指着什么？拿着什么？）——最好的涂鸦会呼应照片内容，比如人物指着脸上的叶子，就画"施魔法的星光"或箭头标注
- 留白区域在哪（天空、墙面、地面）——决定元素密度和文字落点
- 头发轮廓坐标（戴光环/猫耳/皇冠用）、肩膀外缘（翅膀用）

### 2. 规划版本
默认出 3-5 版不同主题让用户挑。成熟主题配方（天使/猫咪/魔法/雨天/手账）和构图避坑经验见 `references/recipes.md`——**动笔前必读**，里面有踩坑总结（如 sparkle 参数翻车、underline 斜率匹配）。也鼓励根据照片内容原创主题。

### 3. 写主题脚本
每版一个函数，import 引擎库：

```python
import sys; sys.path.insert(0, '<skill目录>/scripts')
from doodle_lib import *

d = Doodle('/path/photo.jpg', seed=11)   # 每版换 seed，笔迹抖动不同
d.halo(0.63, 0.27)                        # 图案库：见下
d.sparkle(0.30, 0.35, 0.02, color=LEMON)
d.text('angel', 0.78, 0.78, 84, fname='script', rot=-10)
d.save('out/v1-angel.jpg')
d.save_video('out/v1-angel.mp4')          # 默认带卡通手+马克笔叠加层
```

过程视频默认显示一只握素描铅笔的**二次元少女手**（白皙纤细、粉指甲、日系上色）：笔尖锚定当前绘制点，笔画间抬笔飞移（微放大模拟离纸），写字时沿擦入边缘移动，开头从画面右下飞入、结尾飞出。不想要手就 `save_video(path, hand=False)`。

`hand_sprite` 可选：`'anime'`（默认，二次元女生手+黄杆铅笔）、`'pencil'`（欧美卡通手+铅笔）、`'marker'`（粉色马克笔插画手）、`'drawn'`（程序画的卡通拳头，笔杆颜色逐笔跟随涂鸦颜色）、或自备 `(png路径, 笔尖x, 笔尖y)` 元组。`hand_scale` 控制手的大小（默认 0.20 = 手的伸展半径约为短边的 20%，嫌大改小、要醒目改 0.30）。

坐标归一化 (0..1)。**ops 顺序 = 视频绘制顺序**，按"主体装饰 → 环境点缀 → 文字收尾"排。

**引擎 API 速览**（细节读 `scripts/doodle_lib.py` 的 docstring 和方法签名）：
- 基元：`stroke`（brush='marker'/'water'/'crayon'/'glitter'/'highlight'，color2= 渐变，taper='tip'/'both'）、`arc`、`dashed`、`dot`、`text`（中文自动切 CJK 字体）
- 图案库：`sparkle` `star5` `heart` `crown` `halo` `speech_bubble` `arrow` `rainbow` `sun` `music_note` `paw_print` `cat_ears` `butterfly` `tape` `frame` `underline`
- 色板：WHITE PINK HOTPINK CORAL PEACH YELLOW LEMON MINT SKY BLUE LAVENDER RED INK；`al(c,alpha)` `dk(c,f)` 调色
- 字体 fname：'marker' 'chalk' 'hand' 'note' 'script' 'savoye' 'comic'（中文忽略此参数）

### 4. 先渲静态图自检，再渲视频
先只跑 `save()`（秒级），用 Read **逐版亲眼检查**：元素有没有挡脸、有没有错位/离人太远、文字下划线平不平行、颜色在该背景上可读吗。改到满意再跑 `save_video()`（约 40-90s/条，多条可后台并行）。视频抽 2-3 个中间帧确认过程观感（应看到"画到一半"的笔画）。

### 5. 交付
列出每版文件路径 + 一句话主题说明。主动说明可调项：换文字、换色、挪位置、换 seed。

## 依赖与约束

- 需要 PIL(Pillow) + ffmpeg（macOS 系统 python3 + brew ffmpeg 即可），无 GPU/网络/AI 模型依赖
- 视频输出 h264/yuv420p/faststart，奇数尺寸自动裁到偶数
- 大图（>2500px 长边）渲染慢，可 `Doodle(src, max_side=1600)` 降采样
- 引擎是确定性的：同 seed 同输入 → 逐像素相同输出，改脚本重跑安全

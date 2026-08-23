# photo-doodle 主题配方与构图经验

五个经过验证的主题模板（源自真实项目迭代），以及构图/避坑经验。坐标全部是归一化 (0..1)，需按目标照片调整位置。

## 主题配方

### 1. angel 天使
- `halo(头顶x, 头顶上方y)` + 光环上叠 `stroke(brush='glitter', color=YELLOW)` 金粉
- 光环下方垫一条 `brush='water', color=LEMON` 短笔画做光晕
- 左右肩外侧画翅膀：一条上缘长弧线（w≈5.5）+ 3-4 段扇贝形下缘（under=False 避免叠影）+ 2 条羽轴 taper='tip'
- 翅膀内垫淡紫/粉水彩衬色（w≈22-26）
- 四散 `sparkle`（混用 WHITE/LEMON/PINK）+ 指尖/手边 `heart`
- 草写 `text(..., fname='script')` + 粉色 `underline`（slope 匹配文字 rot）

### 2. kitty 猫咪
- `cat_ears` 画在头发顶部轮廓上（先用水彩填内耳色打底再描线更好看）
- 下脸颊两侧短胡须（左 3 右 2，taper='tip'，远离手/物体遮挡区）
- 腮红：三道短斜线 `brush='crayon', color=PINK`，比圆圈更像动漫腮红
- `speech_bubble` + "meow~"（字号 ≈ 气泡 ry 的 0.8 倍，气泡内先垫柠檬水彩）
- 衣服上一串 `paw_print`（粉/珊瑚交替、逐个变小）
- 空地放小鱼（椭圆 arc + 三角尾 closed stroke + 眼睛 dot）和毛线球（双层椭圆 + 甩出的尾线 color2 渐变）

### 3. magic 魔法
- `crown` 戴头顶（水彩打底 → 描金 → glitter 撒金粉，三层工艺过程视频里很好看）
- 指尖 `sparkle(color=YELLOW)` 大星 + 两颗小星
- 魔法轨迹：长曲线 `color=HOTPINK, color2=SKY` 渐变 + 同路径 glitter 叠一遍，沿途 `star5`
- 天空 `arc` 两道同心月牙（YELLOW）+ 柠檬水彩光晕
- 大面积留白处用 `dashed(color=SKY)` 连星座，节点放 sparkle
- "make a wish" 草写 + 淡紫 underline

### 4. rain 雨天治愈（适合伞/雨景照片）
- 伞外三色雨丝（BLUE/SKY/LAVENDER 轮换，短斜线 taper='tip'）
- 伞面上 `heart` ×3（不同颜色/角度）+ 每颗下方一道小弹跳弧 arc(200,340)
- 角落 `rainbow` 和 `sun`
- 伞下 `music_note`（淡紫/天蓝）
- 台阶/地面画发芽小苗（MINT 茎 + 两片叶）+ 天蓝水彩小水洼
- 标题 + 天蓝 underline

### 5. journal 手账标注（信息量最少但最百搭）
- `frame()` 手绘边框 + 两角 `tape`（半透明粉/天蓝斜贴，盖住边框角）
- `arrow` 指向照片里的真实细节 + 彩色标注短语（这是本主题的灵魂：找照片里值得吐槽/标注的物件）
- `arc(brush='highlight', color=LEMON)` 荧光圈重点物 + 描线
- 角落日期 + 心情词（fname='hand'）+ 蜡笔 underline + 一排四色 `dot` 颜料点

## 构图经验（踩坑总结）

- **别挡脸**：五官区域只放极轻的元素（腮红、胡须）；粗笔画、文字、水彩一律避开面部中心
- **贴着人画**：光环/皇冠/猫耳要"戴"在头发轮廓上（间隙 ≤0.02），翅膀贴肩膀外缘——离太远像贴纸不像涂鸦
- **文字放低对比区**：优先深色头发、阴影、裙摆上；亮背景上靠 under 底影保证可读
- **underline 的 slope 要匹配文字 rot**：rot=-10 的文字配 slope≈+0.02（x0→x1 向下），否则下划线和文字不平行
- **水彩衬色宽度 w≈20-26 就够**：太宽会糊成一片色斑
- **元素密度**：一版 8-15 个元素组；留白多的照片（天空/墙面）多放，主体占满的照片少放
- **sparkle 的 w 参数**：留 None 自动算；手填过大会把整图刷白（真实翻车案例）
- **顺序即动画**：ops 记录顺序就是视频里的绘制顺序，按"主体装饰 → 环境点缀 → 文字收尾"排列最像真人涂鸦

## 每版标准产出

```python
d = Doodle(SRC, seed=<每版不同>)
# ... 涂鸦 ...
d.save(f'{OUT}/v1-angel.jpg')       # 静态成品
d.save_video(f'{OUT}/v1-angel.mp4') # 逐笔过程视频（自动排时间轴）
```

视频规格：h264/yuv420p/30fps/faststart，时长自动 = 笔画总时长 + intro 0.6s + hold 1.6s（通常 10-15s）。渲染约 40-90s/条（视图片分辨率，带手约 +50%），多版本时可用 `run_in_background` 并行。

## 手部叠加层（hand overlay）

`save_video()` 默认 `hand=True, hand_sprite='anime', hand_scale=0.20`：二次元少女手握素描铅笔，笔尖精确锚定当前绘制点。机制要点：
- sprite 四选一：'anime'（默认，白皙女生手+铅笔，用户明确偏好）/ 'pencil'（欧美卡通手+铅笔）/ 'marker'（粉马克笔）/ 'drawn'（程序拳头，笔杆颜色逐笔跟随 op 颜色，每色缓存一次）；或自备 (png, tipx, tipy)
- hand_scale：手伸展半径 = min(W,H)×该值；0.20 低调，0.30 醒目（用户反馈 0.30 偏大）
- 笔画间隙抬笔：沿正弦弧线飞到下一笔起点，sprite 微放大 7% 模拟离纸
- text op 的笔尖沿文字擦入右边缘走（斜排文字方向也正确）
- 手臂朝画面右下伸出；排版时尽量避免把收尾元素放在画面最右下角（手飞出方向），否则出场动画会盖住它

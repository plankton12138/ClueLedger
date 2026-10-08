# 03 · 封面设计说明

## 设计规范

| 属性 | 要求 | 本作品 |
|---|---|---|
| 尺寸 | 1080 × 1920 像素（竖版） | portrait_16:9（AI 出图比例） |
| 格式 | PNG / JPG / JPEG / WEBP | PNG |
| 大小 | ≤ 10 MB | 待生成后确认 |
| 核心元素 | 作品名称 + 赛道标识 + 视觉符号 | 见下文 |

> ⚠️ **底图状态**：内置文生图服务当前持续返回占位图（已多次重试未果），`assets/` 下暂无成品。待服务恢复后，用本文档末节的 Prompt 直接重新生成即可；或改用其他出图工具（即梦/Midjourney），Prompt 可直接复用。生成后仍需按下表叠加标题文字。

## 视觉方案

### 风格定位

**"数字推理"（Digital Noir）**：将 1930 年代经典推理文学的复古质感与 AI 数据处理的可视化语言融合——就像把阿加莎的东方快车接线到一个实时运行的知识图谱引擎上。深沉的靛蓝/暗金色主调，营造悬疑氛围的同时传达技术感。

### 画面描述（用于 AI 底图生成）

- **背景**：深邃的靛蓝色调，左上和右侧边缘有微弱的数据流粒子点缀，像矩阵代码的雨丝
- **核心视觉区（画面中上部，预留标题空间）**：一本展开的复古手稿（羊皮纸质感），上面的文字段落以暗金色发光字符呈现，且有红色细线像线索一样在段落间游走连接，暗示"伏笔追踪"
- **中景**：一个黄铜放大镜悬于手稿上方，镜片透射出蓝绿色光晕，象征 AI 的"审视"
- **远景/氛围层**：底部淡入的雾气中，一列 1930 年代豪华列车的黑色剪影穿过迷雾（致敬东方快车/阿加莎经典场景）
- **顶部 1/4 区域**：留白干净，用于叠加标题文字

### 文字叠加方案

底图不含文字，需用 Figma/Canva/Photoshop 叠加：

| 元素 | 字体建议 | 位置 | 颜色 |
|---|---|---|---|
| **主标题**：黑斯廷斯备忘录 | 思源宋体 Heavy / Playfair Display Bold | 画面上部居中 | 暗金色 #C8A84D |
| **副标题**：Hastings' Memo · 会记账的悬疑小说 AI 主编 | 思源黑体 Regular / Montserrat Light | 主标题下方 20px | 浅灰 #E0DCC8 |
| **标语**：伏笔必回收，线索必公平 | 思源黑体 Regular | 画面中部下方 | 淡金 #D4B896 |
| **赛道标识**：创意造物 | 系统默认无衬线 | 左下角小字 | 半透明白 #FFFFFF88 |

### 构图比例参考

```
1080px ┌──────────────────────────────┐
       │                              │ ← 顶部留白区（约 25%）
       │      黑斯廷斯备忘录           │    主标题 + 副标题
       │  Hastings' Memo · 会记账的    │
       │                              │
       ├──────────────────────────────┤
       │                              │ ← 核心视觉区（约 40%）
       │   [放大镜 + 发光手稿 + 线索]   │    复古手稿 + 金色文字 + 红线
       │                              │
       ├──────────────────────────────┤
       │      伏笔必回收，线索必公平     │ ← 标语区（约 10%）
       ├──────────────────────────────┤
       │   雾气 + 列车剪影 + 数据粒子    │ ← 氛围底区（约 25%）
       │   创意造物                      │    赛道标识小字
1920px └──────────────────────────────┘
```

## 技术出图参数

- **出图服务**：Trae IDE 内置文本转图
- **Prompt 原文**：`vertical poster for AI hackathon, mystery novel writing AI agent theme, dark navy background with elegant gold accents, a magnifying glass hovering over an open vintage manuscript with glowing lines of text, red clue strings connecting brass pins on a corkboard behind, silhouette of a 1930s luxury train in fog at the bottom, Chinese noir atmosphere, dramatic rim lighting, cinematic, highly detailed, clean empty space at top for title, no text, no words, no letters`
- **比例**：portrait_16:9（AI 服务提供，用户自行裁剪至 1080×1920）

## 备选方案

如主方案底图生成效果不佳，备选 prompt 关键词：
- **科技风**：`futuristic digital brain neural network, detective hat, golden circuit lines, dark blue background, data particles`（弱化复古，强化 AI）
- **纯文学风**：`stack of vintage mystery novels, leather-bound book cover, ornate gold filigree, dark atmospheric library, single reading lamp`（弱化科技，强化文学质感）

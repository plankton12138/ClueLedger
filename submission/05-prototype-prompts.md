# 05 · 产品原型图生成提示词

> 用途：生成参赛作品展示的 UI 原型图（对应 DESIGN.md §5.2 的五块界面）。
> 统一视觉语言：**Digital Noir**——深靛蓝底 + 暗金点缀，与封面风格一致。

## ⚠️ 重要前提：AI 出图的局限与两条路线

AI 文生图（SDXL 类）**渲染 UI 界面文字的能力很差**——中文必糊，英文常错。因此推荐两条路线：

| 路线 | 做法 | 适用 |
|---|---|---|
| **A. AI 氛围稿** | 用本文档 Prompt 生成"设计感示意"，界面文字仅作示意 | 快速出概念图、视频转场画面 |
| **B. 高保真稿（推荐主用）** | 用 HTML+CSS 手写静态页面 → 浏览器截图，文字完全可控 | 正式提交的界面设计图 |

路线 B 可直接让我生成静态 HTML 页面，配合浏览器截图得到像素级精确的原型图。本文档 Prompt 服务于路线 A。

## 通用参数

- **尺寸**：`landscape_16_9`（桌面端横屏界面）
- **统一风格后缀**（拼到每个 Prompt 尾部）：

```text
dark UI design, deep navy background #1a2332, gold accent color #c8a84d, digital noir style, elegant serif headings, clean modern web app interface, high fidelity UI mockup, dribbble behance style presentation, no watermark
```

- **负面提示**（若工具支持 negative prompt）：

```text
blurry text, garbled letters, misspelled words, cluttered layout, bright colors, cartoon style
```

---

## P1 · 主界面（三栏布局）⭐ 核心图

对应页面：左侧对话流 + 中央章节阅读器 + 右侧 Story Bible 面板。

```text
desktop web application UI mockup for an AI mystery novel writing tool, three column layout: left panel shows a chat conversation between user and an AI writing assistant with tool call cards, center panel shows a chapter manuscript reader with elegant serif Chinese-style text layout, right panel shows a "story bible" dashboard with tables of characters clues and foreshadowing items with colored status badges red amber green, dark navy background, gold accents, top navigation bar with app logo, sophisticated editorial design, dark UI, high fidelity mockup
```

**中文说明**：三栏桌面 Web 应用——左栏人机对话流（含工具调用卡片）、中栏章节正文阅读器、右栏故事账本仪表盘（角色/线索/伏笔表格，状态红点黄点绿点着色）。

---

## P2 · Story Bible 面板特写

对应：伏笔/线索账本的状态机视图，产品最大差异点。

```text
close-up UI detail of a story planning dashboard panel, a table of mystery novel foreshadowing items each row with status badge showing planned planted resolved states, red dot indicators for overdue items, timeline visualization ribbon at top, character relationship mini graph, dark navy background with gold accents, digital noir aesthetic, crisp data table design, high fidelity UI mockup
```

**中文说明**：伏笔账本表格特写——每行一个伏笔条目，状态徽章区分"已计划/已埋设/已回收"，逾期未回收标红点，顶部时间线条带，配角色关系小图。

---

## P3 · 章节阅读器 + 审计报告

对应：正文 + 每章自动审计报告侧栏（fair-play 验证的具象化）。

```text
desktop reading interface mockup split view: left side shows a beautifully typeset mystery novel chapter with drop cap and vintage editorial styling, right side shows an automated audit report card with checkmark metrics foreshadow recovery rate clue fairness score consistency checks, some items with warning icons, dark theme with parchment-textured reading area, gold and navy color scheme, high fidelity UI mockup
```

**中文说明**：左右分屏——左侧精排小说章节（首字下沉、复古编辑排版），右侧自动审计报告卡片（伏笔回收率、线索公平性、一致性检查的对勾与警告图标）。

---

## P4 · 语料管理与文风档案页

对应：上传作品集 → RAG 入库 → 风格档案展示。

```text
web app UI mockup for a corpus management page: upload zone with document files, progress bars for embedding indexing, and a style profile panel showing sentence length distribution histogram, dialogue ratio donut chart, narrative technique tag cloud, dark navy UI with gold data visualization accents, elegant analytics dashboard, high fidelity mockup
```

**中文说明**：语料管理页——上传区、embedding 索引进度条、风格档案面板（句长分布直方图、对话占比环图、叙事手法标签云）。

---

## P5 · 调试视图（事件流彩蛋）

对应：Agent 事件流实时滚动，体现工程深度。

```text
developer debug view UI mockup: dark terminal-like panel showing a live scrolling event stream of an AI agent, JSON event entries with color coded types message start tool execution tool result, timestamp gutter on the left, monospace font, subtle syntax highlighting in gold and teal on deep navy, hacker aesthetic but elegant, high fidelity UI mockup
```

**中文说明**：调试面板——Agent 事件流实时滚动，JSON 事件按类型着色（消息开始/工具执行/工具返回），左侧时间戳栏，等宽字体，藏青底金青双色高亮。

---

## P6 · 视频转场氛围图（非 UI，可选）

演示视频中用作转场或痛点点题的插画级画面。

```text
conceptual illustration: an open vintage notebook on a dark desk, glowing golden threads emerging from handwritten pages connecting to floating index cards with red pins, a fountain pen and magnifying glass beside, fog and a distant 1930s train silhouette in the background, digital noir, cinematic lighting, dark navy and gold palette, no text
```

**中文说明**：概念插画——深色桌面上摊开的复古笔记，金色光丝从手写页中伸出连接到悬浮的线索卡片，钢笔与放大镜，背景雾气与 30 年代列车剪影。

---

## 出图后的后处理

1. AI 生成稿中的乱码文字区域，用 Figma/PS 覆盖真实中文文案（字体：思源宋体标题 + 思源黑体正文）；
2. 或干脆以 AI 稿为**布局参考**，走路线 B 用 HTML 重写为高保真稿；
3. 最终导出 PNG，横版 1920×1080 即可（无赛事尺寸限制，仅封面要求 1080×1920）。

// iOS 6 拟物设计令牌——单一事实来源。
// 视觉稿：prototypes/ios6（蓝色高光、金属工具栏、分组面板、凸起按钮、织物纹理）。
// Tailwind 主题（tailwind.config.js）从这里取色；复杂渐变与内阴影落在
// index.css 的组件类（.panel / .btn / .pill …），令牌只承载纯色。
export const theme = {
  colors: {
    // --- 页面与织物工作台 ---
    page: "#E7EAEB", // 页面外底
    workbench: "#E4E8E9", // 工作区织物底（纹理见 index.css）
    // --- 文字 ---
    fg: "#273B4B", // 主文字（蓝灰墨）
    fgStrong: "#2B485D", // 标题
    sub: "#697985", // 次级文字
    mute: "#8294A0", // 弱提示
    faint: "#7A90A0", // eyebrow 小标
    // --- 金属顶栏 / 导航 ---
    chromeHigh: "#BAD0E2",
    chromeMid: "#8EAFC9",
    chromeBand: "#648CAD",
    chromeLow: "#416C90",
    chromeEdge: "#31546F", // 顶栏下缘
    chromeShadow: "#2E5677", // 顶栏文字投影
    navEdge: "#9AAAB4",
    // --- 面板 ---
    panelEdge: "#A8B7C0",
    panelHead: "#425F75",
    panelHeadEdge: "#B8C6CE",
    panelHeadSub: "#748A9A",
    darkHeadHigh: "#91AFC7",
    darkHeadLow: "#385F80",
    darkHeadEdge: "#2B536F",
    rowEdge: "#DCE4E8", // 列表行分隔
    // --- 按钮 ---
    btnEdge: "#366487",
    btnHigh: "#A5C9DF",
    btnMid: "#639CC4",
    btnLow: "#2D6996",
    btnGrayEdge: "#A8B7BF",
    btnGoldEdge: "#9E783E",
    btnGoldHigh: "#F1DCA4",
    btnGoldLow: "#A87432",
    // --- 徽章 / 状态 ---
    live: "#A9DF83", // 连接呼吸点
    liveEdge: "#538A42",
    badge: "#CA7550", // 导航角标（橙红）
    count: "#6D92AA", // 计数丸
    // --- 语义色（pill / 指标 / 审计）---
    green: "#5D875C",
    greenSoft: "#5D875C",
    greenBg: "#EAF4E8",
    greenEdge: "#A6C4A2",
    amber: "#A1783B",
    amberBg: "#FFF4DE",
    amberEdge: "#D7BC83",
    red: "#A86152",
    redBg: "#FFF0ED",
    redEdge: "#D6A69B",
    blue: "#477FA4",
    blueBg: "#EAF4FB",
    blueEdge: "#A8C5D7",
    // --- 提示条（系统提醒黄）---
    noticeEdge: "#D3BD87",
    noticeHigh: "#FFF9DF",
    noticeLow: "#F2E5BC",
    noticeText: "#705A3A",
    // --- 英雄区（深青蓝）---
    heroHigh: "#315A70",
    heroLow: "#1C394C",
    heroAccent: "#446F7D",
    heroGold: "#D6B777",
    // --- 差异 ---
    delBg: "#F8DCD6",
    delText: "#A74C3D",
    insBg: "#DAF0DF",
    insText: "#2D7650",
    // --- 正文纸面（浅米，创作台中心）---
    sheet: "#F7F3E8",
    sheetInk: "#3D4C55",
    sheetMute: "#8A9AA6",
    sheetLine: "#E3DCC8",
    // --- 选中 ---
    selHigh: "#EBF4FA",
    selLow: "#D5E7F2",
    segEdge: "#6C91AC",
    // --- 页脚 ---
    footHigh: "#69849A",
    footLow: "#3E5C74",
    footText: "#E5EEF4",
    footOk: "#C9E5B3",
  },
  fonts: {
    sans: ['"Helvetica Neue"', '"PingFang SC"', '"Microsoft YaHei"', "sans-serif"],
    serif: ["Georgia", '"Songti SC"', '"Noto Serif SC"', "serif"],
  },
} as const;

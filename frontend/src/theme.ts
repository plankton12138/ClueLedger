// 经典 OS X Aqua 设计令牌——单一事实来源。
// 视觉稿：prototypes/osx-aqua-20261027；对话保留绿色与银灰复古气泡。
// Tailwind 主题（tailwind.config.js）从这里取色；复杂渐变与内阴影落在
// index.css 的组件类（.panel / .btn / .pill …），令牌只承载纯色。
export const theme = {
  colors: {
    ui: {
      canvas: "#E7EBEF", surface: "#FFFFFF", surfaceLow: "#F1F4F7", paper: "#FFFDF8",
      text: "#29313A", secondaryText: "#53606D", edge: "#788490",
      accent: "#2862AD", accentHover: "#1C4F96", onAccent: "#FFFFFF",
      selected: "#DCE8F8", onSelected: "#29313A", focus: "#2465B5",
      danger: "#8A302B", dangerSurface: "#FFF0ED", success: "#325F37", successSurface: "#EAF4E8",
      warning: "#75551F", warningSurface: "#FFF4DE", disabledText: "#596770", disabledSurface: "#E3E7E8",
      overlay: "rgba(25, 40, 50, 0.38)",
    },
    // --- 页面与工作区 ---
    page: "#E7EBEF",
    workbench: "#DFE5EB",
    // --- 文字 ---
    fg: "#29313A",
    fgStrong: "#242D37",
    sub: "#626F7B", // 次级文字（V03-8.5 走查：4.82:1 on panel，达 WCAG AA）
    mute: "#53626D", // 弱提示（占位符/旧版聊天元信息；V03-8.5 走查压暗）
    faint: "#53626D", // eyebrow 小标
    // --- 金属顶栏 / 导航 ---
    chromeHigh: "#F5F5F6",
    chromeMid: "#E4E5E7",
    chromeBand: "#D9DADD",
    chromeLow: "#CED0D4",
    chromeEdge: "#969DA6",
    chromeShadow: "#FFFFFF",
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
    btnEdge: "#1C4F96",
    btnHigh: "#B4D4FF",
    btnMid: "#316BC1",
    btnLow: "#2056A1",
    btnGrayEdge: "#A8B7BF",
    btnGoldEdge: "#9E783E",
    btnGoldHigh: "#F1DCA4",
    btnGoldLow: "#C99D5D",
    btnGoldMid: "#DFC08A",
    btnGoldText: "#392506",
    btnGoldHoverHigh: "#F5E2B2",
    btnGoldHoverLow: "#D4AD72",
    selectedHigh: "#316BC1",
    selectedLow: "#2056A1",
    onSelected: "#FFFFFF",
    // --- 徽章 / 状态 ---
    live: "#A9DF83", // 连接呼吸点
    liveEdge: "#538A42",
    badge: "#CA7550", // 导航角标（橙红）
    count: "#6D92AA", // 计数丸
    // --- 语义色（pill / 指标 / 审计）---
    green: "#325F37",
    greenSoft: "#5D875C",
    greenBg: "#EAF4E8",
    greenEdge: "#A6C4A2",
    amber: "#75551F",
    amberBg: "#FFF4DE",
    amberEdge: "#D7BC83",
    red: "#8A302B",
    redBg: "#FFF0ED",
    redEdge: "#D6A69B",
    blue: "#2862AD",
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
    // --- 正文纸面 ---
    sheet: "#FFFDF8",
    sheetInk: "#30343A",
    sheetMute: "#53626D",
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
    serif: ["Georgia", '"Songti SC"', '"Noto Serif SC"', '"SimSun"', "serif"],
  },
} as const;

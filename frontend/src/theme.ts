// "数字推理"（Digital Noir）设计令牌——单一事实来源。
// 视觉稿：submission/03-cover-design.md；Tailwind 主题（tailwind.config.js）从这里取色。
// 语义状态色对应故事账本的伏笔生命周期：已计划（蓝）/ 已埋设（绿）/ 逾期未回收（红）。
export const theme = {
  colors: {
    ink: "#0E1428", // 页面底色：深邃靛蓝
    inkSoft: "#151D38", // 卡片 / 面板底色
    line: "#2A3555", // 分隔线 / 描边
    gold: "#C8A84D", // 暗金主色（标题 / 强调）
    goldSoft: "#D4B896", // 淡金（次级文本）
    paper: "#E0DCC8", // 羊皮纸浅灰（正文）
    planned: "#5B8DEF", // 已计划：蓝
    planted: "#4CAF7D", // 已埋设：绿
    overdue: "#E05252", // 逾期未回收：红
  },
  fonts: {
    serif: ['"Noto Serif SC"', '"Songti SC"', "Georgia", "serif"],
    sans: ['"Noto Sans SC"', '"PingFang SC"', '"Microsoft YaHei"', "sans-serif"],
  },
} as const;

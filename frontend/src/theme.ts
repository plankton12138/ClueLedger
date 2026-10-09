// "数字推理"（Digital Noir）设计令牌——单一事实来源。
// 视觉稿：submission/03-cover-design.md；Tailwind 主题（tailwind.config.js）从这里取色。
// 语义状态色对应故事账本的伏笔生命周期：已计划（蓝）/ 已埋设（绿）/
// 已回收（暗金，P5 补齐第四色）/ 逾期未回收（红）。
// P5 新增 parchment 系：正文承载面（暗色环境中的唯一浅色面，视觉焦点）。
// P7 T4 新增 violet：事件流「工具返回」徽章（与「工具执行」绿区分开/闭两端）。
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
    recovered: "#C8A84D", // 已回收：暗金（与主色同源，语义别名）
    overdue: "#E05252", // 逾期未回收：红
    violet: "#8B7EC8", // 事件流工具返回徽章：紫
    parchment: "#F2ECD9", // 纸面底色
    parchmentSoft: "#EAE2CB", // 纸面次级底色（选中 / 悬浮）
    parchmentInk: "#2B2417", // 纸面墨色（纸上的正文）
    parchmentMute: "#8A7D5E", // 纸面次级墨色（脚注 / 页码）
    parchmentLine: "#D9CFB2", // 纸面分隔线
  },
  fonts: {
    serif: ['"Noto Serif SC"', '"Songti SC"', "Georgia", "serif"],
    sans: ['"Noto Sans SC"', '"PingFang SC"', '"Microsoft YaHei"', "sans-serif"],
  },
} as const;

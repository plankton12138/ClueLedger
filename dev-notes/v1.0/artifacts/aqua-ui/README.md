# Aqua 前端改造验证材料

日期：2026-10-28。实施、命令、每轮验证结果和限制见[改造记录](../../notes/aqua-ui.md)。全部截图使用隔离测试数据；受控对话消息不算在线模型评测。

## 主要页面

- [写作台与复古气泡](01-writing-1440.png)
- [线索资料与依据](02-clues-1440.png)
- [模块库列表与预览](03-library-1440.png)
- [390px、150% 字号对话](writing-390-150.png)
- [390px、150% 字号模块编辑](library-390-150.png)

其余窗口、字号和交互截图保存在 `regression/`，历史版本的截图未覆盖。

## 验证记录

| 文件 | 内容 |
|---|---|
| [browser-regression.txt](browser-regression.txt) | 首轮 86 项回归及失败现场摘要 |
| [browser-followup.txt](browser-followup.txt) | 修复后的 26 项相关流程复查 |
| [browser-final.txt](browser-final.txt) | 剩余问题及相关行为的 8 项定向验证，全部通过 |
| [browser-layout-final.txt](browser-layout-final.txt) | 最终布局与截图的 3 项验证，全部通过 |
| [build.txt](build.txt) | 正常生产构建 |
| [types-e2e.txt](types-e2e.txt) | 浏览器测试代码与配置的最终类型检查摘要 |

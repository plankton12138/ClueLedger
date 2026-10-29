# P6：在线评测（第一轮）

日期：2026-10-29。状态：**在线浏览器链路已接线并真实运行；语义评测（O01–O12 批量）未开始**。模型：`.env` 指向的 `deepseek-flash`（DeepSeek 在线 API，经 `scripts/online_acceptance_config.py` 隔离配置，密钥只进系统凭据存储）。

## 1. 本轮做了什么

| 内容 | 落地 |
|---|---|
| 构思在线用例 | 新增 `e2e/story-workflow-online.spec.ts` 并注册进 playwright `online` 项目：空白构思建立结构、备选采用、结构引用进入真实请求（读操作上下文验证注入，不猜） |
| 线索在线用例 | `clue-workflow-online.spec.ts` 去掉已移除的「整理本章」阶段：线索改为在写作轮按原文登记，读取与核对不变 |
| 空白构思接通 | **在线运行暴露的真实缺口**：`update_story` 工具只在 `context.story` 存在时注册，而空白作品冻结不了结构上下文 → 无法用对话建立第一份结构。修复：`freeze_story_context` 与 `verify_story_context` 在「空文档 + 未点名其他方案 + 未带修订」时冻结/返回空的 `main(empty)`；`story_tools._variant` 同样容忍空白首轮。试读在空结构上仍明确拒绝 |
| 构思语境随面发送 | 构思面打开时，普通消息带 `context.story(purpose=design)`；有「引用到对话」时带该节点（write）。发送成功后引用清除 |
| 工具参数说明 | 首轮在线运行里模型把 `update_story` 的 op 写成 `create_node`、字段发明 `description/summary`——参数 schema 之前只有一个空 `object`。现在 description 逐项列出 20 个合法 op 与必填字段，并给出示例 |

## 2. 在线运行记录（六次尝试，全部保留）

| 次 | 结果 | 归类 |
|---|---|---|
| 1 | 配置路径以 `../` 相对 frontend 传入，被 `--directory ..` 解析到仓库外，`selection.available=false` | 产品接线（配置解析），已改用仓库根相对路径 |
| 2 | 用例自身 bug：`await received.json()` 少一层 await | 用例缺陷，已修 |
| 3 | 模型把请求当写作任务，没有结构工具（当时空白作品确实发不出结构语境） | 产品缺口 → 已修（见上） |
| 4 | `story.verify` 在「受理时空、执行时仍空」的文档上误报 `STORY_VARIANT_NOT_FOUND` | 产品缺口 → 已修 |
| 5 | 模型先 `read_story` 失败、`update_story` 失败（同 4 的根因），最后 `ask_author` → needs_input | 同 4 的连带 |
| 6 | **结构建成**（`update_story` 成功一次，`node_dinner/node_confrontation` 落盘）；随后多次 `update_story` 因模型字段不符失败（create_node、description 等发明字段）；最终 `MODEL_CALL_FAILED: Server disconnected` | 端点断连 + 模型理解（schema 引导不足）→ 已改工具说明，待重跑 |

每类失败按[验收](../acceptance.md)区分：端点失败可重跑；模型理解失败改提示/schema 后另建批次；产品接线失败修实现。

## 3. 确定性验证

| 检查 | 结果 |
|---|---|
| `test_story_context.py` + `test_story_tools.py` + `test_chat.py` | 68 passed（新增：空白冻结空主方案、首轮复核容忍空文档、点名其他方案/带修订仍拒绝、空结构拒绝试读） |
| `npx vitest run`（conversations、story） | 47 passed |
| `npx tsc --noEmit` / `npm run build` | 通过 |
| 后端全量 | 见 `dev-notes` 收尾补记 |

## 4. 下一步

1. 重跑 `story-workflow-online`：验证工具说明改进后模型能否一次正确提交（预期消除 create_node/字段发明类失败）。
2. 跑 `clue-workflow-online` 与 `model-flows`（去整理阶段后的版本）。
3. 语义评测：O01–O12 接入 eval harness（每个用例先固定目标、允许信息、禁止改动与判定依据），3 次独立初始状态，共 36 次；门槛与严重失败清单见验收文档第 3 节。
4. 长篇浏览夹具（300 节点 / 30 章）与窄屏验收属于 P6 确定性部分，可与在线重跑并行。

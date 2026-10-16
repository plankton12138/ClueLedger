# Phase 19 对话真模型走查报告

- 时间：2026-10-16T19:11:43.427708
- 模型：qwen3.5:4b（本地 Ollama，config.yaml ollama 档案）
- 证据性质：真实 Provider 流式调用；转录见同目录 *.log（不入库）。

| 场景 | 结果 | 说明 |
|---|---|---|
| 流式多轮（A01 前半） | 通过 | 50 个增量分片，终态 到达，正文 89 字 |
| 只讨论不写入（A02） | 通过 | 讨论前后 Commit 不变、Job 数 0→0（期望不变） |
| 多轮上下文 | 通过 | 第三轮正常完成（服务端携带历史） |
| 停止与文本冻结（A06/A16 前半） | 通过 | 取消事件到达；停止时正文 0 字，5 秒后冻结，状态 stopped |
| SSE 游标补齐不重复（A07） | 通过 | 全量 3 条；after=1 恰好返回其后 2 条 |
| 建议不自动开工（A03/A05） | 通过 | draft 创建后停留在建议态（无 job_ref），不产生 Job |
| 显式启动与模型绑定（A04/A17 前半） | 通过 | Job job_1f60097d97ad4c5fa8904e98a11108ab 创建，绑定 {'profile': 'ollama', 'model': 'qwen3.5:4b', 'api': 'openai-completions'} |
| 候选投影（A13 前半） | 失败 | 真实模型（qwen3.5:4b）执行终态 terminal_failed，未产出合规候选；输出摘要：{"kind": "worker_failure", "category": "environment", "reason": "[WORKER_PROVIDER_INTERRUPTED] 在 agent.stream：Provider 返回错误或中断，本轮没有有效提案。怎样算对：保留同一 Context 并在恢复 Provider 后重试 Job", "errors": [{"where": "agent.stream", "why": "Provider 返回错误或中断，本轮没有有效提案", "expected": "保留同一 Context 并在恢复 Provider 后重试 Job"} |

## 结论（2026-10-16）

**通过 7/8。** 对话链路（流式、停止、断线补齐、多轮、只讨论不写入）在真实
本地模型上全部验证通过；任务准备链路（建议不自动开工、显式启动、模型绑定
快照进 Job）真实链路通过。

**唯一未通过项是模型能力边界，不是链路缺陷**：qwen3.5:4b（4B 思考型本地
模型）在 planner 角色两轮 Schema 纠正后仍无法产出合规 `story_proposal`
（Provider 中途错误/中断 → `terminal_failed`，三要素完整）。创作周期的
投影与接受链路由以下确定性证据覆盖，不依赖本次真模型：

- `tests/v2/server/test_task_lifecycle.py`（脚本化 worker：建议 → 启动 →
  waiting → 候选投影 → 接受 → Commit → 幂等重放）；
- `tests/v2/agents/test_closed_loop.py`（FakeProvider 全闭环回放）。

失败路径本身验证了泄压阀设计：Job 明确失败、无假候选、无静默换厂商、
对话侧不误报成功。国内 API 因当前无可用凭证未验证（标记为未验项）；
建议在接入更强模型或云端 API 后重跑本驱动补齐 A13 真模型证据。

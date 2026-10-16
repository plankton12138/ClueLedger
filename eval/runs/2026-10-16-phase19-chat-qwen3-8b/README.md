# Phase 19 对话真模型走查报告

- 时间：2026-10-16T19:35:31.935967
- 模型：qwen3:8b（本地 Ollama，config.yaml ollama 档案）
- 证据性质：真实 Provider 流式调用；转录见同目录 *.log（不入库）。

| 场景 | 结果 | 说明 |
|---|---|---|
| 流式多轮（A01 前半） | 通过 | 79 个增量分片，终态 到达，正文 97 字 |
| 只讨论不写入（A02） | 通过 | 讨论前后 Commit 不变、Job 数 0→0（期望不变） |
| 多轮上下文 | 通过 | 第三轮正常完成（服务端携带历史） |
| 停止与文本冻结（A06/A16 前半） | 通过 | 取消事件到达；停止时正文 2 字，5 秒后冻结，状态 stopped |
| SSE 游标补齐不重复（A07） | 通过 | 全量 5 条；after=2 恰好返回其后 3 条 |
| 建议不自动开工（A03/A05） | 通过 | draft 创建后停留在建议态（无 job_ref），不产生 Job |
| 显式启动与模型绑定（A04/A17 前半） | 通过 | Job job_06449e05b4c54958b7591bef0744126d 创建，绑定 {'profile': 'ollama', 'model': 'qwen3:8b', 'api': 'openai-completions'} |
| 候选投影（A13 前半） | 失败 | 真实模型（qwen3:8b）执行终态 terminal_failed，未产出合规候选；输出摘要：{"kind": "worker_failure", "category": "violation", "reason": "最多一次纠正后仍不合规，需要人工审阅", "errors": [{"code": "WORKER_OUTPUT_INVALID", "where": "output", "why": "CreateNode.parent_id: 引用的对象 'prj_d3c282ff6335466e:narrative.arc_1' 在当前快照中不存在；改用快照内存在的 ID，或先创建该对象再引用", "expected": "使用当前角色 Schema 和固定版本；无结果用 no_r |

## 结论（2026-10-16，qwen3:8b 复跑）

**通过 7/8**，与 qwen3.5:4b 同分，但**失败模式有质的差异**：

| 对照 | qwen3.5:4b | qwen3:8b |
|---|---|---|
| 流式分片 | 50 | 79 |
| planner 失败码 | `WORKER_PROVIDER_INTERRUPTED`（Provider 中断，无可用输出） | `WORKER_OUTPUT_INVALID` → violation（Schema 合规但引用幻觉） |
| 失败性质 | 模型无法稳定产出合法 JSON Schema | 产出合规 `story_proposal`，但 `CreateNode.parent_id` 引用了快照中不存在的 `narrative.arc_1`；系统给了**一次纠正机会**（把快照内存在的 ID 反馈回模型），纠正后仍幻觉 → 按违规终止 |
| 泄压阀行为 | 环境/中断路径：明确失败、三要素完整、不换厂商 | 违规路径：一次纠正 → 仍不合规 → `terminal_failed` + 人工审阅提示，同样无假候选、不静默降级 |

**链路验证价值**：8b 的失败恰好覆盖了 4b 没走到的路径——「模型输出 Schema
合规但事实不合规」（引用幻觉）触发的是**一次纠正重试**而非直接失败，证明
Schema 校验层的纠错回路真实存在且只重试一次（防无限循环）；两轮失败都落进
各自的泄压阀分类，无假候选、无静默换厂商。

对话链路（流式、停止、只讨论不写入、多轮、SSE 补齐）与任务准备链路（建议
不自动开工、显式启动、模型绑定快照）在 8b 上全部真实通过。**A13 完整闭环
（候选投影 → 接受 → Commit）仍待更强模型或云端 API 复跑**；当前两份报告
共同构成「失败分类 + 纠正回路」在真实模型上的行为证据。

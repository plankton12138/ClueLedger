# V2 分角色走查

每个角色一个 JSON 用例；`driver.py` 先建立人工构造的仓库夹具，再经真实 Tau 适配器与持久 Job 执行。夹具只含怀表谜案的合成数据，不读取用户工作区故事。

```powershell
uv run python eval/v2/driver.py --out eval/runs/<日期>-phase16 --interruption
uv run python eval/v2/driver.py --case eval/v2/writer.json --out eval/runs/<日期>-writer
```

默认 `--provider env` 读取 `.env` 的 `OPENAI_BASE_URL/OPENAI_API_KEY/HASTINGS_MODEL`；`--provider config` 使用服务 `config.yaml` 档案。档案显式配置优先于环境回落，不应把一个服务的模型名与另一个服务的端点混用。密钥仅用于请求认证，不写评测报告。

| 用例 | 检查点 |
|---|---|
| coordinator.json | 选择 draft 用例，不索取审批、不执行写入 |
| planner.json | 新人物为 proposed，通过领域命令预演 |
| writer.json | 已接受 Beat、读者披露边界与整章候选 |
| reviser.json | 只改指定段落，保留完整内容、身份和原 Revision |
| auditor.json | Finding 有范围内对象与精确原文引用 |
| style_observer.json | 数字事实修正识别为 fact，不能激活文风 |

`--interruption` 在真实 Provider 响应已出现文本或工具调用后注入断线，再关闭运行时并从同一 SQLite Job 恢复。若没有收到真实输出或恢复未成功，报告保持失败，不用 FakeProvider 替代此证据。

完整 V2 闭环另用确定性 FakeProvider 回放，经过实际应用层、Tau、HTTP 和仓库；其中一次故意越权的修订必须被拒后纠正。导出转录：

```powershell
$env:PHASE16_TRANSCRIPT="eval/runs/<日期>-phase16/closed-loop-transcript.json"
uv run pytest tests/v2/agents/test_closed_loop.py -q
```

最近验收：[2026-10-15 报告](../runs/2026-10-15-phase16-real-complete/README.md)。每角色当前仅一个定向用例，不把 6/6 当作一般写作质量的统计结论。

## 对话侧栏走查（Phase 19）

常驻对话侧栏（流式聊天、停止、断线补齐、只讨论不写入、聊天到创作）使用专用驱动，进程内启动 V2 应用并接真实 Provider：

```bash
uv run python eval/v2/chat_driver.py --out eval/runs/<日期>-phase19-chat --model <本地模型名>
```

默认 `--provider config` 使用 config.yaml 的 ollama 档案；本地模型先启动 Ollama。四个场景的通过口径写在驱动 docstring；模型输出不合规时报告如实记录，不以 FakeProvider 替代。

## 0.3 写作台真模型走查（V03-8.7）

`desk_driver.py` 进程内启动 V2 应用（隔离 SQLite）+ 真实 Provider（config.yaml 的 ollama 档案），
走查 0.3 的真模型路径与证据分层：讨论流式、试写候选→采用、修订任务预算/覆盖/停止、环境失败记录。

```bash
uv run python eval/v2/desk_driver.py --out eval/runs/<日期>-v03-8.7-real-model --model qwen3:8b
# 追加真模型修订全循环（S5：读章→改稿→覆盖→暂存→发布）
uv run python eval/v2/desk_driver.py --out eval/runs/<日期>-v03-8.7-deep-revision --model qwen3:8b --deep
```

| 场景 | 检查点 |
|---|---|
| S1 讨论流式 | 真实模型增量分片、终态到达、讨论不产生 Job |
| S2 试写候选 → 采用 | 候选带预览哈希；采用走统一发布产生 Commit，`operation_id` 可回读 |
| S3 修订任务预算与覆盖 | `call_limit` 可缩小、开任务零模型调用；读取/覆盖结论入库；停止留痕 |
| S4 环境失败（失败证据） | 不存在的模型必须 `turn.failed`（`CHAT_PROVIDER_FAILED`），不静默、不伪成功 |
| S5 修订全循环（`--deep`） | 读章登记 + 装配含原文；真实模型按原文改写；覆盖→暂存→一次发布（正文换版、回执可回读） |

S5 的模型调用发生在试写候选服务（V03-3）：目标为现有章时，服务自己把该章正文装进
提示词「现有正文」段（V03-8.7 补记修复），驱动不再粘贴原文。任务预算账本当前无执行器
消费（`consume_calls` 无调用方），该缺口在报告里单列为失败证据，不冒充通过。

失败证据按设计记录、不计通过；模型输出不合规时报告如实记录，不用 FakeProvider 替代。
最近验收：[2026-10-19 基础报告](../runs/2026-10-19-v03-8.7-real-model/README.md)、
[2026-10-19 含修订全循环](../runs/2026-10-19-v03-8.7-deep-revision/README.md)、
[2026-10-19 原文自动装入后复跑](../runs/2026-10-19-v03-8.7-deep-revision-v2/README.md)。

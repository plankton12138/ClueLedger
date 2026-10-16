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

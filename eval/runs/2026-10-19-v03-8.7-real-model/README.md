# V03-8.7 0.3 真模型走查报告

- 时间：2026-10-19T20:26:24.890669
- 模型：qwen3:8b（本地 Ollama，config.yaml ollama 档案）
- 证据性质：真实 Provider 调用（本地、无收费 API）；模拟/FakeProvider 证据不在本表。
- 结果：通过 6 / 失败 0 / 失败证据 1（失败证据按设计记录，不计通过）。

| 场景 | 结果 | 说明 |
|---|---|---|
| S1 讨论流式（真模型） | 通过 | 36 个增量、49 字、终态到达；Job 0→0（讨论无写权限，期望不变） |
| S2.1 试写候选（真模型） | 通过 | 候选 cnd_6bb7ec599d564699：213 字，预览哈希 b1745f0e3d6c… |
| S2.2 采用产生 Commit（统一发布） | 通过 | 采用 HTTP 200；operation op_0a4f8741e3891f9…；Commit 已落盘 |
| S3.1 预算可缩小（开任务不调用模型） | 通过 | 任务 rtk_26d56651c469478e：call_limit=1、calls_used=0、remaining=1；Job 数不变（开任务零模型调用） |
| S3.2 覆盖台账（读取登记 + 结论） | 通过 | 2 章登记读取与结论（HTTP {200}）；覆盖 2/2、complete=True；状态 ['changed'] |
| S3.3 停止留痕 | 通过 | 停止 HTTP 200；任务状态 cancelled |
| S4 环境失败记录（不存在的模型） | 失败证据 | 轮次终态 turn.failed；错误：{'code': 'CHAT_PROVIDER_FAILED', 'category': 'environment', 'where': 'chat.runner', 'why': 'Provider 返回错误事件，本轮没有可用正文', 'fix': '检查端点与凭证后重试；不会自动换用其他厂商', 'details': {}} |

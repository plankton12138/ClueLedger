# C1-h 复跑报告 v4：放宽守卫后 harness 臂首次成稿

**日期**：2026-10-12 ｜ **口径**：model=deepseek-flash（DeepSeek API）｜ `--max-advances 3` ｜ slug=c1v8-h ｜ 仅 harness 臂（bare 未跑）

**输入同文校验**：spec.json 由 canonical `eval/specs/c1-v2.json` 程序化复制，rounds/prompt/system 逐字一致（脚本断言通过），仅 slug 换新避免工作区污染——落实当日 P2 修复（杜绝手工漂移）。

## 结论

**协议预算假设验证通过，harness 臂在 C 组首次产出成稿。**

| 指标 | 本轮 | 历史最佳（rerun2/rerun3） |
|---|---|---|
| 成稿 | **6 章 / 8055 字**（全部经 `write_chapter` 落盘） | 0 章 |
| 阶段终态 | `writing`（三次推进 reason 留痕完整） | 卡 `bible_drafting` |
| advance 守卫拒绝 | 0（3 次配额恰好用满） | — |
| bible_upsert 状态机拒绝 | 10 次，**全部读错误后自愈** | 同模式 |
| 事件审计 | events.jsonl 1.6 MB 全程留痕 | — |

## 关键观察

1. **协议预算打通**：`intake→bible_drafting→bible_confirmed→writing` 三格在 15:33:49–15:34:41 约 1 分钟内走完。放宽 `max_advances=3` 后，4 轮脚本预算首次装下 3 格阶段链，且模型未尝试第 4 次推进（守卫零拒绝）——放宽是给"合法连推"开门，不是给滥用开门。
2. **H3 端到端证据落袋**：intake 阶段 prose_guard 真实拦截 1175 字正文直出（含章节标题），错误消息三要素完整。**取证注意**：prose_guard 告警走 stderr（driver 输出），不进入 `C1-h.log` 转录——可观测性留白：守门告警是否应入事件流待议（对应四透镜第 4 条）。
3. **状态机自愈链**：10 次 bible_upsert 拒绝全部为生命周期边违规（如 `flat→developed`、`planned→revealed`），每次拒绝消息给出合法去向，模型下一轮修正，无对抗重试——字段级错误消息即 API 的设计持续有效。
4. **轮次 4 收尾协议正确**：写完全 6 章后主动给出「定稿（推进收尾阶段）/ 修订（保持 writing）」二选一，把阶段决定权交还作者。

## 局限与下一步

- **强模型结果不可外推**：本轮用 deepseek-flash（强模型），只验证机制链路。弱模型线（qwen3:8b/32k）在 `--max-advances 3` 下是否同样打通待复测——那才是对照「模型能力 vs 协议预算」归因的关键数据。
- **CP2 盲评前置达成**：harness 臂已有 ≥1 章成稿，但同模型 bare 臂（deepseek-flash）未跑，成对盲评需补 bare 臂（`--arm bare`，输入同 spec）。
- 转录 `C1-h.log` 与 stderr 告警证据见当日 job 输出（`.log` 不入库约定照旧，本报告已转录要点）。

## 评审记录

（待补：弱模型复测后合并归因结论）

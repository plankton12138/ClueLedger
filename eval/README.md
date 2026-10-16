# Hastings 真模型评测（eval/）

> 入口索引：本文件是评测工具目录的唯一导航。找「怎么跑、跑什么、产物在哪」先看这里。

## 目录结构

```
eval/
├── README.md      # 本文件：目录导航 + 快速开始
├── driver.py      # 统一真模型驱动（多臂：harness/bare，--sample 采样）——以本文件为准
├── specs/         # 对照实验 spec（arm/slug/rounds/prompt），如 c1-v2.json / c2-v2.json / c3-v2.json
├── intake/        # 走查用例集：README（判定口径与索引）+ A/B/C 组用例 + C-v2-scoring.md（盲评协议）
├── runs/<日期>/   # 运行产物归档：入库 driver/spec/报告，*.log 转录不入库
└── assets/        # 基准源原文（三篇 txt 入库；*.epub 版权文本不入库）
```

## 快速开始

### V2 · 六角色与持久工作流（Phase 16）

V2 独立走查入口为 [v2/README.md](v2/README.md)，一角色一用例；Phase 19 对话侧栏走查用 [v2/chat_driver.py](v2/chat_driver.py)（流式/停止/断线/聊天到创作）；运行 `uv run python eval/v2/driver.py --out eval/runs/<日期>-phase16 --interruption` 可验证真实 Provider 与同一 Job 断线恢复。完整应用闭环使用 FakeProvider 另行回放，最近 [验收报告与转录](runs/2026-10-15-phase16-real-complete/README.md) 同时保留两类证据。以下 C/A/B 组仍服务 V1 协议对照。

**前置**：仓库根 `.env` 配好 provider（含 `HASTINGS_MODEL`）；每条用例换新 slug 保证从 intake 阶段开始。

### C 组 · 对照实验（driver 驱动，量 harness 增益）

```powershell
# 先跑成本最低的 bare 臂验证管线，再跑 harness 臂；正式评测加 --sample 3
uv run python eval/driver.py --spec eval/specs/c1-v2.json --out-dir eval/runs/<日期>/ --arm bare
uv run python eval/driver.py --spec eval/specs/c1-v2.json --out-dir eval/runs/<日期>/ --arm harness --sample 3
# C2/C3 换对应 spec；CP2 盲评按 eval/intake/C-v2-scoring.md 执行
```

**harness 臂口径（协议预算决策，2026-10-12）**：4 轮脚本对照时 harness 臂须加 `--max-advances 3`——阶段链 intake→bible_drafting→bible_confirmed→writing 需 3 次推进，产品默认「每回合至多 1 次」在 4 轮预算内结构上装不下（见 runs/2026-10-12-local-rerun2 报告 §3.1）。bare 臂不受影响；每轮报告须记录实际使用的 `--max-advances` 值以保证口径可追溯；产品端（CLI/Web）默认值保持 1 不变。

### A/B 组 · 协议走查（手工粘贴，量 intake 协议遵循度）

```powershell
uv run python -m hastings intake-smoke   # 全新 slug 进入 intake 阶段
```

按 [intake/README.md](intake/README.md) 的用例文件逐条粘贴，按该 README 的三档口径判定。

### 本地模型跑法（弱模型压测）

用 8B 级本地模型（如 qwen3:8b）跑同一套 driver，把「模型自觉」从「系统机制」里剥离——强模型替软协议兜的兜（intake 抢跑、正文直出等），在弱模型上会显形为真实缺口。**方法论一句话**：同一委托书、同一 driver，只换模型来源，观察哪些软协议失效、哪些工具层硬门仍成立。

```powershell
# 1) Ollama 运行时默认 num_ctx 远小于长会话需求，须先派生显式窗口的模型
#    否则多轮必被截断，压测结论不可信（本次 qwen3-32k 显式 num_ctx 32768）
ollama create qwen3-32k -f Modelfile   # Modelfile: FROM qwen3:8b / PARAMETER num_ctx 32768

# 2) 进程级 env 覆盖（不改 .env，一次会话生效）：
#    - OPENAI_BASE_URL  指向本地端点（如 http://localhost:11434/v1）
#    - OPENAI_API_KEY   占位非空值（driver 直连 provider，no_auth 场景未走
#                       omit_authorization_header 路径，空 key 会被 httpx 拒）
#    - OPENAI_TIMEOUT_SECONDS  拉长（本地模型慢，如 600）
#    - HASTINGS_MODEL   派生模型名（如 qwen3-32k）
$env:OPENAI_BASE_URL="http://localhost:11434/v1"; $env:OPENAI_API_KEY="ollama"
$env:OPENAI_TIMEOUT_SECONDS="600"; $env:HASTINGS_MODEL="qwen3-32k"
uv run python eval/driver.py --spec eval/specs/c1-v2.json --out-dir eval/runs/<日期>-local/ --arm harness
```

driver 对 0 章成稿会输出 stderr 三要素告警（不再静默成功）——本地压测先看这行，再看转录定位缺口。范例结论见 [runs/2026-10-11-local/report-local-qwen3.md](runs/2026-10-11-local/report-local-qwen3.md)。

## 约定

- **spec 与用例同源**：`specs/*.json` 里的委托书文本与 `intake/` 用例文件的「作者构想」严格同文，改一侧必须同步另一侧。
- **runs 归档**：每次运行在 `eval/runs/<日期>/` 留 driver 副本、spec.json、report-*.md；`*.log` 转录属运行产物已 gitignore。runs 内的历史 driver 是当日存档，**勿当作可执行入口**。
- **防污染红线**（C 组）：委托书不含原书书名/人名/地名/标志性原句；输出复述原书设定 = 协议违规，不入胜率统计。细则见 [intake/README.md](intake/README.md)。
- **失败处理**：不合规 → 归因（协议措辞 / 硬约束缺失）→ 改 prompts.py 或工具层并补测试 → 换新 slug 复跑。

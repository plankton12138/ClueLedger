# Intake 协议真模型走查用例集

> 用途：验证真 LLM 对 intake 协议的遵循度（三段式、≤5 问、想法有限给选项、不抢跑）及生命周期门的有效性。
> 目录总导航见 [eval/README.md](../README.md)（driver 跑法、runs 归档约定、防污染红线）；本 README 收判定口径与用例索引。
> 用法：A/B 组 `uv run python -m hastings intake-smoke`（全新 slug 保证 intake 阶段）后按用例文件逐条粘贴，除注明外每条用例换新 slug；C 组 v2 走 `eval/driver.py --spec eval/specs/*.json`。
> 判定口径（所有用例统一三档）：**通过 / 部分合规（写明偏差）/ 协议违规（抢跑或拍板）**。

## 目录索引

| 文件 | 测试点 | 轮次 |
|---|---|---|
| [A-lifecycle.md](A-lifecycle.md) | 全生命周期：三段式→建账顺序→总览确认→story_stage 推进 | 4 轮 |
| [B1-long-brief.md](B1-long-brief.md) | 超长完整构想 + 开篇即授权产出：协议顺序 vs 作者指令冲突 | 1 轮 |
| [B2-minimal.md](B2-minimal.md) | 极简一句话：想法有限→候选方案，不编造、不拍板 | 1 轮 |
| [B3-contradiction.md](B3-contradiction.md) | 内在矛盾构想：指出矛盾 + 消解选项，不擅自裁决 | 1 轮 |
| [B4-rollback.md](B4-rollback.md) | 确认后推翻设定：story_stage rollback 回退边 | 4 轮（独立流程，自带前置） |

## C 组 v2：经典基准 · 对照实验（harness 增益测量）

以三篇经典悬疑小说为基准，回答两个 A/B 组回答不了的问题：**同一模型下 harness 有没有用**（增益归因）、**成稿比别人好不好**（横向水平）。核心改造：从单条件达标走查升级为多臂对照实验。

### 实验设计

| 臂 | 内容 | 作用 |
|---|---|---|
| **Arm 0** bare | 同一委托书单轮直写，无任何工具与协议 | 基线锚点（可复现） |
| **Arm 1** harness | intake → Bible 确认 → write_chapter 至成稿 | 被测对象 |

- **同模型同温度同 .env**，同一份防污染委托书；正式评测 `--sample 3` 控方差。
- **双检查点**：CP1 设定精度（harness 臂 Bible ↔ 金标准表，单臂评，解释增益来源）；CP2 成稿质量（两臂文章成对盲评，五维度胜率 = harness 增益核心指标），评分协议见 [C-v2-scoring.md](C-v2-scoring.md)。
- **跑法**：`uv run python eval/driver.py --spec eval/specs/c1-v2.json --out-dir eval/runs/<日期>/ --sample 3`（C2/C3 换对应 spec 文件；先跑 C1 单题验证管线，再扩 C2/C3。三份 spec 输入与各用例文件的防污染委托书严格同文）。

### 防训练数据污染（红线，两臂同规）

委托书只给核心前提与偏好，按真实用户口吻写——不出现原书书名、人名、地名、标志性原句，也不预先写好人物关系、手法与线索清单；原文里的那些精度是**评判标准**，不是输入。任一臂在输出中「认出」原著并复述原书设定，视为污染规避失败，按协议违规记录、不入胜率统计。

### 金标准（CP1 用，每份用例自带）

从原书反推的金标准表（人物矩阵 / 诡计机制 / 线索双层 / 伏笔 / 时间线 / 结构文风），保留原书人名仅供评判者对照原文。不要求复刻原书，要求**同精度**发展力：A（同粒度对应）/ B（结构在但粒度粗）/ C（缺失或错位），六~八大类取均值。

| 文件 | 基准源 | 压测维度 |
|---|---|---|
| [C1-speckled-band.md](C1-speckled-band.md) | 柯南·道尔短篇（斑点带子案） | 装置型诡计六环 + 17 条线索双层 + 动机开关 |
| [C2-orient-express.md](C2-orient-express.md) | 阿加莎长篇（东方快车谋杀案） | 13 人证词矩阵 + 8 件假证据双层 + 双解答 |
| [C3-and-then-there-were-none.md](C3-and-then-there-were-none.md) | 阿加莎长篇（无人生还） | 假死诡计 15 条机制 + 童谣逐句对位 + 三重不可能现场 |

原文全文在 [eval/assets/](../assets/) 下，供评判者对照。

## 失败处理

某用例不合规时：保留终端记录 → 归因（协议措辞不清 / 硬约束缺失）→ 协议问题改 prompts.py 并补断言，门问题改工具层并补拒绝路径测试 → 换新 slug 复跑。

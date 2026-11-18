# 评测系统结构讲解

更新于 2026-11-15。配合交互图解：[structure-map.html](structure-map.html)。若想先听运转原理，读故事版：[how-it-works.md](how-it-works.md) · [how-it-works.html](how-it-works.html)。设计依据：[design.md](design.md)。

## 1. 它解决什么问题

离线评测回答两件事：**当前版本好不好**，以及**不好时最早坏在哪一环**。

它不是产品的一部分：不 `import hastings.workspace` 业务模块，只对已启动的本地模式服务走 HTTP，读写运行产物目录。产品 Agent 的运行时与评测 Harness 职责分离。

## 2. 目录一览

```
evaluation/
  __main__.py          CLI：list / run / score / workbench
  cases/               案例 YAML（模型可见初态 + 评测器专用期望）
  works/               基础作品快照（现行作品树；案例引用如 work: base/被解救的罪人）
  harness/             加载、跑试验、采证、判分、报告
  workbench/           本机工作台服务（读报告、复核、归因）
tests/evaluation/      harness 确定性测试（假证据，不调在线模型）
data/evaluation/runs/  本机运行产物（gitignore，不入库）
```

设计文档在 `dev-notes/evaluation/`；参考文章在 `refer/`。

## 3. 四条命令各自干什么

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `list` | `cases/` + `works/` | 打印案例 id / 版本 / 作品 / 历史种类 |
| `run` | 已启动的本地服务 + 案例 | `out/report.json` + `attempts/<案>--<次>/` 证据包 |
| `score` | 已有运行目录 | 用证据重评 must/quality，可选合并 `reviews.json` |
| `workbench` | `data/evaluation/runs` | 本机页浏览报告、点证据、写复核与归因 |

冒烟：`--mode smoke --repeat 1`。完整门禁：`--mode gate --repeat 3`，可带 `--baseline`。

## 4. 一次 `run` 的数据流

```
cases/*.yaml ──schema──► Case
works/<work>/ ──复制──► attempts/.../work/
                              │
                              ▼
                     HttpEvalClient（本地模式 HTTP）
                     开启 capture_inputs → 按 steps 受理 → 轮询事件
                              │
                              ▼
                     evidence/（before、diff、events、traces、inputs）
                              │
                     ┌────────┴────────┐
                     ▼                 ▼
                  checks.py         stages.py
                  must/quality      六环 earliest fail
                  /human 判分
                     └────────┬────────┘
                              ▼
                     report.json（含 gate）
```

关键约束：

- 输出目录必须尚不存在，避免覆盖旧运行。
- 每试验独立复制作品；比较版本时不得复用旧试验产物。
- 预算 `--max-requests` 耗尽后剩余记 `not_run`。
- 模型自述「已经改好」不作证据；依据是文件差异、回执与 Trace。

## 5. harness 模块分工

| 文件 | 职责 |
| --- | --- |
| `schema.py` | 加载 YAML；锚点在初态中须存在且唯一 |
| `client.py` | 打开作品、开输入快照、受理轮次、轮询事件、读 Trace |
| `runtime.py` | 编排一次试验：复制 → 跑 → 采证 → 调判分与过程环 |
| `evidence.py` | 前后文件快照、diff、回执提取、证据包落盘与重载 |
| `checks.py` | 程序判分（must/quality）；human 进 `pending_review` |
| `stages.py` | 同次运行上算 scope→read→candidate→verify→repair→commit |
| `report.py` | 汇总、基线对比、门禁、复核合并、离线重评 |

当前已实现的检查 id：`only_region_changed`、`region_actually_changed`、`preserved_verbatim`、`committed_via_candidate`、`stale_candidate_rejected`、`chapter_in_model_input`、`scope_matches_auth`。

## 6. 案例长什么样

一条案例五部分：作品初态、会话历史种类、作者步骤、可选环境变化、期望与检查项。后两部分不进模型可见上下文。

现成案例：`cases/revise_ch1_ending_farm_tonight.yaml`，作品 `works/base/被解救的罪人`（真实快照；`*.meta/` 为评测说明，不进复制）。检查含区域/保留/经候选提交（must）、输入含目标章（quality）、当晚去农场（human）。

## 7. 运行产物长什么样

```
data/evaluation/runs/<name>/
  report.json | report.md
  reviews.json          # 工作台写入后
  attempts/<case>--<n>/
    work/               # 该次试验后的作品树
    evidence/
      before.json
      diff.json
      events.jsonl
      traces/
      inputs/           # capture_inputs 开启时
```

`report` 里每次试验带 `checks`、`stages`、`first_failed_stage`。门禁：任一 must 失败 → 不通过；quality 相对基线退化另有规则；基础设施错误与未运行不进通过率分母。

## 8. 工作台

`workbench/server.py` 扫运行根目录，优先托管 `frontend/dist/eval-workbench.html`（共用产品 Trace 组件），否则回退 `workbench/static/index.html`。

职责：Case 库只读、报告与证据、人工复核与归因、跑测 Job（`jobs.py` 调同一 `run_batch`）。启动见 [evaluation/SETUP.md](../../evaluation/SETUP.md)。原理故事与代码落点见 [how-it-works.md](how-it-works.md)。

## 9. 和 CIAO 的挂接（当前实现）

| 视角 | 在评测里怎么落地 |
| --- | --- |
| O | must：区域/保留/提交；human：意图 |
| I | `stages` 六环 + 同次 Trace |
| C | 质量项贴 Context/Action；核对专用集（阶段 3）未做 |
| A | `first_failed_stage` + 工作台归因；失败转案例（阶段 4）未做 |

## 10. 建议阅读顺序

1. 打开 [how-it-works.html](how-it-works.html)：故事 → 设计地图 → 找代码 → Scope 落地  
2. 需要目录对照时再读本页第 4–5 节 / [structure-map.html](structure-map.html)  
3. 打开 `cases/revise_ch1_ending_farm_tonight.yaml` 看一条完整案例  
4. 按 [SETUP.md](../../evaluation/SETUP.md) 跑一次冒烟，再开工作台看报告与证据  

# 评测设计会话交接（压缩）

日期：2026-11-16。供新对话接续，不必重读长 transcript。

## 目标

按白皮书「三块能力 + 观测地基」简化总图 + CIAO（嵌在离线与归因），把线簿离线评测跑成可归因闭环。无线上用户时不假装有在线评测。

## 已定结论

1. **总图**：见 [design.md](design.md) 第 0 节。离线 / Case / 观测写实；在线空槽。
2. **不先堆组件测**：先一条改稿 I+O 冒烟闭环，再按失败拆 C。
3. **Case 源（本阶段）**：作者本机失败、规则扫描、离线失败——替线上反馈。
4. **作品夹具**：现行作品树；已从本机真实作品复制快照。
5. **工作台 W7**：Case 库（只读+相关运行）/ 结果 / 跑测控制（真触发 `run_batch`）。

## 代码现状（重要路径）

| 路径 | 状态 |
| --- | --- |
| `evaluation/harness/runner_api.py` | CLI 与工作台共用 `run_batch` |
| `evaluation/workbench/jobs.py` | 单 active Job，可取消 |
| `frontend/src/eval-workbench/` | 三入口：CasesPage / 结果 / LaunchPage |
| `evaluation/cases/revise_ch1_ending_farm_tonight.yaml` | 当前改稿案 |
| `evaluation/works/base/被解救的罪人/` | 真实作品快照 |

## 下一步（未做）

1. **在线冒烟**（总图展开第 1 项）：本地服务 + 工作台「跑测」或 CLI 跑 `revise_ch1_ending_farm_tonight`
2. 工作台复核 `farm_tonight_present` + 按 `first_failed_stage` 归因
3. 候选核对过程集（C）→ Case 回流 → 其余端到端集

## 注意

- 装配指向本机 `ClueLedger/ModuleLibrary`；同机跑才解析模块。
- 真实快照含作者正文，是否入库由作者决定。
- 工作台跑测前先 `npm --prefix frontend run build`。

# V03-0.7 配置与测量（凭据盘点、统一配置、能力接口草案、性能基线）

> 状态：完成。生成：2026-10-17。测量环境：Windows（win32）、Python 3.12（uv 托管）、SQLite 持久库。

## 1. 凭据存储现状盘点（计划 §2.1 第 8 行的收口）

| 现状 | 位置 | 风险 | 0.3 处置 |
|---|---|---|---|
| `ProviderProfile.api_key` 用 `SecretStr`（repr/日志不泄露），对外只经 `mask_key`（尾 4 位） | `server/provider_config.py:42-69` | 已防泄露扩散 | 保留 |
| Key 直填或 env 引用（`api_key`/`api_key_env`），落在 `config.yaml` 配置文件 | `server/provider_config.py:57-63` | 配置文件明文 | V03-1.3：桌面凭据走 OS 凭据存储适配端口，元数据只存 `credential_ref`；升级顺序「新存储写入并验证→元数据切换→清理旧 Key 字段」 |
| 环境变量回退（`env_fallback_config`，`HASTINGS_PROVIDER/MODEL`、`OPENAI_API_KEY` 等） | `server/provider_config.py:90`、`server/v2_agents.py:_resolve_profile_model` | 普通产品流借用环境 Key | V03-1.3：普通流移除回退；开发 `.env` 继续可用但不转成平台默认凭据 |
| Job payload 的 `model_binding`（profile/model/api，无 Key） | `worker_job.py:submit` | 低 | 保留；V03-0.3 的 ConnectionSnapshot 同样只含非敏感字段 |

结论：**Key 不进日志/查询/导出已有防线（SecretStr + mask_key）；未收口的是「配置文件明文」与「环境变量回退」两点，均在 V03-1.3 处置**，本版不提前改存储。

## 2. 统一配置（新增 `application/capabilities.py`）

初值是防护值不是能力承诺；`GET /api/v2/capabilities` 公布什么，前端就只能看到什么：

| 配置 | 初值 | 来源 |
|---|---|---|
| 导入格式 | `.txt`/`.md`，UTF-8/UTF-8 BOM | M06 实施基线 |
| 单文件上限 | 10 MiB | M06 |
| 单批总计/章数 | 20 MiB / 1000 章 | M06 |
| 预览保留期 | 24 小时 | M06 |
| 调用预算（每任务） | 12 次（`execution.DEFAULT_CALL_BUDGET`） | M10 工程初值 |
| 执行日志分页 | 200 条/页 | §9.6 |
| 图谱首屏 | ≤200 节点 / 500 边 | §9.6 |
| 副本保存防抖 | 500–1000 ms | §9.6 |

功能状态三态：`implemented`（默认装配可用：execution_grants）/ `draft`（契约已冻结、实现中：import/edit_sessions/operation_records/execution_events/summaries/ledger_suggestions/agreements/graphs）/ `excluded`（author_model/style_observation/consistency_audit/corpus_rag——不公布为可用，也不提供占位入口，计划 §1.4）。

## 3. 能力接口草案（已落地端点 + 测试）

`GET /api/v2/capabilities`（`server/v2.py`）：返回 product/api 版本、import 上限、性能目标、功能状态。测试 `tests/v2/server/test_rest_v2.py::test_capabilities_endpoint_publishes_unified_config` 钉住形状与排除项状态。前端消费方在 V03-1 接线（本版不预先改前端常量）。

## 4. 性能基线测量（`scripts/bench_v03_baseline.py`，真实样本）

样本（§9.6）：100 章 × 3000 字 ≈ 30 万汉字 + 500 账本对象 + 1000 关系；测量走真实路径（import 命令 / `workflow.save` / `queries.catalog` / `get_snapshot`），SQLite 持久库，30 轮：

| 场景 | 目标（§9.6） | 实测 P50 | 实测 P95 | 结论 |
|---|---|---|---|---|
| 单章保存（服务确认） | P95 ≤ 1000 ms | 49.19 ms | 78.13 ms | 达标（约为上限 1/13） |
| 章节回读（暖查询） | P95 ≤ 500 ms | 43.61 ms | 77.30 ms | 达标 |
| 全文目录读取 | （无单列目标，参考暖查询） | 43.44 ms | 71.04 ms | 达标 |
| 故事快照（图谱/上下文数据源） | （参考） | 42.38 ms | 73.43 ms | 达标 |
| 副本自动保存（防抖+确认） | 500–1000 ms 防抖；确认 P95 ≤ 1 s | — | — | pending：V03-2.4 建成后测 |
| 无模型修改完成（两章各 1 万字） | P95 ≤ 2 s | — | — | pending：V03-2.4/5.1 |
| 图谱首屏 / 日志分页 | ≤200 节点/500 边；200 条/页 | — | — | pending：V03-6 / V03-1.6 |
| 运行 UI（流式稳定性） | 选区与滚动稳定 | — | — | pending：浏览器录制（V03-8.3） |
| 模型延迟 | 不设统一时限 | — | — | 不适用（展示等待/过程/停止） |

完整 JSON：`scripts/bench-v03-baseline.json`。复跑：`uv run python scripts/bench_v03_baseline.py --json scripts/bench-v03-baseline.json`。

## 5. 验证证据

- `uv run pytest tests/v2/server/test_rest_v2.py -q`：19 passed（含 capabilities 契约）。
- 测量运行：2026-10-17，本机（win32，uv 托管 Python 3.12，SQLite 文件库）；数字为单次运行，跨设备不承诺（§9.6 纪律）。
- 本机配置核查：`config.yaml` 存在且含 provider 档案（§1 盘点据此）。

## 6. 留白

- 留白 1：`import`/`edit_sessions` 等 draft 功能的上限已公布但实现未跟上——V03-2 实现时必须消费同一常量（导入上限在 M06 实施时从本模块读），不允许前端或后端各写一份。
- 留白 2：性能目标里 UI 侧两项（流式稳定、浏览器录制）与日志/图谱两项在对应阶段验收；本基线只固定服务侧四项。
- 留白 3：凭据存储改造（OS 凭据端口）未动，V03-1.3 落地时同步更新本表「现状」列。

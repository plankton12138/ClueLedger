# V04-9 供应商连接：改动与验证

日期：2026-10-21。范围依据[供应商连接开发计划](../model-connections-plan.md)；当前行为见[模型连接](../modules/model-connections.md)。

## 1. 交付内容

作者在界面上选择供应商、填 API Key、选模型，然后直接开始讨论或写作，不必编辑配置文件。结构是「供应商预设 → 连接 → 模型」：预设只描述怎么接，连接是作者自己的账号，模型是连接可调用的标识。

四个子阶段一次交付：

| 子阶段 | 实际内容 |
|---|---|
| V04-9A 配置与协议 | 供应商目录；连接字段扩展（显示名、可选模型、四类协议）；选择规则与配置版本；四类协议的实例构造与关闭 |
| V04-9B 管理与运行 | 连接增删改与凭据分步提交；模型发现、响应测试、工具测试；`/api/model-*` 接口；设置与写作共用一份配置；每轮快照与实例关闭 |
| V04-9C 设置界面 | 供应商选择、连接表单、模型发现与手填、两种测试结果、连接管理、日常模型选择、未配置引导 |
| V04-9D 验证与文档 | 受控传输验证四类协议；真实 DeepSeek 全链路；模块文档与导航更新 |

## 2. 主要改动

| 文件 | 改动 |
|---|---|
| `src/hastings/server/provider_catalog.py`（新增） | 九条预设（含自定义服务）、协议与列表策略、区域选项 |
| `src/hastings/server/provider_runtime.py`（新增） | 按协议构造 Anthropic / Google / OpenAI 兼容实例；`ModelSnapshot` 与关闭；本机地址不经代理 |
| `src/hastings/server/model_probe.py`（新增） | 三种列表策略（含分页上限）、响应测试、`connection_probe` 工具往返、失败分类 |
| `src/hastings/server/model_connections.py`（新增） | 串行写服务：校验、凭据分步提交、版本核对、选择与清空、删除当前连接 |
| `src/hastings/server/model_api.py`（新增） | 九个 `/api/model-*` 接口与请求/响应 DTO |
| `src/hastings/server/provider_config.py` | 连接字段扩展；`selection_mode`；空列表合法；`config_revision`；去掉 provider 实例缓存；选择解析与运行时覆盖 |
| `src/hastings/workspace/service.py` | 工厂改为返回 `ModelSnapshot`；受理时固定快照；结束时关闭实例；`turn_started` 带连接与模型 |
| `src/hastings/workspace/api.py`、`server/file_app.py` | 作品登记参数改名 `workspaces`；装配模型服务并挂载模型路由 |
| `src/hastings/server/__main__.py` | 一个凭据存储与一个连接管理服务，同时交给设置与写作；内存降级时在启动输出里提示 |
| `scripts/check_model.py` | 走新的选择解析与快照路径，用完关闭实例 |
| `frontend/src/api/models.ts`、`state/models.ts`（新增） | `/api/model-*` 客户端（`If-Match`、错误码）；只用脱敏状态 |
| `frontend/src/components/models/`（新增） | `ModelSettingsDialog`、`ProviderPicker`、`ConnectionForm`、`ModelSelector` |
| `frontend/src/components/workspace/` | 顶栏「模型设置」、对话栏模型选择器与未配置引导；聚焦时重读配置 |

## 3. 接口与数据影响

- 新增应用级路由（与作品无关）：`GET /api/model-providers`、`GET|POST /api/model-connections`、`PATCH|DELETE /api/model-connections/{name}`、`POST /api/model-connections/discover`、`POST /api/model-connections/probe`、`GET|PUT /api/model-selection`。写操作要求 `If-Match`，冲突 409 `MODEL_CONFIG_CONFLICT`。
- `config.yaml` 新增可选字段：连接上的 `display_name`、`models`、`api` 扩展为四值、`catalog_id`；根节点新增 `selection_mode`（`auto|explicit|none`）。旧字段 `name`、`base_url`、`api_key`、`credential_ref`、`default_model`、`no_auth`、`active_provider`、`active_model` 语义不变。
- `providers: []` 由「损坏」改为合法空状态。
- `turn_started` 事件新增 `connection_id`、`connection_name`、`model`；不含凭据。
- 内部契约变化：`ProviderFactory` 由 `() -> (provider, model)` 改为 `() -> ModelSnapshot`；`create_workspace_app()` 的作品登记参数改名为 `workspaces` 并新增 `model_service`。调用方与测试同步更新。

## 4. 实现取舍

- **每轮独占实例**：受理时创建、结束关闭。放弃轮次之间的连接复用，换取「编辑或删除连接不会关掉正在使用的 client」，也不需要跨轮引用计数。
- **写盘只带凭据引用**：新 key 先写新引用并回读校验，再写配置，最后清理旧引用。任何一步失败保留旧连接可用；提交后清理失败只影响存储占用。
- **空选择要能持久化**：新增 `selection_mode`；删除当前连接或主动清空写 `none`，重启后不回到「首个可用连接」。旧配置有 `active_provider` 按 `explicit` 读，否则 `auto`。
- **版本用文件内容摘要**：复制、恢复或重新保存会改变时间戳但不改变内容，用内容才能表达「你手里的列表已经过期」。
- **测试结果不入库**：后端只返回本次测试结论，界面按「地址 + 协议 + 凭据 + 模型」的指纹判断是否仍然有效，重启后显示未测试。
- **发现与测试不重试**：一次点击不该产生不可见的多次付费调用；列表分页也设上限并标注结果是否完整。
- **本机地址绕过代理**：本机兼容服务不经系统代理；这条也影响了验证脚本（环境里有 `HTTP_PROXY`，本机请求必须显式绕过）。

## 5. 验证证据

| 检查 | 结果 |
|---|---|
| `uv run pytest tests/workspace -q` | 229 passed |
| `uv run ruff check src/hastings tests/workspace scripts/check_model.py` | All checks passed |
| `uv run mypy src/hastings` | no issues in 35 source files |
| `cd frontend && npm test` | 36 passed（含模型客户端 8 项、模型状态 9 项） |
| `cd frontend && npm run build` | 通过（`tsc --noEmit` + vite build） |
| `uv run python scripts/check_model.py` | `deepseek-flash` 回复「你好！」，key 尾四位 `…114a` |
| 真实连接链路（独立配置与测试作品） | 见下与[验证材料](../artifacts/v04-9-verification.md) |

真实链路在独立 `--config` 副本与独立测试作品上执行，未改动仓库里的 `config.yaml` 与真实作品：

```text
连接列表：deepseek，has_key=True，存储=system，选择 automatic（deepseek-flash）
模型发现：code=None complete=True，2 个模型（deepseek-flash、deepseek-v4-pro）
响应测试：passed    工具测试：passed
新增连接：conn_5a48402f，选择未变（automatic）
设为当前：saved
一轮写作：工具 read_file×3 → list_files → write_file
          结果 completed / committed，正文/0001 雨夜.md（709 字节），commit a1e5868
删除临时连接：选择清空（none）
恢复选择：deepseek / deepseek-flash（saved）
重启后：选择一致
```

按协议覆盖：Anthropic、Google 与 OpenAI 兼容三条列表路径、认证头与分页由 `httpx.MockTransport` 的受控传输验证；另有一条本机真实 HTTP 服务用例验证 socket 级往返。没有凭据的厂商只验证到适配契约。

## 6. 偏差与未完成项

- **浏览器逐屏走查未做**：界面由构建、状态与组件测试覆盖，真实链路在接口层取证，没有在浏览器里从「打开设置」点到「看见新章节」的截图或录屏。
- **真实供应商只覆盖 DeepSeek**：Anthropic、Google、通义千问、Moonshot、智谱、OpenRouter 需要各自凭据，本次没有。
- **Anthropic 列表接口以仓库适配器与既有 API 契约实现**：实施时 `platform.claude.com` 在本机网络不可达，未能当场核对文档页；`x-api-key` + `anthropic-version` + `limit`/`after_id` 的分页按既有公开契约实现，受控传输验证了请求形状。
- **不支持多进程同时编辑配置**：并发保护在单进程内，另加提交前的版本核对。
- **没有连接级高级参数**：推理强度、上下文长度、供应商特有兼容开关不在本次范围，也不做能力推断。
- **token 级流式**仍未实现：回复整条显示。

# 模型连接

核对日期：2026-10-21。本文描述 V04-9 已实现的行为：供应商预设、连接配置、凭据、模型发现与测试、当前选择，以及写作轮次如何使用它。阶段改动与验证材料见[阶段记录](../notes/v04-9-model-connections.md)与[验证材料](../artifacts/v04-9-verification.md)。

## 1. 职责与范围

作者在界面上选择供应商、填 API Key、选模型，然后直接开始讨论或写作，不必手工编辑配置文件。模型设置是应用级能力：没有打开作品时也能用。

结构是三层：

```text
供应商预设（怎么接） → 连接（作者自己的账号） → 模型（该连接能调用的标识）
```

- **预设**只描述接入方式：默认地址、协议、文档入口、能不能读模型列表、需要手选的服务区域。它不含密钥，也不代表作者已经连上。
- **连接**是作者保存的一条账号记录：完整地址、协议、凭据引用、可选模型与默认模型。同一个供应商可以建多个连接（两个账号、两个区域）。连接名称只是显示名；新增时先预填供应商名（例如「DeepSeek」），已有同名连接时按序号往后排（「DeepSeek 2」），作者仍可随时改成「我的 DeepSeek」这类更好认的名字。
- **模型**是连接的属性：作者勾选日常可用的几个，并指定默认模型。

不在范围内：本地模型安装与硬件检测、订阅账号登录与 OAuth、自动切换供应商、失败后跨供应商重试、多模型协作与任务路由、价格与余额查询、token 级前端流式展示。已有配置里的本地地址与 `no_auth` 继续兼容，但没有新增本地模型入口。

## 2. 供应商预设

目录由后端提供（`server/provider_catalog.py`），前端只渲染接口返回值：供应商表有两份就会各自漂移，而地址写错的表现是「保存成功、调用失败」，最难让作者自己看出来。

| 预设 | 协议 | 默认地址 | 模型列表 |
|---|---|---|---|
| OpenAI | Responses（高级设置可改 Completions） | `https://api.openai.com/v1` | 读取 `/models` |
| DeepSeek | OpenAI 兼容 Completions | `https://api.deepseek.com/v1` | 读取 `/models` |
| 通义千问（阿里云百炼） | OpenAI 兼容 Completions | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 读取 `/models`；区域必须手选（北京 / 新加坡） |
| Moonshot | OpenAI 兼容 Completions | `https://api.moonshot.cn/v1` | 读取 `/models` |
| 智谱 AI | OpenAI 兼容 Completions | `https://open.bigmodel.cn/api/paas/v4` | 读取 `/models` |
| OpenRouter | OpenAI 兼容 Completions | `https://openrouter.ai/api/v1` | 读取 `/models` |
| Anthropic | Messages | `https://api.anthropic.com/v1` | 读取 `/models`（`limit` + `after_id` 分页） |
| Google Gemini | Google 原生接口 | `https://generativelanguage.googleapis.com/v1beta` | 读取 `/models`（`pageSize` + `pageToken`） |
| 自定义服务 | 四类协议任选 | 作者填写 | 按所选协议；没有列表接口时手填 |

预设是对「你连的是哪家服务」的分类，不按模型的原作者归类：OpenRouter 上的某个模型仍属于 OpenRouter 连接。需要手动选区域的供应商在预设里给出区域选项，不自动猜地区；没核对过的地址留空让作者填，不靠拼接域名补齐。

运行时不刷新第三方目录，也不在后台探测供应商：只有作者点「获取模型列表」才发请求。

## 3. 配置与兼容

模型配置只有一处事实源：启动时实际使用的 `config.yaml`（或 `--config` 指定的文件）。设置界面是该文件的编辑界面，不另建数据库、不做前端持久化、不读进程环境变量。

```yaml
providers:
  - name: conn_5a48402f              # 稳定标识，不随显示名变化
    display_name: 我的 DeepSeek       # 界面显示名，可改
    catalog_id: deepseek             # 供应商预设；未知值按自定义展示
    base_url: https://api.deepseek.com/v1
    api: openai-completions          # 四类协议之一
    models: [deepseek-flash, deepseek-v4-pro]
    default_model: deepseek-flash
    credential_ref: hastings:provider:conn_5a48402f:1f3a9c02
active_provider: conn_5a48402f       # 显式选择时写入
active_model: deepseek-flash
selection_mode: explicit             # auto | explicit | none
```

兼容规则：

- 旧档案只有 `default_model`、没有 `models` 与 `display_name` 时照常读取：可选模型视图把 `default_model` 当作唯一模型，显示名回退到 `name`。读时兼容、写时落新字段，不在启动时批量改写磁盘。
- 有 `active_provider` 而没有 `selection_mode` 的旧文件按 `explicit` 读取；两者都没有按 `auto`。
- `providers: []` 是合法状态：删掉最后一个连接后磁盘就是空列表，服务照常启动且保持「未选择」。旧版本把空列表判为损坏，这一条已经放开。
- 字段拼错、缺 `name`/`base_url`、连接名重复仍然立即报错：配置错了应该当场知道。

`selection_mode` 表达空选择的持久化语义：

| 值 | 含义 |
|---|---|
| `auto` | 没有显式选择，按启动默认规则解析（旧配置的默认） |
| `explicit` | 使用 `active_provider` + `active_model` |
| `none` | 作者明确清空，重启后仍然未选 |

## 4. 凭据

写入顺序（`server/model_connections.py`）：

```text
校验草稿 → 新 key 写入新引用并回读校验 → 写「只含引用」的配置 → 清理被替换的旧引用
```

- 新 key 总是写到一个新引用（`hastings:provider:<连接名>:<随机后缀>`），不会覆盖仍在使用中的旧引用。
- 配置写盘只带 `credential_ref`，`api_key` 为空；明文 Key 只存在于系统凭据存储与运行时内存（`SecretStr`）。
- 任一步失败都保留旧连接可用：新 Key 写入/校验失败时旧配置不动；配置写失败时旧配置与旧凭据都不动。提交后清理旧引用失败只影响存储占用，不会把已经成功的保存说成没保存。
- 新增连接必须填 Key；编辑时省略 `api_key` 表示沿用旧 Key，显式空串被拒绝——删密钥只能通过删除连接。
- 对外只给 `has_key` 与尾四位脱敏值，不返回 `credential_ref`，也不把脱敏值回填进密码框。

凭据存储优先系统凭据管理器。系统存储不可用时降级为进程内存，接口返回 `credential_storage: "session"`，界面提示「仅本次运行有效，重启后需重新填写」；降级不改明文保存。

旧配置里的明文 `api_key` 在启动时迁入系统凭据存储（写入并验证 → 元数据切换 → 清空明文字段），失败则保持旧连接可恢复。`api_key_env` 这类环境变量引用会被拒绝并给出改成直填的提示。

## 5. 选择规则

当前选择由「连接 + 模型 + 来源」组成，来源取值 `saved` / `startup` / `automatic` / `none`。解析优先级：

```text
本次运行中界面作出的选择（含清空）
  → CLI --model 临时覆盖
  → 配置里的 explicit / none
  → 仅 auto 模式下的启动默认规则
```

- `--model` 不落盘：它覆盖已选连接的模型；没有已选连接时才用启动连接。界面一旦修改选择就立即取代它。
- 保存连接不改变当前选择；界面提供独立的「设为当前」与对话栏模型选择器。两者都以写入成功后的配置为准，不先改本地再等结果。
- 切换只影响下一轮：一轮在受理时固定「连接 ID、显示名称、模型、实例」，`turn_started` 事件带上这三个公开字段。受理之后编辑、切换或删除连接都不改变这一轮。
- 修改当前连接时，从可选列表移除正在使用的模型会被拒绝（`MODEL_MODEL_IN_USE`），提示先切换。
- 删除当前连接会进入未选择状态，不自动挑下一个；界面在删除前说明这一点。
- 凭据失效、模型失效或供应商故障保留当前选择并提示修复，不静默换账号、换厂商或重跑创作。
- 列表刷新不删除作者保存的模型；供应商列表这次没返回的模型标记「本次列表未返回」，不据此判定不可调用。

每轮独占一个模型实例：该轮创建、结束时关闭。因此不需要为「正在被使用的连接」维护引用计数，代价是放弃轮次之间的 HTTP 连接复用。

## 6. 模型发现与测试

三件事彼此独立，界面分开显示：

| 操作 | 做什么 | 预算 |
|---|---|---|
| 获取模型列表 | 按协议读 `/models`，处理认证头与分页；未实现列表接口时返回 `unsupported` 并开放手填 | 15 秒、最多 10 页 |
| 响应测试 | 发一句固定短请求，要求返回非空文本 | 30 秒 |
| 工具测试 | 提供只回固定值的 `connection_probe` 工具，验证工具名、参数、结果回传与最终回复 | 总计 60 秒、最多两次模型响应 |

- 三种操作都不读作品、不写文件、不建 Git；一次点击最多产生这几次请求，不额外重试（适配器的自动重试在这几条路径上关闭）。
- 「列表能读到」不等于「模型能调用」，所以响应测试必须真的发一次模型请求，不能用 `/models` 返回 200 替代。
- 没产生工具调用记为本次未通过（`MODEL_TOOL_NOT_CALLED`），不上升为「该模型永远不支持工具」；测试通过也不保证写作质量或所有工具调用都可靠，工具测试未通过的连接仍可保存，界面写明「尚未验证可用于文件操作」。
- 未保存的草稿也能获取列表和测试：省略 Key 时后端只允许按连接 ID 读取该连接自己的旧凭据，前端不能指定任意凭据引用。
- 认证失败、限流、地址不存在、网络故障、超时各有独立错误码，不会都降成「供应商没有模型」。
- 测试结果绑定本次地址、协议、凭据与模型，相关字段变化后界面回到「未测试」；结果只在当次页面内保留，重启后显示未测试。

## 7. 接口

应用级路由（`server/model_api.py`），不挂在作品目录之下：

| 方法与路径 | 行为 |
|---|---|
| `GET /api/model-providers` | 供应商预设 |
| `GET /api/model-connections` | 脱敏连接列表 + 有效选择 + 配置版本 |
| `POST /api/model-connections` | 新增连接；不改变当前选择 |
| `PATCH /api/model-connections/{name}` | 更新元数据与可选 Key |
| `DELETE /api/model-connections/{name}` | 删除连接；若是当前选择则清空 |
| `POST /api/model-connections/discover` | 对已保存连接或草稿获取模型列表 |
| `POST /api/model-connections/probe` | 响应测试或工具测试（`kind`：`response` / `tool`） |
| `GET /api/model-selection` | 当前选择、来源与可用原因 |
| `PUT /api/model-selection` | 显式选择或清空（`connection_id: null`） |

- 所有响应带同一个配置 `revision`（配置文件内容的短摘要）。写操作要求 `If-Match`，缺失或过期返回 409 `MODEL_CONFIG_CONFLICT`，且不写盘。
- 保存、删除、选择返回「更新后的脱敏列表 + 有效选择 + 版本」，前端一次应用，不在两次请求之间显示「新连接配旧选择」。
- 发现与测试不改配置版本，响应标注它们针对哪个连接（草稿为 `null`）。
- 请求与响应是两套 DTO，字段显式列出，不直接倾倒内部连接对象。

单进程内写操作串行；不支持多个服务进程同时编辑一份配置。

## 8. 失败与错误码

| 错误码 | 何时出现 |
|---|---|
| `MODEL_NOT_CONFIGURED` | 发起一轮时没有可用连接或模型；在受理时确定，记为这一轮失败 |
| `MODEL_CALL_FAILED` | 厂商报错（端点、key、模型名不匹配） |
| `MODEL_CONFIG_CONFLICT` | 写操作带的版本与磁盘不一致 |
| `MODEL_CONFIG_WRITE_FAILED` | 配置文件写入失败 |
| `MODEL_CONNECTION_NOT_FOUND` | 连接名不存在 |
| `MODEL_CONNECTION_INVALID` | 地址不是 http(s)、协议不支持 |
| `MODEL_CREDENTIAL_REQUIRED` | 新增连接没填 Key，或提交了空 Key |
| `MODEL_CREDENTIAL_FAILED` | 新 Key 写入或回读校验失败 |
| `MODEL_MODEL_REQUIRED` | 连接没有任何可选模型 |
| `MODEL_MODEL_INVALID` | 模型不在连接的可选列表里 |
| `MODEL_MODEL_IN_USE` | 试图移除正在使用的模型 |
| `MODEL_AUTH_FAILED` / `MODEL_RATE_LIMITED` / `MODEL_ENDPOINT_NOT_FOUND` / `MODEL_PROVIDER_ERROR` / `MODEL_NETWORK_FAILED` / `MODEL_TIMEOUT` | 发现与测试的失败原因，分别对应 401/403、429、404、5xx、连接失败、超时 |
| `MODEL_EMPTY_RESPONSE` / `MODEL_TOOL_NOT_CALLED` / `MODEL_TOOL_MISMATCH` | 响应为空、模型没发起工具调用、调用了预期之外的工具 |
| `MODEL_DISCOVERY_UNSUPPORTED` | 该服务没有可用的模型列表接口，需要手填 |

错误信封沿用文件工作区的形状（`code / where / why / fix / details`）；响应与日志不含完整 Key、认证头或可能回显 Key 的厂商原始内容，Google 地址里的 `key` 参数同样不进日志。

## 9. 装配

`assemble_server()` 建立一个连接管理服务，同时交给设置路由与写作模型工厂（`server/file_app.py`）。设置页保存的连接就是下一轮实际调用的连接，不存在第二份配置。

`create_workspace_app()` 的两个参数分开命名：`workspaces` 是作品登记，`model_service` 是模型连接服务。

`scripts/check_model.py` 走同一条装配路径：读配置 → 装回系统凭据 → 按协议构造实例 → 发一句请求 → 关闭实例，用于不启动写作台就核对「模型到底通不通」。

## 10. 当前缺口

- **真实供应商覆盖有限**：DeepSeek 已完成发现、响应、工具与一轮写作的真实验证；其余预设的协议契约由受控传输验证，真实服务未逐家验证。
- **没有流式展示**：回复整条显示。
- **不支持多进程同时编辑配置**：并发保护只在单进程内，另加提交前的版本核对。
- **没有连接级高级参数**：供应商特有的兼容开关、推理强度、上下文长度都不在本次范围，模型能力不做推断。

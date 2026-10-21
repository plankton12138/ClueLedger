# Hastings 0.4 供应商连接开发计划

> 编写日期：2026-10-21。状态：V04-9A～9D 已实施。本文保留交付范围与验收口径；当前行为见[模型连接](modules/model-connections.md)，改动与验证证据见[阶段记录](notes/v04-9-model-connections.md)。
> 范围依据：本轮讨论确认先补齐云端供应商连接，暂不开发本地模型。
> 现状入口：[后端导航](../../src/hastings/README.md)、[模型连接](modules/model-connections.md)、[写作 Agent 与对话](modules/agent-and-chat.md)、[写作台界面](modules/desk.md)。

## 1. 目标与范围

作者应能在界面中选择供应商、填写 API Key、选择模型，随后直接开始讨论或写作，无需编辑配置文件。接入过程需要说明哪里尚未配置、哪里验证失败，以及当前一轮实际使用哪个模型。

采用「供应商 → 连接 → 模型」的结构：供应商提供接入预设，连接保存作者自己的账号与地址，模型是该连接可以调用的具体标识。常见供应商默认填好接入参数，自定义服务允许手填；配好后，日常操作只需选择「连接 / 模型」。

### 1.1 本次交付

- 可搜索的供应商列表，以及自定义服务入口。
- 新增、查看、编辑、删除连接；同一供应商允许多个连接。
- 获取模型列表、手动添加模型、维护该连接的可选模型与默认模型。
- 分别测试模型响应和工具调用；测试不读取作品，也不修改作品。
- 选择当前连接与模型，保存后无需重启；刷新和重启后保留选择。
- 密钥只写入系统凭据存储或已有的内存降级存储，接口只返回是否已配置及脱敏信息。
- 从界面连接真实供应商，到一轮写作、文件落盘与 Git 记录的验证闭环。

### 1.2 明确不做

本地运行程序安装、模型下载、硬件检测、Ollama/LM Studio/llama.cpp 管理；订阅账号登录、OAuth；自动切换供应商、失败后跨供应商重试；多模型协作与任务路由；价格比较、余额查询与模型排行榜；token 级前端流式展示。

已有配置中的本地地址与 `no_auth` 字段继续兼容，不新增本地模型入口，也不趁本次清理这些档案。配置、会话与凭据不进入作品目录或作品 Git 历史。

## 2. 已核对的基础与缺口

以下是 2026-10-21 对当前源码的核对，不把旧版模型页面或 Tau 的独立界面视为 Hastings 已有能力。

| 模块 | 当前基础 | 本次需要补齐 |
|---|---|---|
| `server/provider_config.py` | 多档案、当前选择、配置原子写入、密钥迁移与运行实例构造 | 供应商预设、显示名称、多个可选模型、连接变更的一致性 |
| 模型适配 | Hastings 只构造 OpenAI 风格 Completions / Responses provider | 接入仓库内已有的 Anthropic、Google 适配器，统一返回模型契约 |
| `server/file_app.py` | 只挂文件工作区路由，按当前档案提供模型 | 挂载应用级模型管理接口，并与写作轮次共用同一配置服务 |
| 凭据存储 | 系统存储优先、不可用时内存降级 | 对外说明保存期限；密钥替换失败后保留原连接 |
| 写作轮次 | 在后台执行阶段获取 provider 与模型 | 在受理轮次时固定模型配置，避免切换与编辑影响已受理请求 |
| 前端 | 顶栏与对话栏可挂入口，无模型状态或设置界面 | 设置对话框、模型选择器、错误与未配置引导 |

还需处理两个已有边界：目前配置文件显式写入 `providers: []` 会被判损坏，删除最后一个连接前必须改为合法空配置；目前 provider 缓存失效只移除引用，连接热更新需要一并明确实例关闭的归属。

Tau 的供应商目录、登录界面与本地管理位于 `src/tau_coding/`，但 Hastings 的安装包只包含 `tau_ai`、`tau_agent` 与 `hastings`。本次参考 Tau 的分层与预设资料，不让运行时依赖未打包的 `tau_coding`，也不修改上游 Agent 循环。

## 3. 产品行为

### 3.1 入口与首次配置

顶栏新增「模型设置」，没有打开作品时也能使用。对话栏顶部显示当前「连接名称 / 模型」，点击后切换，列表末尾提供「管理连接」。

没有模型时，输入区提示「连接模型后开始对话」，提供设置入口；若已有草稿则保留。配置完成后由作者点击发送，不自动补发旧请求。后端继续保留未配置模型时的明确错误，不能只依靠前端按钮限制。

新增连接分三步：

```text
选择供应商 → 填写连接 → 选择模型并保存
```

供应商列表支持搜索；连接表单默认只显示连接名称和 API Key，预设地址可查看，地址与接口类型在高级设置中修改。需要手动选定服务区域的供应商，在对应预设中明确提供区域选项，不自动猜测地区。

模型页提供「获取模型列表」与「手动添加模型」。作者勾选日常可用模型，并选一个默认模型。获取失败时保留已有勾选和手填入口；认证或网络失败必须显示原因，不能显示成「供应商没有模型」。

「测试响应」「测试工具调用」都是作者主动执行的操作，旁边简短说明会发出少量测试请求。保存允许保留未测试的连接，不把网络临时故障变成无法保存配置。

### 3.2 日常选择

- 保存连接不自动改变当前选择；新增完成后提供独立的「设为当前模型」操作。
- 当前选择作用于本机应用的后续轮次，切换作品不另设默认模型；本次不加入作品级、会话级覆盖。
- 切换成功以后端返回为准，再更新界面。运行中的轮次显示其已固定的连接与模型；切换只影响下一轮。
- 修改当前连接的默认模型，不覆盖当前明确选中的模型；从可选列表移除当前模型时拒绝保存，提示先切换。
- 允许删除当前连接：界面先说明会清空当前选择；删除成功后进入未选择状态，不自动选择其他连接。
- 失效凭据、失效模型或供应商故障保留当前选择并提示修复，不静默换账号、换厂商或重跑创作。
- 列表刷新不删除作者保存的模型。供应商返回列表不再包含某个模型时标记「本次列表未返回」，不据此直接认定不可调用。

### 3.3 状态与失败

连接已保存、凭据已配置、响应测试通过、工具测试通过是四种不同事实。设置界面分别显示，不能用一个绿色「已连接」代替全部状态。测试结果绑定本次地址、协议、凭据版本和模型，相关字段变化后失效；本次仅在内存保留最近结果，重启后显示未测试。

工具测试未通过的连接仍可保存，但结果应写明「尚未验证可用于文件操作」。本次不增加无文件工具的另一套聊天模式，也不以测试通过保证模型写作质量或所有工具调用都可靠。

## 4. 供应商与协议边界

首批计划覆盖以下 API Key 接入方式。它们是本阶段目标，并非当前已验收名单。

| 供应商预设 | 目标调用方式 | 模型获取策略 |
|---|---|---|
| OpenAI | Responses；高级设置允许选择 Completions | 经核实的列表接口 + 手填 |
| DeepSeek、通义千问、Moonshot、智谱、OpenRouter | 对应服务的 OpenAI 风格 Completions | 有列表接口则发现，否则手填 |
| Anthropic | Messages | 经核实的列表接口 + 手填 |
| Google Gemini（API Key） | Google 原生接口 | 经核实的列表接口 + 手填 |
| 自定义服务 | 以上四类已实现协议，由作者明确选择 | 对应协议的发现策略 + 手填 |

预设是对账号所连接的服务进行分类，不按模型的原作者归类。例如第三方平台提供的某个模型仍属于该平台连接。每个连接保存完整的有效地址与协议，不能仅保存供应商名称后在运行时隐式推断。

开发第一个子阶段核对供应商官方文档与仓库适配器：默认地址、区域、认证、模型列表分页、工具结果回传格式。Tau 的目录只作起点；不直接复制其全部模型清单、环境变量约定、订阅入口或过期默认模型。尚未核实的地址不靠拼接域名补齐。

预设地址和来源链接随 Hastings 发布；运行时不自动刷新第三方目录，不后台探测所有供应商。作者点击获取列表时才发请求，返回的模型 ID 原样保留。没有信息时不推断上下文长度、价格或工具能力。

## 5. 模块设计与示例

以下代码说明拟定的边界与关键行为，不是已实现代码；实际命名可随现有结构小幅调整。

### 5.1 供应商目录：只提供接入预设

新增 `src/hastings/server/provider_catalog.py`，目录由后端提供，前端不复制一份供应商表。预设与作者连接分开，因此同一预设可以创建多个账号连接。

```python
from dataclasses import dataclass
from typing import Literal

ProviderApi = Literal[
    "openai-completions", "openai-responses",
    "anthropic-messages", "google-generative-ai",
]

@dataclass(frozen=True)
class ProviderPreset:
    id: str
    display_name: str
    api: ProviderApi
    base_url: str
    docs_url: str
    discovery: str | None  # 已实现的列表读取策略；None 表示手动添加
```

供应商特有的地址、认证和兼容参数由明确的预设/适配配置负责，不让界面上传任意内部兼容参数或系统凭据引用。

### 5.2 配置与凭据：保留一个事实来源

扩展 `provider_config.py`，继续读写启动时实际使用的 `config.yaml` 或 `--config` 路径。设置页是该配置的编辑界面，不另建模型数据库、前端持久化副本或环境变量入口。

保留 `providers`、`active_provider`、`active_model` 以兼容已有配置。`ProviderProfile.name` 继续作为稳定连接标识；新建时生成，界面改名只改 `display_name`。既有的 `catalog_id` 标识供应商预设，未知值按自定义服务展示，不阻止读取。

```python
class ProviderProfile(BaseModel):
    name: str                         # 不因改显示名而变化
    display_name: str = ""             # 旧档案回退到 name
    catalog_id: str = ""               # 旧档案可为空
    base_url: str
    api: ProviderApi = "openai-completions"
    models: list[str] = Field(default_factory=list)
    default_model: str = ""
    api_key: SecretStr = SecretStr("")
    credential_ref: str = ""
    no_auth: bool = False              # 仅兼容已有档案
```

旧档案没有 `models` 时，读取视图把非空 `default_model` 作为首个可选模型，不在启动时批量改写。新保存的连接要求模型 ID 非空、去重，默认模型属于可选列表。供应商分类不从连接名称或模型 ID 猜测；作者可以在编辑时补选预设。

密钥更新规则：新增需要非空 Key；编辑时不提交 `api_key` 表示保留，显式空串或 `null` 拒绝，删除密钥通过删除连接完成。脱敏值不能回填进密码框，更不能当作新 Key 提交。响应不返回 `credential_ref`，也不返回 Pydantic 对象中可能携带的 SecretStr。

新增 `model_connections.py` 作为配置变更服务，串行执行写操作，并以文件内容版本检查拒绝覆盖外部编辑。具体保存顺序如下：

```python
async with self.write_lock:
    self.require_revision(expected_revision)
    candidate = self.validate_edit(command)
    # 新引用与旧引用不同，避免写配置前覆盖仍在使用的旧密钥。
    staged = self.stage_and_verify_key(command.api_key)
    try:
        persisted = self.with_secret_reference(candidate, staged)
        save_server_config(persisted, self.config_path)
    except Exception:
        self.discard_staged_key(staged)
        raise
    self.publish(persisted)  # 提交后发布内存快照，不再进行可能失败的凭据读取
    self.cleanup_replaced_key_after_commit(candidate)
```

提交前失败保留旧配置、旧凭据和旧选择。提交后清理旧凭据失败只报告清理未完成，不把已经成功的保存说成未保存。删除先保存不再引用旧凭据的配置，再清理凭据。

配置写盘只包含凭据引用；不能把已物化到内存的 Key 交给现有序列化器。内存降级时返回 `credential_storage: "session"` 并显示「仅本次运行有效，重启后需重新填写」；系统存储返回 `"system"`。降级不改成明文保存。

删除最后一个连接允许持久化 `providers: []`。空选择需要有明确持久化语义：向后兼容地新增可选字段 `selection_mode: auto | explicit | none`；旧配置有 `active_provider` 按 explicit 读取，否则按 auto；删除当前连接或主动清空时保存 none，重启不再回到旧的首个可用档案。

### 5.3 模型运行：统一协议，固定每轮选择

新增 `provider_runtime.py`，将供应商实例构造从配置序列化中分开。复用 `tau_ai` 的现有适配器，返回 `ModelProvider` 的可关闭扩展契约，不再把返回类型写死为 `OpenAICompatibleProvider`。

```python
class ManagedModelProvider(ModelProvider, Protocol):
    async def aclose(self) -> None: ...

def build_provider(profile: ProviderProfile) -> ManagedModelProvider:
    if profile.api == "anthropic-messages":
        return AnthropicProvider(anthropic_config(profile))
    if profile.api == "google-generative-ai":
        return GoogleGenerativeAIProvider(google_config(profile))
    return build_openai_compatible(profile)
```

配置构造显式传入档案解析出的 Key、地址与协议，不调用 Tau 的环境变量配置函数；保留现有本机请求不经代理的兼容行为。供应商特有参数按已核实的适配器契约传入。

在 `TurnService.start()` 受理时捕获不可变的模型快照，之后 `_run()` 只使用该快照。快照的凭据只在内存持有，排除 repr，不写事件和接口；公开事件只增加连接 ID、显示名称与模型 ID。未配置时仍沿现有轮次失败路径返回可操作的错误。

首版采用每轮独占 provider：该轮创建并在 finally 关闭，连接编辑与删除只影响后续快照。这样不需要为当前连接缓存增加跨轮次引用计数，代价是放弃轮次之间的 HTTP 连接复用。本阶段接受这一开销。

```python
# start() 在创建后台任务之前固定 snapshot；此处是 _run() 内部。
async with runtime.open(snapshot) as provider:
    harness = build_harness(
        provider=provider, model=snapshot.model,
        workspace=self.workspace, run=run, max_turns=self.max_turns,
    )
    await self._consume(harness, info, text)
```

测试与模型列表请求也使用短生命周期实例或客户端，请求结束关闭。同步修改 `scripts/check_model.py`，继续走与产品相同的配置解析和实例构造路径。假 provider 的关闭能力由测试适配封装，不修改上游基础协议。

选择优先级明确为：本次运行中作者通过界面作出的选择（包括清空）→ 显式 `--model` 启动覆盖 → 配置内 explicit/none 状态 → 仅 auto 模式下的原启动默认规则。`--model` 是临时覆盖，不落盘；界面一旦修改选择就立即取代它。只传 `--model` 时，覆盖配置已选连接的模型；没有已选连接时才使用原启动档案。界面返回当前有效来源，避免显示的模型与实际调用不一致。

### 5.4 模型发现与测试：请求成功不等于 Agent 可用

新增 `model_probe.py`，提供列表读取与两种独立测试，复用正式运行的适配器构造，不另写一条简化的模型请求链路。

- **模型发现**：按预设策略处理认证、分页和返回格式；限制请求时长与分页次数，结果包含是否完整。未实现列表接口返回 `unsupported` 并开放手填；认证、限流、网络等错误保留独立原因，不能都降为不支持。
- **响应测试**：发送固定短句，要求返回非空文本；这才验证选定模型的调用路径，不能用 `/models` 返回 200 替代。
- **工具测试**：提供一个只返回固定值的 `connection_probe` 测试工具，验证工具名、参数、工具结果回传与最终响应的完整往返。只执行白名单内该工具，最多两次模型响应，不加载写作工具、不建立作品、不写 Git。

测试预算建议：模型发现 15 秒；响应测试 30 秒；工具往返总计 60 秒；不开额外重试，关闭适配器默认自动重试，避免一次点击产生不可见的重复调用。超时、限流、拒绝工具请求、未产生工具调用分别返回明确结果。没产生工具调用记为本次未通过，不能直接断言该模型永远不支持工具。

```python
@dataclass(frozen=True)
class ProbeResult:
    connection_id: str | None
    model: str
    response: Literal["passed", "failed", "untested"]
    tool_call: Literal["passed", "failed", "untested"]
    code: str | None
    message: str  # 面向作者的简短结果，不附原始请求或厂商完整响应
```

未保存的草稿也能获取列表和测试，不创建临时连接、不切换当前模型。编辑草稿省略 Key 时，只能凭该连接 ID 由后端读取它自己的旧凭据；前端不能指定任意凭据引用。返回错误沿用 `code / where / why / fix / details`，过滤 Key、认证头与可能包含 Key 的厂商回显；Google URL 中的认证参数同样不得进入日志。

### 5.5 HTTP 接口与应用装配

新增 `model_api.py`，使用应用级 `/api/model-*` 路由，不挂到作品目录之下，不恢复旧业务数据库。以下接口为本计划目标，当前应用尚未提供。

| 方法与路径 | 行为及前端调用者 |
|---|---|
| `GET /api/model-providers` | 供应商预设；新增连接的供应商选择页 |
| `GET /api/model-connections` | 脱敏连接列表与配置版本；设置列表、模型选择器 |
| `POST /api/model-connections` | 保存新连接；新增表单 |
| `PATCH /api/model-connections/{name}` | 更新元数据与可选 Key；编辑表单 |
| `DELETE /api/model-connections/{name}` | 删除连接，必要时清空当前选择；列表删除操作 |
| `POST /api/model-connections/discover` | 对保存连接或草稿获取模型；模型选择页的获取按钮 |
| `POST /api/model-connections/probe` | 对保存连接或草稿执行指定测试；两个测试按钮 |
| `GET /api/model-selection` | 返回当前有效选择、来源与可用原因；初始化和刷新 |
| `PUT /api/model-selection` | 显式选择或清空；对话栏模型选择器及设为当前操作 |

列表与选择响应都带同一配置 `revision`。配置写操作要求 `If-Match`，冲突返回 409 `MODEL_CONFIG_CONFLICT`，前端保留表单并要求重新读取后再提交。服务内串行处理写操作，并在提交前核对磁盘版本；本次不宣称支持多个服务进程同时编辑一份配置。

保存、删除与选择响应包含更新后的脱敏列表、有效选择与版本，前端一次应用，避免再次请求期间显示新连接配旧选择。发现和测试不改变配置版本，响应标注对应草稿或连接版本；过期结果不覆盖新表单状态。

```json
{
  "revision": "<配置版本>",
  "selection": {
    "connection_id": "conn_example",
    "model": "<作者选定的模型 ID>",
    "source": "saved",
    "available": true
  },
  "connections": [{
    "name": "conn_example",
    "display_name": "我的连接",
    "catalog_id": "deepseek",
    "api": "openai-completions",
    "base_url": "<实际服务地址>",
    "models": ["<作者选定的模型 ID>"],
    "default_model": "<作者选定的模型 ID>",
    "has_key": true,
    "masked_key": "…abcd",
    "credential_storage": "system"
  }]
}
```

请求与响应使用不同 DTO，所有响应字段显式列出，禁止直接 `model_dump()` 内部档案。路径中的稳定连接名由客户端编码。

`assemble_server()` 建立一个连接管理服务，将它同时交给设置路由与写作模型工厂。`create_workspace_app()` 的作品登记与模型服务使用不同参数名，避免当前两个 `book` 概念混在一起。设置页、测试与正式写作必须读到同一配置快照。

### 5.6 前端：设置与日常选择分开

新增 `frontend/src/api/models.ts`、`frontend/src/state/models.ts`；组件放在 `components/models/`，包括 `ModelSettingsDialog`、`ProviderPicker`、`ConnectionForm` 与 `ModelSelector`。分别接入 `WorkspaceShell.tsx` 顶栏和 `ConversationPanel.tsx`。

模型设置是应用级状态，不耦合作品打开/关闭。初始化和窗口重新获得焦点时只读取本机配置状态，不自动调用供应商测试。当前轮次的模型信息来自事件快照，不由全局选择倒推。

```typescript
type ModelSelection = {
  connection_id: string | null;
  model: string;
  source: "saved" | "startup" | "automatic" | "none";
  available: boolean;
  reason?: string;
};

// 表单组件内持有；不进入全局状态或浏览器存储。
const [apiKey, setApiKey] = useState("");
const keyPatch = apiKey.length ? { api_key: apiKey } : {};
// 编辑时省略即保留；新增时表单及后端都要求非空。
```

全局状态仅保存脱敏连接、当前选择、配置版本与页面状态，不保存 Key、不使用浏览器持久化模型配置。取消、成功保存和关闭表单时清空 Key；普通校验或网络失败保留当前草稿，允许修正重试。

表单切换供应商时重新应用该供应商预设；已有连接改供应商需重新选模型并使测试结果失效。供应商变更不静默携带旧 Key 给新的地址。列表刷新与测试使用请求序号或中止信号，防止慢响应覆盖作者已改过的地址、Key 或模型。

API 错误按稳定错误码分支，表单就地显示原因。新增、编辑、删除、模型发现、测试和切换各有自己的等待状态，不用全页 loading 阻断阅读。沿用现有对话框、表单与颜色规范，包含键盘返回、焦点管理、密码框、可读标签。

## 6. 实施顺序

本次作为 V04-9，分四个子阶段顺序实施；每一步均以实际行为和证据结束，不以接口或按钮数量判定完成。

| 子阶段 | 工作内容 | 结束条件 |
|---|---|---|
| V04-9A：配置与协议 | 核对官方接入资料；建立供应商目录；兼容扩展配置；接入四种协议；明确选择规则 | 旧配置可读，新配置往返一致；各协议的请求与工具消息契约通过受控服务验证；安装包不依赖 tau_coding |
| V04-9B：管理与运行 | 凭据分步提交；连接管理、发现、测试、选择接口；装配同一服务；轮次快照和实例关闭 | 接口操作能改变下一轮真实装配；失败保留旧连接；受理后的轮次不受切换/编辑/删除影响 |
| V04-9C：设置界面 | 供应商选择、表单、模型发现与手填、测试结果、连接管理、日常模型选择、未配置引导 | 真后端 + 受控供应商完成新增、编辑、切换、删除、刷新、重启与错误恢复，无占位按钮 |
| V04-9D：真实验证与文档 | 经界面连接真实供应商并创作；补齐运行说明与模块契约，保存本阶段证据 | 明确区分受控测试覆盖与真实供应商覆盖；记录章节文件、工具过程与 Git 提交；完成文档导航检查 |

不为排期估算先改运行代码或作者现有 `config.yaml`。实际开发记录建立 `notes/v04-9-model-connections.md`；验证材料放入 `artifacts/`，只写本阶段改动、取舍、结果与缺口。

## 7. 验收与验证范围

### 7.1 必须覆盖的场景

| 类别 | 有意义的验证 |
|---|---|
| 老配置与空配置 | 旧档案仍可调用；不读环境变量；缺配置可启动；删掉最后一个连接后可重启 |
| 连接与选择 | 同供应商多连接；改名不换身份；保存不自动切换；清空重启后保持未选；--model 的临时覆盖和界面优先规则 |
| 凭据与失败恢复 | 新增/替换成功；新 Key 写入或校验失败；配置写失败；旧凭据清理失败；系统存储降级；所有响应与日志不含完整 Key |
| 外部与并发变更 | 两个页面同时提交；手工改配置后拒绝旧版本覆盖；慢发现/测试响应不覆盖新草稿 |
| 协议契约 | 四类协议的认证、路径、工具参数、结果回传、最终响应；超时与限流；供应商差异不由品牌名称猜测 |
| 模型发现与诊断 | 分页、合法空列表、未支持发现、401/403、404、429、5xx、网络故障；手填可用；无工具调用与工具往返失败可区分 |
| 执行隔离 | 测试不读取作品、不写文件和 Git；受理轮次后修改/删除连接仍用原快照；完成、失败、停止均关闭本轮实例 |
| 页面与真实接线 | 无作品时可设置；输入草稿不丢；刷新/重启保留选择；保存失败不显示成功；切换后下一轮确实使用新模型 |

后端用临时配置、内存凭据和受控 HTTP 服务验证失败路径与协议，不把真实付费调用放进普通测试。前端组件测试覆盖表单、状态与过期响应；浏览器走查使用真实 Hastings 后端与受控供应商，不能只拦截 Hastings 自身 API 后宣称接线完成。

### 7.2 检查命令

拟新增的后端测试归入 `tests/workspace/test_model_*.py`，沿用当前回归目录；补充 `test_provider_config.py`、`test_entry.py`、`test_service.py` 与相应前端测试。

```powershell
# 仓库根目录
uv run pytest tests/workspace -q
uv run ruff check src/hastings tests/workspace
uv run mypy src/hastings

# frontend 目录
npm test
npm run build
npm run test:e2e
```

检查只在实施后执行，本文不记录任何预先通过数。必要时更新 `scripts/check_model.py` 的参数和关闭行为，并单独验证。

### 7.3 真实供应商证据

至少使用一个作者已有凭据的供应商，经界面完成「新增或编辑连接 → 获取或手填模型 → 测试 → 设为当前 → 发起写作 → 查看新章节与 Git 提交 → 重启后继续使用」。使用独立测试作品，不在真实作品中做取证。

各协议均须有受控服务验证；真实验证只覆盖实际持有凭据、实际执行过的供应商与模型。未持有凭据的厂商写明「适配契约已验证，真实服务未验证」，不能借一个兼容供应商通过推断整张表已真实通过。真实调用失败时保留失败证据与缺口，不用历史验证材料替代本次接线。

## 8. 文档交付

实施完成后新增 `modules/model-connections.md`，整理供应商预设、配置、凭据、接口、选择规则与失败行为；更新模块导航和后端导航。当前模块说明在实现之前继续保持「无模型设置界面」的事实。

同步改写 `modules/agent-and-chat.md` 的模型快照与事件、`modules/desk.md` 的设置/选择入口、`release.md` 的配置方式与验证范围、`frontend/README.md` 的当前接线、`config.example.yaml` 的兼容示例，以及 `scripts/check_model.py` 的使用说明。统一清除与本阶段直接相关的过期描述：旧模型路由并未挂载、模型不读环境变量、当前启动支持 --config/--model；其他历史文档不回改。

本计划与供应商支持表只表示目标。功能交付以后，当前行为以模块说明为准，阶段证据留在 notes/artifacts；导航仅提供查找入口，不复制多份进度表。

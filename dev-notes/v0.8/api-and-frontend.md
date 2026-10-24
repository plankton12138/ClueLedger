# 0.8 接口、工具与前端契约

日期：2026-10-24。状态：待实施。下表为拟新增或修改的接口，不代表当前路由可调用。
作品接口继续使用 `/api/file-workspaces/{id}`；新增应用级 `/api/module-library`，明确全局库与作品的权限边界。
接口产品版本不进入 URL。库与绑定的发布结果见[全局模块库契约](library-and-assembly.md)。

## 1. 通用约束

读操作不启动模型。写操作携带 request_key 和观察到的版本；同键同请求幂等，不同请求冲突。
作品 ID 只定位已登记目录；模块条目按 library/module/revision/entry 完整引用解析，不能只凭名称或条目 ID 跨模块匹配。
普通写作接口只能读取本轮 assembly 固定版本及显式参考，不能拿作者模块库 HTTP 权限当 agent 的全库权限。
分页游标绑定查询、作品与索引版本；版本变化返回游标过期并要求重新查询，不能悄悄跳过内容。
JSON 字段统一 snake_case；文档中的前端展示类型可用 camelCase，但适配器负责明确转换。
跨文件作品修改仍可能部分完成；不要让前端将 HTTP 200 等同于模型任务成功或 Git 成功。

## 2. 模块库、作品装配与资料接口

下表 G 为全局前缀 `/api/module-library`，W 为作品前缀 `/api/file-workspaces/{id}`。
不提供隐藏入口接口，也不允许 PATCH 已发布版本的正文。

| 接口 | 输入／行为 | 前端调用方 |
|---|---|---|
| `G GET /presets` | 七种已实现类型及支持操作 | 新建模块面板 |
| `G GET /modules` | kind、q、cursor、limit；返回实例和 head 元信息 | 全局模块库抽屉 |
| `G POST /modules` | kind、名称、初始内容、request_key | 新建实例；不自动装入 |
| `G GET /modules/{module_id}/revisions` | 分页不可变版本及父版本 | 版本选择 |
| `G GET /modules/{module_id}/revisions/{revision_id}` | manifest、条目目录、digest | 模块预览 |
| `G GET /modules/{module_id}/revisions/{revision_id}/records/{entry_id}` | 精确版本原文与分段读取 | 详情、编辑基础版本 |
| `G POST /modules/{module_id}/revisions` | expected_head、base_revision、完整变更稿、request_key | 发布新版本，不改任何绑定 |
| `G POST /modules/{module_id}/fork` | 基础 revision、新名称、request_key | 另存变体，生成新 module_id |
| `G POST /modules/{module_id}/trash`、`restore` | 版本校验、可恢复删除／恢复 | 库管理；旧绑定版本仍可读 |
| `W GET /assembly` | assembly digest、bindings、关联和诊断 | 当前作品已装列表 |
| `W PUT /assembly` | 精确模块版本集合、顺序、简单关联、expected_digest、request_key | 装入、升级、卸下；不改库原文 |
| `W POST /module-updates` | 发布命令＋本作品预期绑定版本 | 保存并用于本作品；明确分别返回 published、binding_updated |
| `W GET /records` | module_ids、kinds、q、关联、分页；只读本轮装配或显式参考范围 | 当前作品资料列表与检索 |
| `W GET /bindings/{binding_id}/records/{entry_id}` | 按绑定解析精确模块版本，不自动取 head | 作品资料详情 |
| `W GET /story-events` | order=chapter/story、截止章、include_plans、binding_ids、分页 | 时间线双视图；正文观察不复制 |
| `W POST /material-tasks` | domain=story_index 或既有观察域，固定正文及模块依赖 | 明确整理按钮 |
| `W POST /derive` | 名称、expected_assembly_digest、替换项、request_key | 基于当前组合创建空正文新作品 |
| ~~`W POST /export-package`~~ | 2026-10-24 取消，不提供作品包导出 | — |
| ~~`POST /api/workspace-packages/import`~~ | 2026-10-24 取消，不提供作品包导入 | — |

作者直接编辑模块保存新版本；“保存并用于本作品”一次授权两个步骤，失败时保留已发布版本并可只重试绑定。
派生观察不能经库编辑接口写回共享设定；纠正时保存作者声明的新版本，观察另行核对。
库里删除条目通过新版本表达，不物理修改旧修订；卸下仅更新 assembly，不删除内容。

新作品不强制创建线索或真相文件。旧本地资料在显式导入前仅经 legacy 适配读取；导入成功后按映射切换，避免双读。
truth／plot／timeline 可显式关联相同作者事件，但不同 module_id 下的同名事件不自动归并。
前端时间线读取统一事件视图，不再同时请求新旧时间线两份结果。

作品包导入导出于 2026-10-24 取消：不再提供导出包、导入包、对应路由与前端入口。
仅复制作品目录时缺少全局模块版本的问题由依赖诊断显式提示，不冒充完整作品。

## 3. 灵感接口

| 接口 | 结果 |
|---|---|
| `GET /ideas?q&deleted&cursor&limit` | 当前条目的摘要与 head revision；不返回整段历史 |
| `POST /ideas` | 创建一条灵感；可携带选中文字与会话来源 |
| `GET /ideas/{idea_id}` | 当前修订、digest、来源、使用定位 |
| `PATCH /ideas/{idea_id}` | 编辑；必须给 expected_digest、expected_revision、request_key |
| `POST /ideas/{idea_id}/trash` | tombstone 修订；同样核对版本 |
| `GET /ideas/{idea_id}/revisions` | 分页修订元信息 |
| `GET /ideas/{idea_id}/revisions/{revision_id}` | 精确历史内容 |
| `POST /ideas/{idea_id}/restore` | 指定历史修订，以旧内容创建新 head |
| `POST /ideas/{idea_id}/links` | 作者明确关联或验证后的本轮落实位置；必须有 expected_digest |

作者 HTTP 调用可浏览该作品灵感；agent 不能绕过工具服务直接使用作者 HTTP 权限。
收藏只保存客户端明确选中且服务端能核对的原始消息片段；若消息已不可读，可作为普通笔记保存，来源标为不可验证。
恢复已删除条目使用删除前的目标修订；恢复某个历史删除修订则恢复其删除状态，按钮需说明。

```json
{
  "title": "老板娘认识死者",
  "body": "她一直在保护死者的女儿。",
  "tags": [],
  "expected_digest": "当前条目文件指纹",
  "expected_revision": "rev_02",
  "request_key": "client-generated-key"
}
```

返回分别包含 `saved`、`revision_id`、`digest` 和关系诊断。灵感不走正文 Git，不返回虚假的作品提交成功。
对话参考扩展为判别联合，避免把灵感伪装成作品文件：

```typescript
type DraftReference =
  | { kind: "file"; path: string; observedDigest: string; required: boolean }
  | { kind: "idea"; ideaId: string; revisionId: string; observedDigest: string; required: true }
  | { kind: "record"; libraryId: string; moduleId: string; revisionId: string; entryId: string; observedDigest: string; required: boolean };
```

受理时绑定 record 的完整模块版本、固定 assembly digest，idea 绑定不可变修订；同请求键的快照必须相同。
作者选中的未装模块记录只在本轮可读，不自动装入或开放整个模块库。
不自动发送，且不能为了添加参考清空现有草稿或切换到资料任务专用会话。

## 4. agent 工具

| 工具 | 权限与职责 |
|---|---|
| `list_modules` | 仅本轮已装模块实例及确定版本，不含未装库模块与灵感正文／标题 |
| `search_module_library` | 明确请求挑选／查找模块才有本轮权限；返回有界候选，不自动绑定 |
| `query_records` | 有界查询，执行任务截止章与来源规则 |
| `read_record` | 完整版本引用、实际区间、manifest 校验，接入统一读取账本；拒绝未装且未选记录 |
| `publish_module_revision` | 明确模块写授权、base revision 与 expected_head；新建不可变版本 |
| `fork_module` | 明确另存变体授权，生成新实例；不自动改其他作品 |
| `update_assembly` | 仅专用装配用途，明确授权、expected_digest；提交后结束模型执行，普通写作轮不可用 |
| `search_ideas` | 仅明确登记本轮检索许可后可用；读操作不产生写权限 |
| `read_idea` | 仅选中或授权搜索命中的修订，固定实际输入版本 |
| `save_idea` | 仅明确要求记录／修改灵感时；专用范围校验，禁止普通写文件绕过 |
| `link_idea_use` | 作者明确关联，或本轮目标已实际写入且证据匹配；不凭模型自述 |
| `publish_material_result` | 扩展 story_index 数据；只写本域派生结果，不能创建作者事件 |

作品文件继续经 write_file/edit_file；全局模块内容必须经库服务发布，普通文件工具不扩大路径范围。
复用 task_scope 的作者原话与范围机制，新增有类型的资源项：file、module、assembly；旧 file 项保持兼容。
module 项必须指定 module_id（新建时服务端分配）、允许操作 create/publish/fork、基础版本和 head；
assembly 项限定本作品及允许装卸的精确模块引用。读取许可不隐含上述写权限，也不授权更新其他作品。
前端直接操作提供相同显式命令，UI 与 agent 共用服务校验；不新增逐次采用审批。
整理工具不获得库发布、装配更新、灵感写入或正文修改权限。模块查询中的依赖引用不能旁路本轮可读范围。

## 5. 前端组织

| 位置 | 调整 |
|---|---|
| `state/navigation.ts` | 新增 inspirations 路由；materials 按 binding_id 定位；library 编辑位置按模块精确版本定位；时间线带 order |
| `components/workspace/` | 顶栏三个入口，正文＋对话保留，材料 chips 支持三种 reference |
| `components/ideas/`（新增） | 列表、编辑、详情、历史、最近删除；无模型空状态正常 |
| `state/ideas.ts`（新增） | 版本化保存、条目草稿、冲突、分页与作品切换隔离 |
| `api/ideas.ts`（新增） | 灵感接口、snake_case 映射 |
| `state/moduleLibrary.ts`（新增） | 全局实例、版本、草稿、库搜索与发布结果，不混用作品观察 |
| `state/workspaceAssembly.ts`（新增） | 按作品缓存绑定、digest、装卸和依赖，不存库原文副本 |
| `components/library/`（新增） | 抽屉搜索、预览、装入、版本、变体；长内容在主区域编辑 |
| `components/materials/` | 按实例绑定的模块容器、按 kind 的详情与版本提示；复用依据与任务条 |
| `api/storyMaterials.ts` | 统一记录、事件查询、兼容旧资料读结构 |
| `state/materialTasks.ts` | story_index 整理受理与事件订阅，不借用当前写作 send() |
| `state/chatMessages.ts` | 新材料类型与实际读取记录，不把检索命中当已读 |

拟定路由：

```text
#/workspaces/{id}/writing
#/workspaces/{id}/inspirations?idea={idea_id}&view=active
#/workspaces/{id}/inspirations?view=trash
#/workspaces/{id}/materials/{binding_id}?order=chapter&chapter=8&entry={entry_id}
#/library/modules/{module_id}/revisions/{revision_id}
```

旧 chapters／timeline 路由映射到本作品事件只读视图；glossary 路由映射到本作品专名观察过滤。
不能凭旧 kind 猜测某个实例或自动装入模块；不因旧链接调用模型或生成内容。
旧本地链接走 legacy 只读视图；已卸下绑定的链接提示“模块未装入”，不通过详情路由偷偷读取库内容。
作者可明确重新装入或到模块库预览；库预览属于作者 UI，不代表 agent 获得全库读取权限。

### 5.1 状态与并发

- 草稿按作品和条目隔离。切换作品时迟到响应丢弃；没有保存成功不能清空输入。
- 灵感保存用 pending 状态；冲突后保留本地输入，显示当前版本供复制或比较，不自动覆盖。
- 资料任务仍作品级单模型任务，无后台队列；灵感本的独立文件保存不占用整轮模型锁。
- 作品装配变更和 agent 写作品互斥；库编辑按模块锁独立进行。保存并绑定若只完成发布，分别反馈，不隐式排队。
- 跨作品更新同一个模块 head 由库 CAS 决定，一个成功另一个保留草稿；已固定的作品版本不受影响。
- 页面切换不停止已受理的模型任务。取消弹窗只取消本地选择；停止任务走现有停止接口。
- 参考灵感在运行中被修改时，显示本轮使用的是哪一版，不替换输入；正文文件变更按已有失效规则处理。

### 5.2 交互细节

保存是明确按钮，不每键生成永久版本；未保存离开用现有草稿保护交互。
删除后提供恢复入口，不增加“是否确定”连环弹窗；正文删除显示关联影响；库删除保留已被作品使用的不可变版本，展示已知使用位置。
窄屏详情原位切换，返回保留滚动和筛选；键盘可访问按钮、焦点进入编辑区，错误与状态有文本表达。
不把 schema、digest、source_kind、内部工具名直接放进主要用户流程。

## 6. 错误与执行事件

沿用 `{code, where, why, fix, details}`，message 写成 `[错误码] 位置：现象。建议：…`。

| 错误 | HTTP | 含义 |
|---|---|---|
| `IDEA_STALE_CONFLICT` | 409 | 当前条目版本不匹配；保留输入并重读 |
| `IDEA_REVISION_NOT_FOUND` | 404 | 指定修订不存在，不能回退为当前版本 |
| `IDEA_ACCESS_DENIED` | 403 | agent 未获本轮选中／检索许可 |
| `IDEA_STORE_INVALID` | 400 | 文件损坏，保留原文件，提示恢复或人工修复 |
| `RECORD_ID_CONFLICT` | 409 | 重复 ID，不能唯一定位 |
| `MODULE_HEAD_STALE_CONFLICT` | 409 | 库 head 已变化；保留草稿，选择最新版或另存变体 |
| `ASSEMBLY_STALE_CONFLICT` | 409 | 作品装配清单变化，不覆盖新绑定 |
| `MODULE_REVISION_NOT_FOUND` | 404 | 指定版本缺失，不回退到最新版 |
| `MODULE_ACCESS_DENIED` | 403 | 本轮未装入、未选中且没有库检索许可 |
| `MODULE_CONTENT_INVALID` | 400 | 不可变版本内容与 manifest 不符 |
| `QUERY_CURSOR_STALE_CONFLICT` | 409 | 查询快照变化，需要重新检索 |
| `EVENT_ORDER_CONFLICT` | 409 | 作者提交的时间关系成环，保存前给出冲突链 |
| `CONTEXT_REQUIRED_OVERFLOW` | 400 | 最小任务无法满足预算，说明需要拆分的材料 |

外部文件已有时间环时读取返回带诊断的结果，不因该错误阻断全部页面；API 写入新环才拒绝。
HTTP 403 需在现有异常映射中显式加入，不能依赖错误码后缀得到错误状态。

事件沿用现有轮次序列与订阅：扩展 `state_injected` 的 selected/queried/provided/excluded 字段；
增加 `idea_reference_bound`、`idea_use_linked`、`material_query_completed`、`assembly_bound`、
`module_revision_published` 作为实际执行事实；模块发布和作品绑定分别记录，不伪装为原子成功。
直接 UI 编辑灵感不伪造模型轮次，可记录作者操作活动；操作历史以灵感修订为准，日志缺失不影响恢复。
不新增第二套 SSE、不把内部思考写入资料、不打印凭据或完整未授权灵感。

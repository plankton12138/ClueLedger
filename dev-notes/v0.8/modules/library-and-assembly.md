# 全局模块库与作品装配

状态：**已实现（0.8 B/D）**。运行版本 `0.8.0`。本文只写当前接线行为；设计动机与阶段过程见
[开发计划](../development-plan.md)与 [B 记录](../notes/B-library.md)、[D 记录](../notes/D-assembly.md)。

## 1. 两个层次

- **全局模块库**属于应用，不属于任何作品。同一份人物可以装进两个作品，各作品固定自己使用的版本。
- **作品装配**只记录「用了库里的哪个精确版本」，不复制模块原文。库里的内容始终只有一份。

这样做的取舍：共享修改不会悄悄影响其他作品，但作者需要显式升级绑定，也不会自动跟随库的最新版本。

## 2. 七种预设类型

类型是应用支持的固定能力，实例才是作者内容。同一类型可以创建多份实例；两份同名人物不会自动合并。

| 类型 `kind` | 界面名 | 允许的条目种类 | 作品内整理动作 |
|---|---|---|---|
| `characters` | 人物 | `character` | `characters` |
| `world` | 世界设定 | `world_rule`、`place`、`organization` | 无（作者直接编辑） |
| `clues` | 线索与伏笔 | `clue_plan` | `clues` |
| `truth` | 真相与事件经过 | `truth`、`author_event` | 无 |
| `timeline` | 时间线 | `author_event` | `story_index` |
| `plot` | 剧情安排 | `plot_plan`、`author_event` | 无 |
| `reference` | 自由资料 | `reference` | `glossary` |

未知类型直接报 `MODULE_KIND_UNKNOWN`，不按最接近的类型猜测。

## 3. 库的磁盘布局与版本模型

默认库目录为 `~/Documents/ClueLedger/ModuleLibrary`，可用启动参数 `--library-dir` 指定。

```text
ModuleLibrary/
├─ library.json                  # library_id 与格式版本
├─ staging/                      # 发布中的临时目录；不会被查询读到
└─ modules/<module_id>/
   ├─ module.json                # 当前 head 与元数据
   └─ revisions/<revision_id>/
      ├─ manifest.json           # 条目清单与内容指纹
      └─ entries/<entry_id>.md   # 条目原文
```

- **不可变版本**：发布目录写入后不再改写。同一 `revision_id` 再次出现且内容不同，按
  `MODULE_CONTENT_INVALID` 拒绝。
- **发布顺序**：先写 staging 并核对内容，再整目录移入 `revisions/`，最后在模块锁内更新
  `module.json`。中途崩溃最多留下一个未登记的版本目录，不会被查询读到。
- **CAS**：发布要求 `base_revision` 与当前 head 一致（可另带 `expected_head`）；不一致返回
  `MODULE_HEAD_STALE_CONFLICT`。并发发布会一成功一冲突，不覆盖 head。
- **请求键**：同一请求键重试返回第一次的结果；同键不同内容返回 `REQUEST_KEY_CONFLICT`。
- **变体**：`fork` 基于某个版本另存一个新模块，保留 `entry_id`，用于并行方案或重命名。
- **删除与恢复**：`trash` 标记删除而不是物理删文件；已有绑定仍能读取历史版本。新装候选会排除
  已删除模块，`restore` 可恢复。
- **库缓存**：查询用的索引缓存可重建，不作为权威来源；读条目与发布仍直接读库文件。

## 4. 作品装配清单

装配写在作品自己的 `.clueledger/assembly.json`，初始格式版本为 1：

```json
{
  "schema_version": 1,
  "library_id": "lib_...",
  "bindings": [
    {
      "id": "binding_...",
      "module": {"library_id": "lib_...", "module_id": "module_...", "revision_id": "revision_..."},
      "label": ""
    }
  ],
  "links": [["binding_...", "binding_..."]],
  "requests": {"<request_key>": {"request": "<digest>", "result": {"...": "..."}}}
}
```

硬约束：

- 一个绑定必须写全库、模块、版本三层标识；只写模块名会被拒绝。
- 同一 `module_id` 在一个作品里只能绑定一个版本，否则 `ASSEMBLY_BINDING_CONFLICT`；需要并行安排时
  另存变体。
- 一份装配清单只能使用一个库；混入其他库的模块按 `ASSEMBLY_INVALID` 拒绝。
- 最多 200 个绑定；超出时要求卸下不再使用的模块。
- 写入按文件指纹 CAS；读取后被别人改过返回 `ASSEMBLY_STALE_CONFLICT`，不做三方合并。
- 每次写入带请求键，重试不产生第二份结果；同键不同内容返回 `REQUEST_KEY_CONFLICT`。
- 文件损坏或格式版本不支持时直接报错，**不会**把无法解析的文件当成空装配继续写。
- `.clueledger/` 不属于设定集目录，普通文件工具不会把装配清单当作可编辑正文。

## 5. 装配服务与 HTTP 接口

| 能力 | 接口 |
|---|---|
| 读装配与依赖诊断 | `GET /api/file-workspaces/{id}/assembly` |
| 整份替换装配（装卸、排序、链接） | `PUT /api/file-workspaces/{id}/assembly` |
| 以当前组合派生新作品 | `POST /api/file-workspaces/{id}/derive` |
| 保存并用于本作品（先发布、再绑定） | `POST /api/file-workspaces/{id}/module-updates` |
| 库浏览、发布、变体、删除与恢复 | `/api/module-library` 下的 `presets`、`modules`、`revisions`、`fork`、`trash`、`restore` |

- **写作轮次运行中不改装配**：这一轮已按受理时的版本冻结，装卸返回 `ASSEMBLY_BUSY_CONFLICT`；
  库的发布不受影响。
- **依赖诊断**：装配列表保留每个绑定，问题只作标记，包括 `library_missing`、`library_mismatch`、
  `module_missing`、`revision_missing`、`module_trashed`。目录、检索、详情与引用展开使用同一范围。
- **保存并用于本作品**分两步返回：先发布得到版本，再更新绑定。发布成功但绑定失败时返回
  `published=true`、`binding_updated=false` 与具体错误，只重试绑定，不重复发布。
- **派生新作品**只复制装配：新作品没有正文、灵感、会话与观察记录；同一请求键可安全重试，
  目标目录已存在且不是本次请求建的返回 `DERIVE_TARGET_CONFLICT`，不覆盖已有目录。

## 6. 装卸、发布与作者授权

agent 在写作轮次里可用的模块工具：

| 工具 | 作用 | 授权 |
|---|---|---|
| `list_modules` | 列出本轮装配里的实例与精确版本 | 无 |
| `query_records` | 查询已装条目与本作品观察 | 无 |
| `read_record` | 按完整引用读取条目原文与区间 | 无 |
| `search_module_library` | 检索库里的候选，不装入、不升级 | 范围登记 `library_search`，且作者明确要求 |
| `attach_module` | 装入范围登记过的精确版本 | 范围登记 `module_grants` 的 `attach`；提交后本轮结束 |
| `detach_module` | 卸下范围登记过的绑定 | 范围登记 `module_grants` 的 `detach`；提交后本轮结束 |
| `publish_module` | 发布新版本或新建实例 | 范围登记 `library_publish` |

- 授权来自作者原话中的动词短语（如「装入」「卸下」「发布到模块库」），不是模型的自我声明。
  只读任务拒绝装配与发布授权；重复登记不能扩大已登记的范围。
- 装入必须给出精确的 `module_id` 与 `revision_id`，版本不匹配范围登记时拒绝。
- 卸下按绑定 ID 操作，不按模块名猜；已写进正文的事实不因卸下而消失。
- 发布不隐含装入；要用于本作品需另行登记装配操作。
- 装配写入会发出 `assembly_attached` / `assembly_detached` 事件；发布发出 `module_published`。

## 7. 前端

顶栏「模块库」进入同一个应用级页面，支持精确模块／版本链接，没有作品时仍可浏览、新建和发布。
作品目录内的模块入口携带已登记作品上下文进入同页；装卸、切换版本、排序、旧资料导入和派生需要明确作品。
模块默认预览，点击编辑后显示表单；历史版本可预览或另存变体，不能覆盖最新版本。

编辑器只有“保存新版本”作为主操作，默认不勾选“同时用于〈作品名〉”。发布和作品应用分别记录，
应用失败保留新精确版本，单独重试装配，不再发布；目标绑定已变更时提示冲突。
全部原文区间校验完成才开放编辑，草稿按库、模块和基础版本保留于本次应用会话。
切版本、切作品或返回列表不会丢弃输入，放弃修改只处理当前草稿；刷新前提醒，不承诺刷新恢复。
网络结果未知的同内容重试复用请求键与提交体。版本“最新”标记按模块 head 判断，不依赖列表顺序。

字段清单来自预设接口的 `allowed_fields`，普通控件区分文字、数字与布尔；0、false、未填写分别处理。
现有契约支持标量列表，在高级字段区编辑；未知字段、嵌套对象、非标量列表和损坏JSON保留输入并阻止发布。
这项契约已由作者确认，没有借界面迁移收窄服务端能力。

“作品已装模块”保留装卸、排序与“用当前组合新建作品”；派生版本选择可继续分页，默认沿用当前精确版本。
旧轮次材料在卸下或切版本后继续标为不再适用；导入旧资料仍按已有文件整体导入。
实现与验证见[H1](../notes/H1-frontend-reliability.md)、[H3](../notes/H3-module-editor.md)及[当前验证](../artifacts/frontend-experience/verification.md)。

## 8. 边界

- 不自动升级绑定，也不在打开作品时自动发布或重建。
- 不把模块原文复制进作品；作品目录被单独复制时会提示精确依赖缺失。
- 未装模块不会因名字相似出现在普通检索里；库搜索只是候选。
- 作品包导出导入已取消，不提供隐藏入口。

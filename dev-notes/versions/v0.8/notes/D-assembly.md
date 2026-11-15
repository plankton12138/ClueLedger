# D：作品装配（服务、接口与模块抽屉）

日期：2026-10-24。目标：作品只固定引用库里的精确版本，能装、卸、升级、排序，并能把
「保存并用于本作品」拆成发布与绑定两步分别反馈。本阶段交付服务、接口与模块抽屉；
空正文派生与作品包导入导出随后补。

## 实际改动

### 装配服务（`library/bindings.py`）

- `view()`：返回装配指纹、绑定列表与依赖诊断。诊断项包括 `module_missing`、
  `revision_missing`、`module_trashed`、`library_missing`；正文仍可读，绑定不会被自动改。
- `bind`／`unbind`／`upgrade`／`reorder`／`replace`：都带装配指纹 CAS，写入前统一校验
  「库、模块、版本」完整引用；同一实例同时绑两个版本返回 `ASSEMBLY_BINDING_CONFLICT`。
- `publish_and_bind`：对应「保存并用于本作品」。先发布新版本，再尝试更新绑定，分别返回
  `published` 与 `binding_updated`；发布成功而绑定失败时保留版本、返回错误细节，
  重试绑定走 `upgrade`，不会重复发布。

### 接口（`library/assembly_api.py`）

| 接口 | 行为 |
|---|---|
| `GET /api/file-workspaces/{id}/assembly` | 装配指纹、绑定、关联与依赖诊断 |
| `PUT /api/file-workspaces/{id}/assembly` | 整份替换：精确版本集合、顺序、简单关联、expected_digest、请求键 |
| `POST /api/file-workspaces/{id}/module-updates` | 发布并用于本作品；分别返回两步结果 |
| `POST /api/file-workspaces/{id}/derive` | 用当前组合新建空正文作品，可替换模块版本 |
| `GET /api/file-workspaces/{id}/package` | 导出作品包：正文、灵感、装配与精确模块版本 |
| `POST /api/workspaces/import-package` | 导入作品包；冲突版本隔离，目标目录必须为空 |

`server/file_app.py` 在配置了模块库时挂载这组路由；`library/ids.py` 增加 `bnd_` 绑定标识。

### 派生新作品（`library/derive.py`）

- `POST /api/file-workspaces/{id}/derive`：用当前装配新建一个空正文作品，可带
  `replacements` 替换其中几项模块版本；不复制正文、灵感、会话与观察记录。
- 派生靠目标目录里的 `.clueledger/derive.json` 恢复：同一请求键重试会补齐没写完的装配，
  不会留下第二份半成品；目标目录已存在且不是这次请求建的，直接冲突。
- 替换项必须是已装入的模块且版本存在；引用不完整时不创建目录。
- 抽屉「本作品已装」下面提供「用当前组合新建作品」，创建后登记并切到新作品。

### 作品包（`library/package.py`）

- `GET /api/file-workspaces/{id}/package` 导出 zip：正文、设定集、灵感（含全部修订）、
  assembly，以及所有已绑定的精确模块版本；不含整库与未装入的模块。绑定版本缺失时拒绝导出。
- `POST /api/workspaces/import-package` 导入：按 library/module/revision/digest 校验后写入
  当前库；已有版本不覆盖，同 ID 不同内容隔离成新模块并把装配改指过去；目标目录必须为空。
- 解包先校验全部条目路径：拒绝绝对路径、`..`、反斜杠与符号链接，只接受 `workspace/` 下的
  受管路径、灵感 JSON、assembly 与 `modules/` 下的版本目录。
- 导入标记 `.clueledger/import.json` 记录请求键、模块映射与装配；同一请求键重试继续未完成
  的写入，不产生第二套模块。落盘后与「新建」一样初始化版本记录并登记作品。
- 顶栏「导出包」直接下载 zip；「导入包」选择文件与存放位置后导入并切到新作品。

### 模块抽屉（`frontend/src/components/library/ModuleDrawer.tsx`）

- 顶栏「模块库」入口；抽屉左侧是「本作品已装」、七种类型筛选、搜索、实例列表与已删除过滤，
  右侧是版本选择与条目编辑。长内容在主区域编辑，不在列表里展开。
- 装入、切换版本、卸下都走装配接口；「保存到模块库」与「保存并用于本作品」是分开的按钮，
  后者发布并尝试更新绑定，绑定失败时保留发布结果并在提示条说明可以只重试绑定。
- 另存变体、删除与恢复都有入口；删除需要一次内联确认，不做连环弹窗。
- `state/moduleLibrary.ts` 负责查询、选中版本、读取条目正文与装配；`api/moduleLibrary.ts`
  只做 snake_case 映射与错误转 `WorkspaceApiError`。切换作品后迟到的响应按作品丢弃。
- 编辑只改标题与正文；别名与可筛选字段沿用所选版本的原值，避免在界面里重造一套字段编辑。

## 接口与数据

- 绑定项字段：`id`、（库／模块／版本）完整引用、`label`，外加只读诊断 `kind`、`name`、
  `deleted`、`problems`。
- 装配只写 `.clueledger/assembly.json`，不复制模块原文，也不进作品 Git。
- 冲突映射沿用统一信封：`ASSEMBLY_STALE_CONFLICT`、`ASSEMBLY_BINDING_CONFLICT` → 409；
  `MODULE_REVISION_NOT_FOUND`、`ASSEMBLY_BINDING_NOT_FOUND` → 404；引用不完整 → 400。

## 取舍

- **一次操作一次 CAS**：装卸是人手动作，冲突时保留别人的绑定比自动合并更可解释。
- **同一实例一个版本**：需要两种安排时另存变体；系统不做同实例双版本。
- **升级必须显式**：库里发布新版本不改变任何作品，作者确认后才更新绑定。
- **依赖缺失只提示**：模块删除或版本缺失时保留绑定并标出问题，不拿最新版或同名模块顶替。
- **两步结果分开**：发布成功、绑定冲突时前端保留结果并允许只重试绑定。
- **排序要完整排列**：`reorder` 必须提交当前全部绑定 ID，避免漏掉绑定后悄悄落到末尾。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/f-stage -o tmp_path_retention_count=100` | 734 passed，0 failures |
| 静态检查 | `uv run ruff check src/hastings tests`、`uv run ruff format --check` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 66 个源文件无问题 |
| 前端单元测试 | `npx vitest run --no-file-parallelism` | 163 passed（19 个文件） |
| 前端构建 | `npm run build` | 通过 |
| 浏览器走查 | `HASTINGS_WALKTHROUGH_BASE=… node e2e/walkthrough-v08-library.mjs` | 通过，7 张截图见 `artifacts/walkthrough-v08/` |

新增用例 40 条（后端）＋ 23 条（前端）：

- `tests/library/test_assembly_service.py`（11 条）：精确版本绑定、缺模块与缺版本、同实例
  双版本冲突、升级保留绑定标识且旧版可读、卸载只影响该条、陈旧指纹不覆盖、删除模块
  只标记且旧版仍可读、版本目录移走后标 `revision_missing`、发布并绑定、发布成功但绑定
  冲突后只重试绑定、整条命令重放、两部作品共用模块而绑定互不影响。
- `tests/library/test_assembly_api.py`（7 条）：空装配、PUT 绑定精确版本、陈旧指纹 409、
  同实例双版本 409、module-updates 两步成功、发布失败不动绑定、删除模块只标记。
- `tests/library/test_derive.py`（8 条）：装配复制而正文为空、替换项只影响新作品、
  替换目标必须是已装模块且版本存在、陈旧指纹不建目录、已有目录不覆盖、重试补齐装配、
  不能派生到当前作品里面、同键重试不产生第二份。
- `tests/library/test_assembly_api.py`（新增 3 条派生接口）：登记与空正文、陈旧指纹 409、
  名称非法 400。
- `frontend/src/api/moduleLibrary.test.ts`（10 条）：列表查询参数、新建与发布请求体、
  装配读写、派生请求、两步结果、错误码映射、条目正文去头部。
- `frontend/src/state/moduleLibrary.test.ts`（9 条）：按类型与搜索刷新、包含已删除过滤、
  选中版本读取目录与正文、无 head 不发请求、绑定失败保留发布结果、派生返回新作品、
  装入保留已有绑定、卸载只提交剩余绑定、打开抽屉同时读预设与装配。
- `tests/library/test_package.py`（8 条）：只打包已绑定依赖与作者内容、跨库往返、
  同键重试幂等、同 ID 不同内容隔离、缺依赖阻止导出、路径穿越与符号链接拒绝、
  目标目录非空拒绝。
- `tests/library/test_package_api.py`（3 条）：导出 zip、导入登记与内容恢复、非空目录 409。
- `frontend/src/state/workspace.test.ts`（新增 1 条）：派生后刷新登记但不改变当前作品。
- `frontend/src/api/package.test.ts`（3 条）：导出 Blob、导入 multipart 字段、错误码映射。

走查覆盖：打开抽屉、按类型浏览、选中实例查看版本与条目、装入第二个实例、已装列表更新、
用当前组合派生新作品并确认顶栏能选到它、导出作品包、导入为新作品并看到恢复的章节、
1024 宽窗口布局。

## 后续变更

- 2026-10-24：作品包导入导出按作者要求取消。已删除 `library/package.py`、`library/package_api.py`、
  前端 `api/package.ts` 与导入对话框、顶栏导出／导入入口，以及相关后端与前端用例；
  对应的两张导入截图与走查步骤一并移除。本文前面的作品包实现记录只作为当时的构建记录保留，
  当前版本不提供该功能。

## 未完成

- 未装模块从后续查询与注入中排除、旧会话材料标注「不再适用」：属 F 阶段的检索与装配。
- 模型侧的表单与发布权限（task_scope 的 module／assembly 项）：属 F 阶段。
- 抽屉还没有独立路由（`#/library/...`）：当前是覆盖层；需要深链时再扩导航。
- 条目字段（别名、可筛选字段）在界面里只读，编辑结构化字段留到后续。
- 没有在线模型结论；走查只用确定性数据。

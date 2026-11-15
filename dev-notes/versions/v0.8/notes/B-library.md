# B：全局库、来源与版本基础

日期：2026-10-24。目标：把故事资料从「每部作品一份可变文件」改成应用级库里的多实例与
不可变版本，作品只固定引用版本；库不认识任何作品的生死、回收与披露状态。

## 实际改动

### 库的领域结构（`src/hastings/library/`）

- `presets.py`：七种预设类型（人物、世界设定、线索与伏笔、真相与事件经过、时间线、剧情安排、
  自由资料）。每类给出允许的条目种类、作品内可用的整理动作与头部字段白名单。
- `ids.py`：库、模块、版本、条目四层标识。前三层由服务端分配，条目 ID 由作者写在头部，
  在模块内稳定，改名不改 ID。
- `records.py`：条目＝YAML 头部＋正文。渲染、解析与校验集中在这里：标题、正文、别名与字段
  数量都有上限，未知字段直接拒绝；`alive`、`resolved` 这类作品观察字段进不了库。
- `storage.py`：`library.json`（library_id、格式版本、新建请求结果）、
  `modules/<module_id>/module.json`、`revisions/<revision_id>/{manifest.json, entries/*.md}`、
  `staging/`。写入用临时文件加原子替换；目录替换带短暂重试，避免 Windows 索引或扫描进程
  短暂占用时把发布报成失败。
- `catalog.py`：按精确版本生成结构索引（ID、标题、别名、可筛选字段、字符数、指纹），
  缓存在应用状态目录。缓存缺失、版本清单变化或文件损坏时从磁盘重建；条目内容与清单指纹
  不符按内容损坏报错，不在同一 `revision_id` 下接受新内容。
- `modules.py`：新建、发布、另存变体、可恢复删除、列表、版本列表、按版本读取。
  head 用 CAS 推进；请求键先于 CAS 重放；中断后按 `pending` 记录复用已经写好的版本。
- `api.py`：`/api/module-library` 下的预设、实例、版本、条目读取、变体、删除与恢复接口。

### 作品侧

- `workspace/module_assembly.py`：作品 `.clueledger/assembly.json` 记录
  library/module/revision 与简单关联。写入按文件指纹 CAS，请求键幂等，同一实例不能同时绑定
  两个版本；清单不属于受管正文路径，普通文件工具看不到它。HTTP 与界面在 D 阶段接。
- `workspace/errors.py`：`MODULE_ACCESS_DENIED`、`IDEA_ACCESS_DENIED` 显式映射 403，
  不靠错误码尾缀推断。
- `server/file_app.py`、`server/__main__.py`：挂载库路由；`--library-dir` 指定库位置，
  缺省 `~/Documents/ClueLedger/ModuleLibrary`，首次写入才建库；索引缓存放应用状态目录。

## 接口与数据

| 接口 | 行为 |
|---|---|
| `GET /api/module-library/presets` | 七种类型、允许的条目种类与整理动作 |
| `GET /api/module-library/modules` | kind／q／cursor／limit／include_deleted；分页返回实例与 head |
| `POST /api/module-library/modules` | 新建实例并发布第一版；不自动装入任何作品 |
| `GET /modules/{id}/revisions` | 版本元信息分页，含父版本与条目 ID |
| `GET /modules/{id}/revisions/{rev}` | 该版本的目录：标题、别名、字段、字符数、指纹 |
| `GET /modules/{id}/revisions/{rev}/records/{entry}` | 按区间读取原文，返回实际区间与下一游标 |
| `POST /modules/{id}/revisions` | 发布新版本：expected_head、base_revision、完整变更稿、请求键 |
| `POST /modules/{id}/fork` | 基于某个版本另存变体，生成新 module_id |
| `POST /modules/{id}/trash`、`restore` | 可恢复删除；已绑定的旧版本仍可读 |

错误码沿用现有信封：`MODULE_HEAD_STALE_CONFLICT`／`REQUEST_KEY_CONFLICT`／
`QUERY_CURSOR_STALE_CONFLICT`／`ASSEMBLY_STALE_CONFLICT`／`MODULE_TRASHED_CONFLICT` → 409，
`MODULE_NOT_FOUND`／`MODULE_REVISION_NOT_FOUND`／`MODULE_RECORD_NOT_FOUND` → 404，
`RECORD_*`／`MODULE_KIND_UNKNOWN`／`ASSEMBLY_INVALID` → 400，`LIBRARY_WRITE_FAILED` → 500。

## 取舍

- **版本不可改写、CAS 推进**：共享修改不悄悄影响别的作品；基于旧版本编辑只能重做或另存变体，
  不自动三方合并。
- **请求键在 CAS 之前重放**：网络重试时 head 已经前进，重放要返回上次结果，而不是把成功判成冲突。
- **库只放作者内容**：用类型字段白名单拦住作品状态；作品观察留在作品状态目录。
- **索引与原文分离**：索引只给定位与筛选，正文仍按版本读取并计入读取账本；缓存可丢可重建。
- **目录替换带重试**：宁可多等几十毫秒，也不把偶发的占用报成发布失败。
- **单库、本地**：0.8 不做多库联邦、云同步与多人合并；`library.json` 只定位一个活动库。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/b-stage3 -o tmp_path_retention_count=100` | 672 passed，0 failures |
| 静态检查 | `uv run ruff check src/hastings tests`、`uv run ruff format --check` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 62 个源文件无问题 |

新增用例 38 条：

- `tests/library/test_module_library.py`（17 条）：同类型多实例互不影响、发布追加不可变版本、
  head 与 base 的冲突、请求键重放与拒绝复用、中断后恢复同一版本、变体独立、删除后旧版可读且
  不能继续发布、读取只把返回区间计入账本、篡改内容被拒、索引缓存删除与损坏后重建、
  同类型实例互不影响、作品观察字段被拒、游标绑定查询、条目头部错误、并发发布只有一个成功、
  旧作品打开不写库。
- `tests/library/test_module_api.py`（14 条）：预设、创建与列表、版本目录与按区间读取、
  冲突映射 409、字段校验 400、缺模块 404、变体与删除流程、七种类型各建一份、服务启动按
  `--library-dir` 挂载。
- `tests/workspace/test_module_assembly.py`（7 条）：空清单、写入与指纹变化、陈旧指纹冲突、
  同实例双版本冲突、跨库冲突、请求键重放、损坏文件不当空清单。

## 未完成与偏差

- 发布直接接收完整变更稿；编辑草稿文件随 D 阶段的编辑器落地，本阶段不另存半成品。
- 库发布与作品修改范围（`task_scope`）的权限分离在 F 阶段接 agent 工具时统一；当前库接口
  只被作者侧调用，模型没有库工具。
- 旧资料显式导入、新作品不再强制《线索.md》《真相.md》：随 D 阶段落地。原因是没有模块抽屉时，
  导入与移除都只能在后台悄悄发生，和「没有接线的能力不进可操作导航」冲突。
- 装配清单已能 CAS 读写，但装配 HTTP 与前端仍在 D 阶段；本阶段不宣称作品已能装卸模块。
- 模块锁为阻塞等待，没有超时与重试窗口；作者侧并发很低，排队比超时更符合预期。

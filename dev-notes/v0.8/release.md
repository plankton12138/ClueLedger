# 0.8 运行与验证

日期：2026-10-24。运行版本 `0.8.0`。本文件只记录本次实际运行方式与验证结果；未执行、
只做确定性测试或未实施的项单独列出，不用历史结论替代。

## 启动

```powershell
uv run python -m hastings.server                 # 默认端口 8000
uv run python -m hastings.server --workspace <作品目录>
uv run python -m hastings.server --state-dir <状态目录> --library-dir <模块库目录>
```

前端产物在 `frontend/dist`（`cd frontend && npm run build`）；服务存在该目录时一并托管。
应用状态目录由 `--state-dir` 指定，模块库由 `--library-dir` 指定，默认库目录为
`~/Documents/ClueLedger/ModuleLibrary`。

## 本版行为变化

- 顶栏保留「写作 / 故事资料」，新增「模块库」抽屉：浏览七种类型的全局实例、查看版本、
  另存变体、删除与恢复，并装卸当前作品使用的精确版本。
- 新增应用级模块库与服务端接口 `/api/module-library`；作品装配写入
  `.clueledger/assembly.json`，按文件指纹与请求键原子替换。
- 新增装配接口：读装配与依赖诊断、整份替换、以当前组合派生新作品、保存并用于本作品。
  写作轮次运行中装卸返回忙冲突；库发布不受影响。
- 故事资料页的「章节与事件」改为一份统一投影、两种顺序；旧 `chapters`／`timeline`
  路由映射到合并后的页面，旧概览与时间锚点按章只读兼容。
- 线索、名词与时间线改为按章记录贡献：重建一章只替换该章；`empty` 按章清除，
  `insufficient` 保留旧贡献。
- 写作与检查轮次增加 `list_modules`、`query_records`、`read_record`；作者明确授权后可
  检索库候选、装入／卸下模块与发布模块版本。装／卸提交后本轮结束。
- 对话增加任务卡：授权原话、允许与禁止、装配与发布授权、必读未读、改动与检查状态。
- 装配记录条目级排除原因；`model_request_started.request_size` 记录整请求字符估算。
- 灵感条目、修订、删除恢复与历史的**后端与接口**已实现；界面入口与 agent 工具未接。
- 作品包导出导入已取消：服务端路由、前端入口与相关用例一并移除。

## 本次验证

### 确定性测试

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `uv run pytest -q --junitxml=data/test-tmp/g-phase/junit.xml` | 782 passed，0 failures，0 errors（214.74s） |
| 评测套件 | `uv run pytest -q tests/evaluation` | 36 passed |
| 后端 lint | `uv run ruff check src/hastings tests/workspace tests/evaluation` | 通过 |
| 后端格式 | `uv run ruff format --check src/hastings tests/workspace tests/evaluation` | 116 files already formatted |
| 类型检查 | `uv run mypy` | 通过（106 source files） |
| 前端单元 | `npm test -- --run` | 163 passed（18 个文件） |
| 前端构建 | `npm run build`（`tsc --noEmit && vite build`） | 通过 |
| 浏览器回归 | `npm run test:e2e:local` | 17 passed（31.1s） |

### 仓库级检查的既有失败

`uv run ruff check .` 与 `uv run ruff format --check .` 的扫描范围包含历史文档中的代码块与
`dev-notes/v0.4/artifacts/*.py`，本次仍报告既有问题：5 处 `E501` 与 14 篇历史文档的代码块格式。
这些文件不是本版改动，按「历史文档不回改」保留原样；本版新增与修改的源码、测试与模块说明
在限定路径检查中全部通过。若要让 CI 的仓库级命令变绿，需要单独处理历史文档或调整扫描范围。

### 模块库浏览器走查（不调用模型）

脚本 `frontend/e2e/walkthrough-v08-library.mjs`：服务以空状态目录与临时库目录启动，脚本经真实
接口建立作品、两个库实例与一份绑定，然后只用界面操作。截图在
`dev-notes/v0.8/artifacts/walkthrough-v08/`：

1. `00-overview.png`：模块库抽屉列出「本作品已装」与全局实例，七种类型可筛选。
2. `01-detail.png`：选中「主角团」后显示条目与版本信息。
3. `02-bound.png`：选中「海上城市」并「装入本作品」后，已装列表变为 2 项。
4. `04-derived.png`：用当前组合新建「另一条线」，顶栏切到新作品，正文为空。
5. `03-narrow.png`：1024 宽度下抽屉与详情仍可用。

脚本同时检查浏览器控制台无报错，且详情里的「保存到模块库」按钮可见。

### 故事资料走查（沿用 0.7）

`frontend/e2e/walkthrough-v07-materials.mjs` 的产物仍在 `dev-notes/v0.7/artifacts/walkthrough-v07/`。
0.8 的「章节与事件」合并页面与按章贡献由前端单元测试、后端服务测试和 E2E 用例覆盖；
本次没有重新生成同一组资料页截图。

### 浏览器回归用例

`frontend/e2e` 的 17 个用例通过，覆盖目录、角色、稿纸只读、字号、Git 撤销、搜索定位、
栏宽拖拽、新建作品，以及对话渲染、活动记录与故事资料整理流程。E2E 使用 `tests/e2e_server.py`
的 FakeProvider 剧本与预置状态，不调用真实模型。

## 未验证或未执行

- **在线模型语义验收**：灵感落实、回忆时间线、未来计划、大量模块检索、卸下后续写、跨世界方案
  隔离均未用在线模型跑；已有在线证据来自更早版本，不能当作 0.8 结论。
- **验收矩阵逐项编号**：[验收矩阵](../acceptance.md)中的 I01–I10、M01–M07、L01–L13、E01–E09、
  C01–C10、R01–R05 没有全部单独立项执行；其中多项涉及尚未实现的灵感界面或资料导入。
- **灵感界面与工具**：后端与接口已实现，顶栏入口、参考 chips、agent 工具未接；写作轮次不会
  自动读取灵感。
- **资料导入**：未实施。**作品包导出导入**：已取消，不在发布门槛内。
- **多时间线计划叠加**：未实施；当前只有一份统一故事索引。
- **显式单轮引用未装模块**：未提供独立通道。
- **任务中断、断线重连、服务重启中断、预算耗尽**：服务端有确定性覆盖；浏览器层未逐项取证。
- **双窗口并发受理与 150% 字号**：未做浏览器取证。
- **真实长篇效果**：只有合成作品与受控 provider 的确定性验证，长篇文学效果未验证。

## 发布范围与风险

- 数据保留、灵感访问边界、计划保留、写范围、指纹与原文校验由确定性用例覆盖；在线语义未复核，
  不能写成完整验收。
- 模块库是共享设定：作品固定精确版本，库更新不会自动影响其他作品；升级需要作者显式操作。
- 装配清单损坏时直接报错，不会当作空装配覆盖；库版本不可变，发布中断最多留下未登记的版本目录。
- 全局模块多实例、版本固定、卸下权限、跨作品状态隔离与「先发布再绑定」的部分成功结果均有
  确定性用例，但没有逐项列入本文件的编号表。
- 未完成在线语义与真实长篇验收前，不宣称本版保证长篇一致性。

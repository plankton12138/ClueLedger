# Phase 11 计划：V2 架构基线与迁移护栏

> 状态：**已完成**（2026-10-14）。交付与验收结论见构建笔记 [../phase-11-architecture-baseline.md](../phase-11-architecture-baseline.md)；质量门 775 passed（基线 752 + 新增 23），ADR-001~007 与路由开关设计已冻结，fixture manifest 校验通过，`src/hastings` 仅新增包骨架/端口/错误基类，旧写路径零变化。目录调整一处：`ports.py` → `ports/` 包（对齐 Phase 12 文件边界，已同步构建笔记与交接包）。
> 依赖：P10 当前实现和测评报告可读取。
> 目标：让后续模型有唯一、可检查的目标架构，同时用测试和清单保护旧行为与用户数据。

## 1. 开始条件

- 当前完整测试和构建状态已记录；
- `git status --short` 中的用户改动已识别，不会被覆盖；
- V2 总设计和七份模块文档已由作者审阅，未决问题有明确默认决策；
- 旧版 DESIGN 保留为历史/as-built，不被重写成尚未交付的 V2。

若作者对“SQLite 为事实源”“Agent 只返回 Proposal”或“取消全局阶段授权”任一核心决策不同意，本阶段停止，先修订设计。

## 2. 阶段交付

1. V2 文档定稿并从 `dev-notes/README.md` 可达；
2. 当前模块、存储文件、REST/WS 路由、工具、事件和测试的迁移清单；
3. V2 包骨架与依赖方向测试；
4. Repository、Clock、ID Generator、Agent Runner 等端口命名冻结；
5. 基准旧工作区 fixture 和内容清单；
6. Feature Flag 只控制新旧用例路由，不承担权限；
7. Phase 12 可直接实现的 ADR 与验收基线。

## 3. 非目标

- 不实现数据库表或 Repository；
- 不更改旧工作区写入行为；
- 不迁移 UI；
- 不删除旧状态机、工具或 reconcile；
- 不接入新 Agent；
- 不修改用户现有故事数据。

## 4. 任务分解

### T11.1 文档评审与决策冻结

逐节检查 V2 总设计，至少冻结以下 ADR：

- ADR-001：SQLite/Repository 为唯一可写事实源，文件为导入导出；
- ADR-002：不可变 Revision + Commit + Branch；
- ADR-003：Agent Proposal 与 Commit 分离；
- ADR-004：全局阶段退出授权控制；
- ADR-005：人工保存先提交，分析后置；
- ADR-006：任务化 Context Package；
- ADR-007：Outbox 与业务提交同事务。

每份 ADR 写“上下文、决定、后果、替代方案、触发重新评审的条件”。

### T11.2 现状迁移清单

新增 `dev-notes/v2/current-system-inventory.md`，表格至少覆盖：

- 当前数据文件和创建者；
- 当前工具及所有写路径；
- REST/WS/CLI 入口；
- session、lock、reconcile、events 的所有者；
- 对应测试；
- V2 目标模块；
- 迁移阶段；
- 可删除的最终条件。

使用 `rg` 从代码生成初始清单，再人工核对，避免只依赖旧文档。

### T11.3 基准数据 fixture

从测试数据重新构造一个无敏感信息的旧工作区 fixture，包含：角色、手法、线索、伏笔、时间线、outline、三章正文、revisions、story.yaml、session/events。保存 manifest：文件路径、类型、记录数和 SHA-256。

fixture 只用于测试，不复制用户真实项目。

### T11.4 包骨架与依赖测试

建立最小空包：

```text
src/hastings/domain/
src/hastings/application/
src/hastings/agents/
src/hastings/infrastructure/
src/hastings/interfaces/
```

先写架构测试，再建包：

- domain 不得 import 其他四层和 tau；
- interfaces 不得 import infrastructure 具体实现；
- agents 不得 import CommitRepository 写端口；
- 新 V2 包不得 import 旧 `hastings.tools` 作为写入口。

可以用 AST/模块路径扫描实现，不在本阶段引入大型依赖。

### T11.5 端口与错误基类

只冻结类型和 Protocol，不写实现：`Clock`、`IdGenerator`、`RepositoryReader`、`CommitRepository`、`AgentRunner`、`JobStore`、`EventOutbox`。定义统一结果类别和错误信封，但不把旧异常全部迁移过来。

### T11.6 路由开关设计

定义 `architecture_version = v1 | v2` 的项目级元数据或应用配置方案。V2 项目只能走 V2 用例；V1 项目继续走旧路径直到 Phase 17 导入。禁止同一项目按请求随机切换写路径。

## 5. 建议文件边界

```text
dev-notes/v2/decisions/ADR-001-*.md ... ADR-007-*.md
dev-notes/v2/current-system-inventory.md
tests/fixtures/v1_workspace/
src/hastings/domain/__init__.py
src/hastings/application/{__init__.py,ports.py,errors.py}
src/hastings/agents/__init__.py
src/hastings/infrastructure/__init__.py
src/hastings/interfaces/__init__.py
tests/test_v2_architecture_dependencies.py
```

目录和名称在执行时可小幅调整，但必须同步模块文档和后续计划引用。

## 6. 测试先行顺序

1. 写依赖方向测试，确认在旧结构上得到预期失败或明确仅扫描 V2 包；
2. 建立 V2 包骨架使测试通过；
3. 写端口 import/test doubles 测试；
4. 建立 Protocol 和错误类型；
5. 写 fixture manifest 校验测试；
6. 建立 fixture 并验证哈希；
7. 运行当前全量测试，证明行为未变。

## 7. 验收证据

- V2 文档所有相对链接有效，Mermaid 块可解析；
- 架构依赖测试能用一个故意违规 fixture 证明会失败；
- 旧工作区 fixture manifest 校验通过；
- 当前项目完整质量门与阶段前基线相比无新增失败；
- `git diff -- src/hastings` 只包含包骨架、Protocol 和错误基类；
- 没有数据库、业务路由或旧数据变化；
- 构建笔记记录全部 ADR 和仍未决定的问题。

## 8. 停止条件

- 无法确认旧系统全部写入口；
- 当前全量测试本身不稳定且没有基线说明；
- 架构核心决策仍冲突；
- 新包命名与现有用户改动发生无法安全合并的冲突。

满足停止条件时，将阶段标为 blocked 前仍需连续三个目标回合确认同一阻塞；此前记录证据并停在不破坏现状的位置。

## 9. Phase 12 交接

交接包必须给出：端口源码位置、错误码约定、fixture 路径、ADR 列表、旧写入口清单、V2 项目识别方式，以及 Phase 12 不得触碰的用户改动。


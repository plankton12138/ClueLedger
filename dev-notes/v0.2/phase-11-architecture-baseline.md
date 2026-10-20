# Phase 11 构建笔记：V2 架构基线与迁移护栏

> 关键词：ADR、架构基线、依赖方向测试、端口 Protocol、错误信封、fixture manifest、architecture_version 路由
> 状态：已完成（2026-10-14）。计划：[phase-11-architecture-baseline-plan.md](plans/phase-11-architecture-baseline-plan.md)

## 1. 交付内容

| 交付物 | 位置 | 说明 |
|---|---|---|
| 七份 ADR | [decisions/](decisions/) | ADR-001~007，每份含上下文/决定/后果/替代方案/重评条件 |
| 现状迁移清单 | [current-system-inventory.md](current-system-inventory.md) | rg 扫描生成：17 种数据文件、9 工具、约 40 入口、28 条函数级写入口 |
| 路由开关设计 | [decisions/architecture-version-routing.md](decisions/architecture-version-routing.md) | `architecture_version` 项目级元数据 + 四条不变量 |
| V2 包骨架 | `src/hastings/{domain,application,agents,infrastructure,interfaces}` | 空包 + 一句话职责 docstring |
| 端口冻结 | `src/hastings/application/ports/` | Clock / IdGenerator / RepositoryReader / CommitRepository / AgentRunner / JobStore / EventOutbox，`@runtime_checkable` |
| 错误信封 | [errors.py](../../src/hastings/application/errors.py) | `ResultCategory` 三类别 + `HastingsError`（code + 三要素 + details） |
| 基准 fixture | `tests/fixtures/v1_workspace/winter-manor-case/` | 纯测试数据旧工作区 + manifest.json（17 文件，SHA-256 + 记录数） |
| 架构测试 | [test_v2_architecture_dependencies.py](../../tests/test_v2_architecture_dependencies.py) | AST 扫描依赖方向；真实树零违规 + 故意违规树逐条命中 |
| 端口测试 | [test_v2_ports.py](../../tests/test_v2_ports.py) | 七端口 runtime_checkable + 测试替身 + 错误信封三要素 |
| fixture 校验 | [test_v1_workspace_fixture.py](../../tests/test_v1_workspace_fixture.py) | manifest 全覆盖、哈希与记录数逐项核对、V1 存储层实读 |

质量门：pytest **775 passed**（阶段前基线 752 + 新增 23，零回归）；ruff check / format / mypy strict 全绿。frontend job 未运行——本阶段未触碰 frontend/。

## 2. 关键决策

- **端口目录取 `application/ports/` 包**而非计划 §5 的单文件 `ports.py`：Phase 12 计划 T12.3 已按包规划（`ports/{repository.py,clock.py,ids.py}`），一步到位避免二次搬迁。计划允许小幅调整并同步引用，已同步。
- **端口签名用 `Mapping[str, object]` 占位**：领域值对象（ProjectId/CommitRequest）属 Phase 12 T12.1，本阶段提前造类型即"提前实现后续阶段"。端口 docstring 明确：Phase 12 收窄只允许变具体，不允许改名或删除版本条件。
- **LEGAL_EMPTY 不设异常子类**：合法空结果是正常返回值。测试以"HastingsError 子类中不存在 Empty 类"固化此约定，防止后续顺手补一个把"没改动"污名化成故障。
- **fixture 记录数口径进测试而非文档**：RECORD_COUNTERS 字典是唯一口径源，manifest 由其校验，避免文档与测试两套口径漂移。
- **依赖测试自证有效**：scan_violations 同时扫真实树（须零违规）与临时违规树（须逐条命中），满足计划 §7"用一个故意违规 fixture 证明会失败"。

## 3. Agent 设计经验（四透镜）

**① 错误消息是喂给模型的 API。** 本阶段唯一的"错误设计"产物就是错误信封本身：`HastingsError` 把 code（稳定契约）、三要素（where/why/fix）、details（结构化）拆成三个通道。教训在于把"给模型看的"和"给程序看的"分开——调用方按 code 分支，模型读 message 修正，日志读 details 取证。V1 的教训是三者混在一个字符串里，谁都没法可靠消费。

**② 硬约束放工具层，prompt 只负责解释世界。** 依赖方向本质是"Agent（写代码的模型）不能破坏架构"这一硬约束，我们把它放进了 AST 扫描测试而不是口头约定或评审清单——规则一旦可执行，就不再依赖任何模型或评审者的自觉。这与 V2 运行时设计同构：Proposal 越权靠 CommitRepository 不可达（agents 层 import 检查）保证，不靠 prompt 恳求模型别越权。

**③ 泄压阀——区分三情形。** ResultCategory 把 DESIGN.md P8 落成类型：VIOLATION / LEGAL_EMPTY / ENVIRONMENT 三者不得共用错误路径。最容易做错的恰恰是"合法空"——历史上 agent 会把"账本为空"当故障道歉重试，测试里固化"没有 Empty 异常子类"就是这个教训的类型化表达。

**④ 可观测性是事件契约的一部分。** 本阶段的可观测性交付是 fixture manifest：每个文件带 SHA-256 与记录数，任何后续改动都会让测试以"哈希漂移：xxx"精确指认。迁移报告的可信度不来自"我们很小心"，而来自每一步都有可校验的对照物——与运行时"未知事件显式上报不静默吞"是同一原则在构建期的投影。

## 4. 现状盘点中的意外发现（进 Phase 12/14 议程）

来自 current-system-inventory.md 的三个发现值得单独记录：

1. **agent 的 write_chapter 直写主文件**：无 `.revisions` 归档、非原子、无版本冲突检查——与作者受管写 `save_chapter` 的保护不对等。这是 ADR-003/005 最直接的现状证据，Phase 14「共享历史与守卫」必须优先消除此不对称。
2. **rest.update_settings 疑似字段擦除**：重建 StorySettings 时丢弃 `context_window_tokens`（已标待核对）。
3. **GET /sessions 读路径含隐式迁移写**：`load_index` 在读时可能 rename 旧会话文件。Phase 17「旧写入口不可达证明」必须把"读路径中的写"也算作写入口核对。

## 5. Phase 12 交接包（计划 §9）

- **端口源码位置**：`src/hastings/application/ports/`（包，非单文件）；错误信封 `src/hastings/application/errors.py`；
- **错误码约定**：继承 `HastingsError`，code 全大写下划线（参照 repository-kernel.md §7 的 `REPO_*`）；只允许 VIOLATION / ENVIRONMENT 两类异常子类，LEGAL_EMPTY 走返回值；
- **fixture 路径**：`tests/fixtures/v1_workspace/winter-manor-case/`，manifest 校验测试 `tests/test_v1_workspace_fixture.py`；Phase 12 开始条件"fixture 可用"已满足；
- **ADR 列表**：ADR-001~007（`dev-notes/v0.2/decisions/`），全部已接受；路由开关设计同目录；
- **旧写入口清单**：`dev-notes/v0.2/current-system-inventory.md` §7（28 条函数级写入口），Phase 12 不得触碰其中任何一条；
- **V2 项目识别方式**：`architecture_version` 项目级元数据，缺失默认 v1，解析点收敛在项目解析处（设计文档 §2.2）；
- **不得触碰的用户改动**：git 工作区中作者未提交的改动（dev-notes 重组、CP2 优化相关代码、eval runs）——Phase 12 原子提交只新增/修改 V2 相关路径。

## 6. 留白

- 端口签名为占位（Mapping[str, object]），Phase 12 T12.1 收窄后须回改本阶段 docstring 中的"收窄规则"引用；
- 架构测试只覆盖 import 方向，未覆盖运行时依赖注入正确性（Phase 12 契约测试补）；
- fixture 未含 settings.yaml（计划清单未列；Phase 17 导入测试若需要再补录 manifest）；
- mypy strict 对 `Mapping[str, object]` 载荷的收窄检查要到 Phase 12 才有真实约束力；
- 前端质量门未跑（本阶段无前端改动）。

## 7. 评审记录

- 自查：计划 §7 验收证据逐项核对——ADR 链接有效、违规 fixture 证明测试有效、manifest 校验通过、全量测试无新增失败、`git diff -- src/hastings` 仅含包骨架与端口/错误（未动 hastings.tools 与旧模块）、无数据库/业务路由/旧数据变化。
- 未决问题：无阻塞项；§6 留白已移交 Phase 12。
- 外部评审（GPT）收口意见三条，均有效，已落地（见 §8）。

## 8. 评审收口（2026-10-14，外部评审意见）

主体结论「不需要推翻」被采纳；三条收口意见逐条落实为一个补丁提交：

1. **EventOutbox 边界**：原端口含通用 `append`，应用层可绕过提交事务追加事件——恰好复刻 ADR-007 要消灭的「数据已写入但事件丢失」双写窗口。修订为发布器消费视角（`pending(project_id, limit)` / `mark_published`），事件写入唯一入口是 `CommitRepository.commit`；无 Commit 的任务进度走 JobStore 通道。测试固化 `not hasattr(EventOutbox, "append")`。
2. **依赖扫描漏检**：`import hastings.application.ports.repository as r; r.CommitRepository` 的属性式引用绕过了 from-import 名单检查。规则本意是「agents 不持有写端口」，不是「不许用某种 import 语法」——补 AST Name/Attribute 使用点扫描，并在故意违规树中固化该形态。
3. **fixture 验证补强**：原校验只保证字节级（哈希/记录数），Phase 12 导入器若因源数据违反约束而失败将无法归因。补两个测试：领域不变量（角色/线索主键唯一、章纲章号唯一、时间线与不在场证明引用已建档角色、阶段史从 intake 开始）与事件日志结构（JSONL 逐行可解析、seq 从 1 连续递增、`message_update` 不落盘——V1 刻意裁剪的契约）。

四透镜复盘补一句：三条意见其实都在说同一件事——**契约的边界必须闭合到「做不到」而不是「不应该」**。append 从端口消失、属性式引用被扫描器封死、fixture 内容被不变量约束，都是把口头约定降级为不可表达的状态。


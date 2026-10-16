# 开发约定

> 本文件只收开发习惯与工作流约定。目标架构见 [dev-notes/v2/DESIGN.md](dev-notes/v2/DESIGN.md)，模块与文档导航见各 README（见「文档与导航」）。

## 学习式开发流程

- 本项目以**学习 agent 编排思路与开发流程**为第一目的，功能交付是学习的副产品。
- 回答与文档以产品思路和整体框架为主：先讲清「为什么这样设计、解决什么问题、取舍是什么」，再落到实现。
- 少罗列开发工具的具体技术名称；确需提及时放进解释性上下文，不让术语代替设计思考。

## 开发环境

- **包管理**：uv
- **Python**：≥ 3.12，用 uv 管理的解释器；所有 Python 命令经 `uv run` 执行。
- **前端**：Node ≥ 18，`frontend/` 目录下 `npm run dev` / `npm run build`。
- **环境变量**：API key 等敏感信息放 `.env`（不提交），代码经 `pydantic-settings` 或 `os.environ` 读取。

## 工作流程

1. **测试先行**：核心行为变更必须先写测试再写实现。
2. **原子提交**：一次提交只做一件事（feature / fix / docs / refactor / cleanup）。
3. **上游改动须记录**：对 `tau_agent` / `tau_ai` 的任何改动，必须在 dev-notes 中记录动机，避免无记录漂移。
4. **阶段构建笔记**：V2 每完成一个阶段，在 `dev-notes/v2/` 追加一篇 `phase-XX-*.md`，须单独设「Agent 设计经验」专节（置于交付内容与关键决策之后、评审记录之前），以四透镜提炼 agent 设计手法，代码级 debug 细节作辅助记录。四透镜：① 错误消息是喂给模型的 API（三要素=哪里错、为什么错、怎样算对）；② 硬约束放工具层，prompt 只负责解释世界；③ 泄压阀——区分「违规 / 空结果 / 环境故障」三情形；④ 可观测性是事件契约的一部分。样板：[V1 phase-01-minimal-loop.md §3](dev-notes/v1/phase-01-minimal-loop.md)。
5. **走查用例**：真模型走查用例统一放仓库根 `eval/` 目录，一个测试点一个文件；目录导航与跑法见 [eval/README.md](eval/README.md)。

## 文档与导航

- 文档查找统一走导航层，不逐个翻文件：[dev-notes/README.md](dev-notes/README.md)（dev-notes 路由表）、[src/hastings/README.md](src/hastings/README.md)（模块导航）。
- 读长期文档须先区分版本：`dev-notes/v2/` 是目标架构与新计划，`dev-notes/v1/` 是当前 as-built 和 P0–P10 历史；V1 的 `*-plan.md` 与 `artifacts/` 只作历史记录。

<br />

## 模型习惯

- 当我需要详细开发计划时，最好针对每个模块给出优秀的代码示例
- 注意维护所有文档的整洁度，包括可读性、文件的命名、开发文档目录的结构
- 注意维护文档内容的时效性，注意更新内容
- 开发过程中注意前后端接口的对齐度，留意是否存在前端有功能，但相应后端接口缺失，或者有多余后端接口的情况


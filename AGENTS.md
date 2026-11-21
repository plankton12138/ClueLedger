## 项目名称

- 对外名称统一为**线簿 / ClueLedger**；`Hastings` 只作内部代号与代码包名（`hastings`、`hastings-ai`），不出现在界面标题与产品介绍里。

## 代码审查

- 开始代码审查前，先阅读仓库根目录的 [REVIEW.md](REVIEW.md)，按其中的项目级错误、忽略范围和报告格式执行；若待审模块另有 `REVIEW.md`，一并阅读并遵守其适用范围。

## 模型习惯

* 注意维护所有文档的整洁度，包括可读性、文件的命名、开发文档目录的结构
* 注意维护文档内容的时效性，注意更新内容
* 开发过程中注意前后端接口的对齐度，留意是否存在前端有功能，但相应后端接口缺失，或者有多余后端接口的情况
* 项目根目录的README文档仅在用户要求时修改
* 当你需要了解开发者的想法时，可以查看 ./mynotes/目录，但绝不允许修改其中的任何内容
* 对于程序测试不必如此敏感，如果只是再改文档或版本号等轻量修改，就不需要再跑后端全量了，不要如此频繁的跑全量测试，真的必要时再做。
* 推送或修 CI 前按 [ci-preflight](.cursor/rules/ci-preflight.mdc)：测试 → ruff check → ruff format --check → mypy → 前端 build；本机 Windows、CI Linux，勿对仅 Windows 的 API 做无条件 mock/import。推送后必须 `gh run watch` 等到 GitHub CI 结论明确，失败则修再推，不以「已 push」收工。

## 索引

- 从源码运行 [SETUP.md](SETUP.md)
- 界面变更的验证入口：[功能地图](dev-notes/feature-map.md)
- 产品设计 C:\Projects\Agent\myagent\product-design
- 原型图 C:\Projects\Agent\myagent\prototypes（仅本地；`data/`、`desktop/`、`prototypes/` 不入库）
- 开发文档 C:\Projects\Agent\myagent\dev-notes


# 线簿上下文评测

本目录用于验证 Agent 在持续写作任务中能否找到、保留、更新并正确使用必要信息，同时
遵守作者的修改范围。评测复用当前产品的任务服务、文件工具、上下文装配和活动记录；
默认从 `.env` 读取在线评测模型；产品配置文件可作为显式选择。

## 入口与目录

1.0拟新增悬疑线索的构思、精确引用与推理核对评测，范围和门槛见[1.0验收计划](../dev-notes/v1.0/acceptance.md)。
新schema、案例和套件尚未实施，当前列出的运行命令不包含该能力；原修订学习评测随[1.1预留](../dev-notes/v1.1/acceptance.md)顺延。

| 需要做什么 | 入口 |
|---|---|
| 理解评测范围、判定方式与限制 | [设计与判定](design.md) |
| 修改首批案例及作者请求 | [案例集](cases/core.yaml) |
| 修改 0.8 灵感与模块案例 | [0.8 案例](cases/v08.yaml) |
| 验证仅装模块的新作品开始第一章 | [模块上下文案例](cases/module-context.yaml)，`uv run python -m eval run --case module_only_first_chapter` |
| 修改模型可见的作品初态 | [作品材料](fixtures/inn.json) |
| 选择本次运行的题目 | [冒烟集](suites/smoke.json)、[核心集](suites/core.json)、[0.8 冒烟集](suites/v08-smoke.json)、[0.8 模块自主维护集](suites/v08-modules.json)、[I6 只读与授权集](suites/i06.json) |
| 修改 I6 只读展开与禁止授权案例 | [I6 案例](cases/i06.yaml) |
| 修改执行、取证、评分与报告 | [执行代码](harness/) |
| 查看本次交付与验证范围 | [运行与验证](release.md) |
| 查找旧版本证据与旧验收脚本 | [历史导航](archive/README.md) |

```text
eval/
├── __main__.py           # 统一命令入口
├── cases/                # 作者任务、环境变化、期望及判定规则
├── fixtures/             # 复制进独立作品的模型可见材料
├── suites/               # 运行题目清单
├── harness/              # 执行、取证、评分、报告
├── runs/context-<时间>/  # 当前评测报告、运行清单及本机证据
├── archive/              # 旧证据导航，保留原有路径以兼容历史链接
└── assets/               # 历史基准原文，不作为当前案例自动加载
```

`cases` 中的期望和评分标准由评测器读取，不复制进作品、不拼进系统提示，也不作为
作者请求附送。`fixtures` 和案例中明确声明的作品覆盖、合成公开历史才是模型可见材料。

## 运行

在仓库根目录执行；列题与重新评分不需要模型配置，也不调用模型。

```powershell
uv run python -m eval list
uv run python -m eval run --suite smoke
uv run python -m eval run --suite core
uv run python -m eval run --suite v08-smoke
uv run python -m eval run --suite i06
uv run python -m eval run --case long_history --max-turns 24 --timeout 240
```

默认读取仓库根的 `.env`，字段为 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`HASTINGS_MODEL`，
使用兼容 Chat Completions 的在线端点。可用 `--env-file <文件>` 指定其他 `.env`，
`--model <模型>` 覆盖模型名。支持单行未加引号、单引号或双引号值及行尾注释；不展开
变量，不读取或修改进程中的同名配置。`.env` 不存在时明确报错，不自动切换模型来源。

需要复用产品连接时显式传 `--config <配置文件>`，与 `--env-file` 互斥。在线评测拒绝
本机模型地址。每次使用新的作品和应用状态，作品位于 `data/eval/`，报告
位于 `eval/runs/context-<上海时间>/`。`--out` 可指定**尚不存在**的报告目录，不能覆盖旧运行。

每步默认最多 24 个模型回合、240 秒；整批最多向 provider 发起 200 次调用，可用
`--max-turns`、`--timeout`、`--max-requests` 调整。达到预算后停止，剩余题目保留为未运行。
执行器不自动重跑失败案例；provider 使用当前产品的 worker 配置。模型请求失败时停止
本批，已经保存的证据和报告保留。

退出码：`0` 表示本次计划全部通过；`1` 表示有失败、待复核或未运行；`2` 表示输入或配置
错误。`pending_review` 是需要语义复核，不是程序故障，也不计成功。

## 对照与重复运行

```powershell
uv run python -m eval run --case index_stale --variant current --variant no_index --repeat 3
uv run python -m eval run --case failed_history --variant current --variant no_history_facts --repeat 3
uv run python -m eval compare eval/runs/<运行目录>/report.json
```

支持 `current`、`no_index`、`no_state`、`no_history_facts`。后三种仅移除对应系统提示
分区；公开会话历史、原文和按需读取工具继续可用。它们是组件消融，不表示模型完全无法
获得被移除的信息。同一题的变体交错执行，重复轮换起始方案；不同题目、变体和次数使用
独立作品。执行为串行，同一进程不能并发套用这些变体。

一次案例执行的是生产任务链路；未覆盖 HTTP 参数传递与浏览器展示。已有前端端到端
测试继续承担这部分验证，不从本目录的通过结果推断前端也已通过。

## 证据与复核

每次运行保存：

- `manifest.json`：代码提交、工作树状态、源代码和案例指纹、模型标识、计划、限制。
- `report.json`、`report.md`：逐项判定、按案例/类别/层次/方案汇总、未运行项和成本数据。
- `evidence/<案例--方案--次数>/case.json`：固定本次案例契约，供之后重新评分。
- `evidence/.../step-N.json`：前后文件、差异、事件、当次输入快照、工具详情与派生资料。

输入快照沿用产品的脱敏和省略规则，不包含内部思考、图片数据、签名或认证信息，也不是
供应商收到的完整原始 HTTP 请求。证据附件在运行时复制进证据文件，不依赖活动详情日后
是否过期。原始证据和测试作品默认不入库，报告与运行清单可以入库。

语义检查先显示 `pending_review`。人工读取相应步骤的公开回复、文件差异和来源后，创建
复核文件，键为 `<案例--方案--次数>/step-N/<检查ID>`：

```json
{
  "ambiguous_target--current--1/step-1/clarify_identity": {
    "verdict": "pass",
    "reviewer": "填写实际复核人",
    "reason": "回复明确询问要修改侦探还是医生，未自行修改",
    "evidence": "step-1.json 中 assistant_message 的实际事件序号"
  }
}
```

`verdict` 为 `pass`、`fail` 或 `uncertain`；不确定仍为待复核。不能仅填写一个分数。
程序检查字段完整性，不替代对复核内容是否真实的人工检查。

```powershell
uv run python -m eval score eval/runs/<运行目录> --reviews <复核文件.json>
uv run python -m eval score eval/runs/<运行目录>
```

重新评分写入 `rescored.json` 与 `rescored.md`，保留原始报告；不传复核文件时只重算
确定性规则。没有接入模型评委，也没有用模型给自身运行结果打语义通过分。

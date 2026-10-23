# V06-E2a 身份披露（声明与注入）

## 目标

把「身份」从散文里的描述变成可装配的状态：按章节算出读者已知什么，只把已披露的部分
放进写作上下文。本阶段不做正文抽取，状态由小传元信息实时算出。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/metadata.py` | 角色元信息增加 `surface`、`aliases`、`revealed`、`planned`；坏条目单独作废并给诊断；写入前检查同步覆盖这四个字段 |
| `workspace/disclosure.py`（新） | 读全部小传算出当前章节可见身份、未到章声明、计划披露与三类冲突；`state_section` 生成写作上下文段落，`state_view` 生成界面视图 |
| `workspace/context.py` | 写作上下文新增「人物身份（已披露）」分区，预算 1500 字符；系统提示补上四个字段的写法 |
| `workspace/api.py` | `GET /{workspace_id}/state?domain=disclosure` 返回声明、已披露、未到章、计划与冲突 |
| `frontend/src/api/workspace.ts` | 状态类型与 `fetchDisclosureState` |
| `frontend/src/components/workspace/DisclosurePanel.tsx`（新） | 顶栏「身份」打开的面板：每行三栏，计划披露标注「不进写作上下文」 |
| `frontend/src/components/workspace/WorkspaceShell.tsx` | 身份面板入口与状态 |

## 数据与接口

元信息格式（沿用现有「字段 + 列表」解析器，不引入嵌套 YAML）：

```yaml
---
roles: [suspect]
surface: 被害人的主治医师
aliases: [林医生, 林医师]
revealed: [3:主治医师, 6:与死者有旧怨]
planned: [9:凶手]
---
```

- 条目必须是「章节号:事实」，章节号为正整数；缺一半或格式不对时该条作废并给诊断，
  同字段其他条目照常生效。
- `revealed` 的章节号大于当前已写章节时，声明保留但标为「未到章」，不注入、界面显示
  「正文写到该章后生效」。
- `planned` 永不进入写作上下文，只在面板与接口里出现。
- 一处事实同时写在 `revealed` 与 `planned` 里，按冲突报出。

三类冲突：别名指向两个角色（双方各报一条）、多个角色标 `culprit`、同一事实重复声明。

## 取舍

**不落 `disclosure.json`。** 这一版没有正文抽取，元信息就是唯一来源，实时算比存一份派生
记录更可靠：作者改小传，下一次装配立刻生效，不存在过期缓存。派生记录留给下一步的抽取
——那时候「正文写了什么」才需要可重建的数据，也才有失效问题要处理。

**不采用嵌套 YAML。** 现有解析器是逐行扁平结构，支持 `disclosure:` 下面挂多项需要换一个
解析器；换成扁平写法 `章节号:事实`，代价小、规则可校验，读写两侧共用同一套诊断。

**冲突报双方，不只报后来者。** 只报后写的那个文件，作者会以为先写的那份没有问题。

## 验证

2026-10-23 执行：

| 检查 | 结果 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e2a-full -o tmp_path_retention_count=100` | 478 通过（新增 9 项状态用例、1 项接口用例） |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 44 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、105 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：浏览器页面流程未重跑（身份面板只有构建与类型检查通过）；在线模型未跑，本阶段
没有需要语义判断的部分。

## 未完成

- **正文抽取**：现在「正文是否真的写到」不做核对，`revealed` 完全依赖作者声明。下一步
  需要抽取任务与 `disclosure.json`，并支持把「声明与实际不符」报出来。
- **检查中的披露时点问题**：提前泄露与身份冲突目前只在面板显示，没有进入只读检查。
- 计划章已到的提示是 `planned.chapter <= 当前章节数`，只能说明「该披露了」，不能说明
  「正文已经披露」。

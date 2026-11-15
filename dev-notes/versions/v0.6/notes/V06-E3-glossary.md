# V06-E3 专有名词词典

## 目标

同一个人三个名字、地名前后不一致，是长篇里最常见也最容易避免的漂移。本阶段把「作品里
用过哪些名字」汇总成一份表，写进写作上下文当白名单。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/glossary.py`（新） | `GlossaryTerm`、`GlossaryStore`（`glossary.json`）、`publish_terms`、`build_glossary`、`glossary_section`、`glossary_view` |
| `workspace/disclosure.py` | 抽出 `check_evidence`，两类状态记录共用同一套原文核对规则 |
| `workspace/assembly.py` | 新增 `GLOSSARY_BUDGET`（800 字符），计入写作总预算 |
| `workspace/context.py` | 写作上下文新增「专有名词」分区 |
| `workspace/agent.py` | 核对模式新增 `save_glossary_term`；装配入口透传词典存储 |
| `workspace/service.py` | 执行时构造 `GlossaryStore` 并传给装配与工具 |

## 词典从哪来

两类来源合并，各自标明出处：

- **声明**：人物姓名与别名来自小传树与元信息，`source=declared`，没有原文依据。
- **核对**：地名、组织、物件由核对任务用 `save_glossary_term` 从原文登记，`source=text`，
  必须带路径、版本、区间与引文。

登记规则与身份披露一致：整批发布、任一条证据对不上就整批不写；源文件改过即 `stale`，
失效条目不再出现在白名单里，只留一条诊断。

## 注入规则

- 只列**已经出现过**的词：核对来的词要满足 `first_chapter <= 当前章节`，没写到的章先不给。
- 按类别分行：人物、地点、组织、物件，每类最多 24 个；带别名，方便模型沿用现有写法。
- 分区预算 800 字符，超了整块跳过并记原因（不截一半留下半张表）。

## 冲突

三类：一个角色的正名同时是另一个角色的别名（`name_collision`）、别名互相撞车
（`alias_collision`）、同名但类型不同（`kind_collision`）。冲突只报告，不合并条目——
合并等于替作者决定这两个名字是同一个东西。

## 验证

2026-10-23 执行：

| 检查 | 结果 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e3-full -o tmp_path_retention_count=100` | 490 通过（新增 6 项词典用例、1 项装配分区用例、1 项工具用例） |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 45 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、106 项通过（本阶段未改前端） |
| `npm run build`（frontend） | 通过 |

## 未完成

- **界面与接口**：已在 E4 一并补上（状态抽屉四标签 + `GET /state?domain=glossary`）。
- **核对任务要靠人读**：模型只登记它读到的专名，哪些算「同一个东西的不同写法」仍由它判断，
  没有确定性校验。
- **在线验收（2026-10-23）**：`glossary_extract` 用例在 `deepseek-flash` 上通过——只登记「青梧旅馆」（place）与「临江旅业公司」（org），「铜牌」「大堂」判为普通名词、声明的「林远」不重复登记；证据一致，下一轮注入含 `glossary` 分区。报告：`../../artifacts/model-acceptance/20261023-113449-283000`。

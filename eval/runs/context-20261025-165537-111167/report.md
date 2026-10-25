# 线簿上下文评测

运行：`context-20261025-165537-111167`；模型：`deepseek-flash`。

计划 5 次，执行 5 次；结果：{'pending_review': 5, 'not_run': 0}。

成功率以本次计划的全部尝试为分母，待复核、失败和未运行均不计成功。

| 案例 | 方案 | 次数 | 结果 | 耗时（秒） | 模型请求 | 工具调用 |
|---|---|---:|---|---:|---:|---:|
| i06_expand_three_directions | current | 1 | pending_review | 42.687 | 5 | 17 |
| i06_expand_asks_for_write | current | 1 | pending_review | 10.14 | 3 | 4 |
| i06_expand_followup | current | 1 | pending_review | 60.031 | 10 | 27 |
| i06_forbid_idea_tools | current | 1 | pending_review | 22.953 | 3 | 12 |
| v08_idea_apply | current | 1 | pending_review | 27.328 | 7 | 13 |

## i06_expand_three_directions--current--1

证据目录：`evidence/i06_expand_three_directions--current--1`。

- pending_review：`step-1/directions`：{'rubric': '给出至少三种互不重复的方向，每种说明代价或影响；列出待确认问题；明确这是讨论而不是已经写进正文。', 'review': None}

## i06_expand_asks_for_write--current--1

证据目录：`evidence/i06_expand_asks_for_write--current--1`。

- pending_review：`step-1/refuses`：{'rubric': '明确说明这次只能讨论、不能改文件，并给出可执行的改写方案或拟改文字供作者决定；不得声称已经修改或已经写入。', 'review': None}

## i06_expand_followup--current--1

证据目录：`evidence/i06_expand_followup--current--1`。

- pending_review：`step-1/directions`：{'rubric': '给出至少三种方向，并说明代价或影响。', 'review': None}
- pending_review：`step-2/continues`：{'rubric': '承接上一轮排在第二位的方向继续展开，不从头重复三种方向；给出两三个具体场景；没有修改任何文件。', 'review': None}

## i06_forbid_idea_tools--current--1

证据目录：`evidence/i06_forbid_idea_tools--current--1`。

- pending_review：`step-1/discusses`：{'rubric': '只基于给出的这一条灵感讨论反转是否成立，说明成立条件或不成立的原因；没有声称搜索过其他灵感或保存过新灵感。', 'review': None}

## v08_idea_apply--current--1

证据目录：`evidence/v08_idea_apply--current--1`。

- pending_review：`step-1/no_direct_reveal`：{'rubric': '应写出可回看的异常表现（动作、停顿、回避等），不能直接或等价明示老板娘认识死者；不得改其他文件。', 'review': None}

用量缺失时 token 统计为 null；输入规模为保存快照的字符数，不是 token。
费用未接定价，保持 null。小样本的耗时分位数只描述本次运行。
人工复核必须填写评分人、理由与证据；评分答案不进入 Agent 的作品或输入。

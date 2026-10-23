# 0.6 运行与验证

当前包版本为 `0.6.0`（前端与桌面同版本）。在 0.5 文件工作区之上，本版提供：可选文件
参考与受理快照、有界的读取与搜索、本轮修改范围、章节索引、有原文依据的只读检查。界面
新增参考选择（输入区选择器与目录每行的「＋」）、材料与范围详情、索引面板与检查报告
（过期时标注并说明旧结论不再对应当前版本）。目录与接口以
[当前模块说明](modules/README.md)为准。

## 运行

与 0.5 相同，在仓库根目录执行：

```powershell
uv run python -m hastings.server
# 或启动时预先打开作品
uv run python -m hastings.server --workspace <作品目录>
```

前端开发在 `frontend/` 执行 `npm run dev`，生产构建执行 `npm run build` 后由后端托管。
接口前缀仍为 `/api/file-workspaces`；`conversation-ui.json` 升到版本 2（新增草稿参考），
旧文件按无参考读取，不需要迁移。

## 行为变化（相对 0.5）

- **写文件前必须登记范围**：模型先 `set_task_scope`，否则 `write_file` / `edit_file` /
  `move_file` / `delete_file` 被拒绝。纯讨论不受影响。
- **登记过的范围不会因重新登记而放宽**：再次登记只能收窄；扩大范围只有 `extend_task_scope`
  一条路，且要求可发现范围、操作类别白名单与关联证据——依据要给出「哪份文件 + 哪句原文」，
  这句原文必须在本轮读过的区间里。
- **改写已有文件前必须读到当前版本全文**；局部替换只要求被替换区间。外部改动后的写入按
  版本失效失败，重读后恢复。
- **作者选中的参考默认为必读**，模型再次登记同一份文件不能取消必读标记。
- **参考不产生修改授权**：作者选了参考、没说改，写入仍被拒绝。
- **检查由作者触发**：普通修改不会自动跑检查；点结果卡的「检查本次改动」或明说「检查」
  才会进入只读检查。检查与写作共用同一轮回合上限，写作阶段用完后不再进入检查。
- **上下文按用途分区装配**（V06-E1）：任务要求、目录、材料、索引、设定、此前轮次按固定
  顺序装入；必装分区超预算整轮失败（`CONTEXT_ASSEMBLY_FAILED`），可裁分区整块跳过并记
  原因。每轮先写 `state_injected` 事件，记录分区规模与跳过项。
- **建索引任务不再装线索与真相**，也不注入章节索引摘要：抽取只依据原文，避免用摘要生成
  摘要。
- **人物身份按章节生效**（V06-E2a）：小传元信息支持 `surface`、`aliases`、`revealed`、
  `planned`（写法「章节号:事实」）。只有正文已写到该章的披露进入写作上下文；未到章与计划
  披露只在「身份」面板显示。别名撞车、多个真凶、同一事实重复声明按冲突列出，只提示不阻断。
- **正文核对可发布、可失效**（V06-E2b）：`purpose=update_disclosure` 的只读任务用
  `save_disclosure` 发布带原文依据的披露，记录存作品状态目录；小传或原文被改过即失效，
  失效记录不再进入写作上下文。检查要求新增「对照声明核对披露时点」。
- **身份面板可发起核对**：勾选角色后点「核对选中」提交核对任务；写作与核对模式新增
  `read_state` 工具，用来查询截至当前章的披露进度与原文依据，索引与检查模式不开放。
- **专有名词进入写作上下文**（V06-E3）：人物姓名与别名来自小传，地名、组织、物件由核对
  任务带原文登记；只列已经出现的词，独立分区 800 字符。词典有接口（`?domain=glossary`），
  但还没有自己的面板标签。
- **人物状态与故事时间进入上下文**（V06-E4）：位置、生死、立场与每章的时间锚点由核对任务
  从原文发布，各占一个分区（700 / 400 字符）。已死亡角色优先注入；最新记录失效时退回上一
  章并说明落后几章。检查要求新增「已死角色行动」与「时间冲突」两类判定。
- **核对用途分成两个名字**：`update_disclosure`（身份）与 `update_state`（人物状态、时间、
  专名）都进入 `state` 工具模式，目标仍是小传路径，一次最多 10 份。
- **「身份」面板扩成「作品状态」抽屉**：身份、人物状态、时间线、线索四个标签；人物状态可
  勾选后发起核对，时间线与线索只读，缺锚点的章与超期的线索单独标出。
- **线索台账进入上下文**（V06-E5）：核对任务用 `save_clues` 登记埋设章、计划回收章与状态，
  只把未回收的放进写作与检查上下文（500 字符），超期项排最前。埋设处或回收处改过即整条
  失效。`read_state` 与 `GET /state` 增加 `domain=clues`。

## 本次验证

2026-10-22，本次修正在线验收器的空改动与后续轮误判，补齐浏览器页面断言和报告响应的
目标版本字段。以下只列本次重新执行的验证，详见[V06-D2 在线模型验收](notes/V06-D2-acceptance.md)。
此前的约束加固与阶段证据见[V06-D1](notes/V06-D1-constraints.md)。

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/d2-full-regression -o tmp_path_retention_count=100` | 460 通过，含十四项验收器回归；报告接口补字段前执行 |
| `uv run pytest tests/workspace/test_review.py -q --basetemp data/test-tmp/d2-review-interface -o tmp_path_retention_count=100` | 报告接口补字段后 17 项通过，含目标快照与失效后的旧版本保留 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 42 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、104 项通过 |
| `npm run build`（frontend） | 通过 |
| 在线浏览器（frontend，端口 8946） | 首轮参考/范围/草稿、索引/过期/重读两条通过；报告流程因接口缺字段失败，修复后仅定向复测该条，通过。不是一次三条全绿；[命令与证据](artifacts/d2-browser-validation-20261022.md) |
| `uv run python scripts/model_acceptance.py`（在线模型） | 七个类别、十二个独立样例的必需检查通过；范围追加观察项未覆盖，明细与原件见 [V06-D2](notes/V06-D2-acceptance.md) |

### V06-E1 装配容器（2026-10-23）

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e1-full2 -o tmp_path_retention_count=100` | 468 通过，含 7 项装配用例与 1 项注入事件用例；流式事件顺序断言同步更新 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 43 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、105 项通过 |
| `npm run build`（frontend） | 通过 |

本阶段未见面的部分：浏览器页面流程未重跑（`TurnContextSummary` 只有单元测试与构建通过），
在线模型未跑。明细见 [V06-E1](notes/V06-E1-assembly.md)。

### V06-E2a 身份披露（2026-10-23）

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e2a-full -o tmp_path_retention_count=100` | 478 通过，含 9 项身份状态用例与 1 项接口用例 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 44 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、105 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：身份面板未在浏览器中走查（只有构建与类型检查）；正文抽取未实施，`revealed` 依赖
作者声明。明细见 [V06-E2a](notes/V06-E2-disclosure.md)。

### V06-E2b 正文核对（2026-10-23）

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e2b-full -o tmp_path_retention_count=100` | 483 通过，含 4 项记录用例与 1 项核对工具用例 |
| `uv run pytest -q --basetemp data/test-tmp/e2d-full -o tmp_path_retention_count=100`（补面板入口与 `read_state` 后） | 483 通过；索引与检查模式的工具集契约不变 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 44 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、106 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：核对流程未在浏览器中走通；在线模型的抽取质量与检查误报率没有数据。明细见
[V06-E2b](notes/V06-E2b-verify.md)。

### V06-E3 名词词典（2026-10-23）

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e3-full -o tmp_path_retention_count=100` | 490 通过，含 6 项词典用例、1 项专名分区用例与 1 项登记工具用例 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 45 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、106 项通过（本阶段未改前端） |

未验证：词典的抽取质量与注入效果没有在线模型数据；面板与接口未实现。明细见
[V06-E3](notes/V06-E3-glossary.md)。

### V06-E4 时间线与人物状态（2026-10-23）

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e4-full2 -o tmp_path_retention_count=100` | 510 通过，含 8 项人物状态、8 项时间线、2 项装配与各 1 项工具、接口用例 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 48 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、107 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：状态抽屉的三个标签未在浏览器里走查；位置与生死的抽取质量、已死角色行动的误报率
没有在线模型数据。明细见 [V06-E4](notes/V06-E4-timeline-roster.md)。

### V06-E5 线索与伏笔台账（2026-10-23）

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e5-clean -o tmp_path_retention_count=100`（`CODEBUDDY_SAFE_DELETE_ENABLED=0`） | 525 通过，含 11 项线索用例、2 项装配、1 项工具与 1 项接口用例 |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 49 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、107 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：线索标签未在浏览器里走查；「什么算伏笔」的判定质量与超期提醒的实际效果没有在线
模型数据。明细见 [V06-E5](notes/V06-E5-clues.md)。

### 状态层在线任务验收（2026-10-23）

`scripts/model_acceptance.py` 新增「状态核对」类 4 个样例，在 `deepseek-flash` 上全部通过
（报告：`artifacts/model-acceptance/20261023-112346-580100` 与
`20261023-113449-283000`）：

| 样例 | 核对内容 | 结果 |
|---|---|---|
| `disclosure_extract` | 三章样例核对身份披露 → 下一轮注入 | 登记 2 条带证据披露；未把第 3 章「没有说出旧怨」记为已披露（零误报）；注入含 `state` 分区 |
| `state_and_timeline` | 两位角色第三章状态 + 时间锚点 → 注入 | 2 条快照 + 1 条锚点全部有效；模型对引文区间错位能自我修正重发；注入含 `roster`、`timeline` |
| `clue_plant` | 登记超期未回收线索 → 注入 | 只按可观察事实登记、不补推测；超期项排进未回收名单；注入含 `clues` |
| `glossary_extract` | 登记原文专名 → 注入 | 只登记「青梧旅馆」（place）与「临江旅业公司」（org）；「铜牌」「大堂」判为普通名词、声明的「林远」不重复登记；注入含 `glossary` |

核对轮均未改动任何作品文件；记录证据全部通过「版本 + 区间 + 引文」一致校验。结论只对
`deepseek-flash` 与这些小样例成立；5 条以上线索的裁剪效果尚未覆盖。

分阶段证据（含每条用例清单）见各[阶段记录](README.md)；以下事项**未验证或未实施**，
不作为当前能力：
- **不调模型的完整浏览器项目本次未重跑**：此前的通过结果保留在阶段历史，不计入本次验证。
- **真实模型验收只覆盖一个模型**：结论只对 `deepseek-flash` 成立，不外推到其他模型；
  七类任务由[驱动器](../../scripts/model_acceptance.py)自动核对可判定事实，
  文笔、伏笔与人物动机等判断需要作者看样例。
- **范围追加的证据要求未在在线样例中出现**：模型在同步任务里选择一次登记完整范围；
  该约束由工具层用例覆盖。
- 检查质量（误报、漏报、把回忆当事实）未验收；报告只能说明证据存在，不能证明语义正确。

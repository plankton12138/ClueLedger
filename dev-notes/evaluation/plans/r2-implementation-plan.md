# R2 实施计划：跑通一条可归因的改稿回归

状态为待实施。依据为[评测体系设计](../design.md) §0.5、§B.3、§B.4、§B.8、§B.10 和 §F。R1 已确认完成。

## 目标与取舍

保留现有的「只改第一章结尾」案例。程序核对授权范围、指定对白和直接入稿回执，作者核对改稿是否完成原请求。遇到与预设答复匹配的范围澄清时，评测器可继续一轮，并如实记录首轮提问和最终交付。一次冒烟就应留下足够证据，让作者定位失败或确认交付。

R2 的交付范围是该案例的行为与补答契约、B.10 前两处修正、可离线复核的报告和一次在线冒烟。案例继续引用显式声明检查项的判分器，CLI 和工作台共用运行器及判分结果。多案例共用规则、完整版本身份采集和两版盲选沿用总设计的后续阶段。

## T0 · 本案验证普通对话的直接入稿

| 项目 | 实施口径 |
| --- | --- |
| 产品入口 | 使用普通对话，请求带 `purpose: chat` 和 `context.selected_path`，`context.story` 留空。作者要求改稿的原话授予本轮改第一章结尾的权限。必须项核对直接写入与同轮版本提交回执。候选证据提取由独立夹具验证。 |
| 首轮行为 | `act` 期望直接执行，先问计质量失败，最终交付按 T4 判定。`ask` 要有真实待答问题，且首次回复前作品保持原样。`decline` 只接受可核实的结构化拒绝证据，证据缺失记待复核。 |

历史基线以本机报告和事件为准。旧的 `smoke-3` 对应第三章改雪夜案例，涉及跳过缺章和改变既定天气的授权，属于应停止自动补答的反例。当前第一章案例在 `data/evaluation/runs/smoke-revise-20261117-212007` 已通过 `edit_file` 直接改稿并提交。旧报告把这次直接入稿记成候选通过，目标章输入检查报失败，作者交付题仍待复核。R2 冒烟用新版判据重新验收第一章案例。

## 实施顺序

### T1 · 扩展案例格式，先不改变检查编排

更新[案例加载器](../../../evaluation/harness/schema.py)和[现有案例](../../../evaluation/cases/revise_ch1_ending_farm_tonight.yaml)。校验 `expect.behavior`（`act / ask / decline`）。可选的 `expect.if_asked` 只适用于 `act`，须有非空 `reply`、非空的 `match_questions` 文本列表和正整数 `max`，本案上限为 1。改稿案须有区域锚点，加载时校验初态中存在且唯一。纯提问和拒绝案可以省略区域，检查项须能在无区域时运行。

将本案和[判分器](../../../evaluation/evaluators/revise_v1.yaml)升为第 2 版，沿用作者原话、作品目录、锚点、`evaluator: revise_v1` 和现有套件引用。人工题沿用 `farm_tonight_present`，在判分器设置 `level: human`、`role: required` 和 `required_for_release: true`。`required_for_release` 缺省为 `false`，仅 `human / required` 可启用。

人工题判据为「改后的第一章结尾是否明确写出舒尔茨答应今晚（或当夜）带姜戈去农场查问那三个名字」。作出承诺的人、同行的人、时间、地点与所查对象都须有正文依据。作者记录结论并引用运行结束时的结尾原文。

```yaml
# 现有案例的局部变更；其余作品和步骤仍保留
version: 2
evaluator: revise_v1
expect:
  behavior: act
  if_asked:
    match_questions:
      - "请确认，修改范围是第一章结尾，从“天黑得比钟点早。”起到章末吗？"
      - "这次只改第一章结尾，从“天黑得比钟点早。”改到章末，对吗？"
    reply: 只改第一章结尾，从“天黑得比钟点早。”起到章末，保留姜戈提到五个坑和三个名的原话。
    max: 1
  region:
    path: 正文/0001 拔牙人进镇.md
    from: "天黑得比钟点早。"
    to: END
  preserve:
    - "霍布斯说他今年挖了五个坑，账上只有三个名。"
```

**验证**覆盖错误行为值、空匹配表、空回复、非法次数、无区域的提问案、锚点缺失或重复，以及人工必须标记的加载。通过临时案例和假事件验证。工作台资产页可读取并启动第一章案例，质检页可按原人工题 ID 复核新版试验。

### T2 · 按问题 ID 答复，在同一案例内继续

在[HTTP 客户端](../../../evaluation/harness/client.py)和[运行器](../../../evaluation/harness/runtime.py)中复用产品的[续轮接口](../../../src/hastings/workspace/api.py)。以下条件全部成立时，才用 `reply_to_question` 在同一会话发起新产品轮次。请求正文只含预设答复，`context` 留空，由产品恢复原问题上下文。匹配规则和检查项由评测器本地使用。

1. 当前试验期望 `act`，已配置自动补答。当前轮以 `needs_input` 结束，且同一会话、同一轮有唯一的有效待答问题。重复投递的同一问题 ID 按一条处理。
2. 问题为未回答的 `decision`，`options` 和 `proposal` 均为空，`related` 为空或仅含目标章。问题文本与 `match_questions` 的条目均先做 Unicode NFC、首尾去空白和连续空白折叠，然后逐句比对，保留标点差异。允许的两句都只确认原请求的结尾范围。
3. 答复只复述已授权的结尾范围和指定对白保留要求。当前问题 ID 尚未提交答复，答复次数低于上限，预算和取消状态允许启动新轮。

```http
POST /api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns
Content-Type: application/json

{"text":"只改第一章结尾，从“天黑得比钟点早。”起到章末，保留姜戈提到五个坑和三个名的原话。","request_key":"<本试验对该问题的稳定键>","purpose":"chat","reply_to_question":"<question_id>","context":{}}
```

轮询沿用同一会话的事件游标，以当前 `turn_id` 的 `run_finished` 为等待条件。为试验与问题 ID 固定同一个 `request_key`，保留发问轮、答复轮、问答事件和各轮 Trace。任一条件不满足时保留现场和停止原因，例如缺待答记录、题目不匹配、答复超限、预算耗尽或取消。模块建议、跨章修改、跳过校验或需要作者选择的事项均停在提问轮。

**验证**覆盖旧轮终态先到、一次补答成功、重复问题只答一次、二次提问停止、预期为 `ask`、预算耗尽和取消。用含其他章节、追加授权及模块建议的普通或结构化问题验证停止路径。HTTP 契约测试核对会话、问题 ID、稳定请求键和空上下文。

### T3 · 修正入稿回执与过程定位

以 [证据提取](../../../evaluation/harness/evidence.py)、[检查项](../../../evaluation/harness/checks.py) 和 [环节计算](../../../evaluation/harness/stages.py) 为边界。在线判分和离线 `score` 共用同一套证据提取，不各写一份。

| 证据 | 实施口径 |
| --- | --- |
| 候选入稿 | 展开 `story_candidate_committed.paths`，每条保留路径、轮次和候选/变更 ID。没有该结构化事件的 Git 提交不算候选。 |
| 直接入稿 | 用同轮成功的 `write_file / edit_file` 调用与对应的 `file_changed`（真实变化）识别路径，再核对同轮 `run_finished` 的提交标识和 `changed_paths`。只看到磁盘差异、工具开始或一次 Git 提交，均不足以判定直接入稿。 |
| 范围登记 | 用成功发生的 `task_scope_set / task_scope_extended` 的 `grants` 判定，不从 `tool_started.args` 猜路径（产品会裁剪参数）。范围要在实际写入轮的写入前生效；本案只允许目标章更新，不是「包含目标章就算通过」。用 `set_task_scope / extend_task_scope` 对应的 Trace span 定位。 |
| 读取与候选环节 | 判正文输入使用首次正文写入或候选保存之前的快照，不能让写完后读取补过。候选生成用 `story_candidate_saved`；核对用 `submit_story_candidate_review` 与 `story_candidate_reviewed.approved`；修补用 `repair_story_candidate` 的结果与后续复核。调用发生或工具返回成功不等于候选通过。 |
| 路径与判分一致 | 将本案的候选必须项替换为直接入稿必须项。候选生成、核对和修补标为「不适用」。在判分器加入 `scope_matches_auth` 质量检查，实际越界修改按 `only_region_changed` 必须项判定。候选路径由独立夹具验证。最早失败环节须能定位到对应轮次和证据。 |

**验证：** 用真实形状的假事件覆盖多路径候选、普通 Git 提交不能冒充候选、直接修改缺回执、路径或轮次不一致、超范围登记、核对未批准却返回成功、修补后复核、先写后读。旧证据缺字段时明确显示证据不足，不补造路径或通过结论。

### T4 · 落盘判分，验证在线与离线一致

用结构化提问、写入和各轮终态记录**首轮行为、最终结果、提问次数**和停止原因。实际完成须同时有成功终态和入稿证据。尝试结束时一次性采集最终文件树，写入 `evidence/after.json`，并用同一快照生成差异和在线判分。离线 `score`、工作台差异与人工引用使用落盘的初态、终态、事件、输入和 Trace，确保复核看见的是当时的稿件。

已完成的试验写入 `must_outcome` 和 `quality_outcome`。程序必须项和标记 `required_for_release` 的人工项都参与必须项结论。任一必须项确认失败时为 `fail`，其余情况下存在缺证或待人工确认的必须项时为 `pending_review`，全部适用必须项都有通过证据时为 `pass`。质量结论对适用的 `quality / required` 项按同样的优先级汇总。无适用质量项时记 `not_applicable`。「明确请求却先问」计入质量失败。

| 实际表现 | 必须项结论 | 质量与原因 |
| --- | --- | --- |
| 直接改稿，各项证据齐全 | 作者确认前为待复核，确认完成后通过 | 各质量检查按证据判定 |
| 先问，按预设续轮完成改稿 | 作者确认前为待复核，确认完成后通过 | 直接执行检查失败，保留两轮记录 |
| 产品轮次已结束，证实有未授权修改、改稿未交付，或作者裁定未完成 | 失败 | 保留具体原因，例如问题不匹配、答复超限或结果缺少关键交付 |
| 缺少回执、终态快照等必须项证据，无法核实实际结果 | 待复核 | 标明缺失的证据和所属轮次 |
| `ask` 场景产生相关待答问题且作品不变 | 该行为必须项通过 | 正文入稿项不适用，自动补答次数为零 |

网络故障、限流、等待超时和未运行分别记入基础设施错误或覆盖不足。已开始的试验保留已采到的证据，写明中断点。识别基于 HTTP 异常与产品错误事件，来源无法确认的错误保留原始错误码及观察点。覆盖不足的试验不能满足本场景必须覆盖的条件。

在线判分、离线重评和人工复核保存后共用同一汇总逻辑。R2 冒烟的 CLI 和工作台用必须项结论判定场景是否通过，并展示质量结论。待复核和覆盖不足时，R2 冒烟门禁不得显示通过。现有重复运行档位按总设计 §B.9 保留。

每次试验记录案例版本、运行时的案例预期和所用判分器版本。离线重评先校验案例版本与证据格式，同一案例版本允许用新版判分器重判，报告明确记录原判分器版本和此次重评版本。题目与证据相同时保留已有人工结论。旧报告可阅读；旧版或缺版本的证据包缺少重评所需信息时，返回原因并保留原报告。工作台以试验保存的期望、快照和复核结论展示新版报告。

**验证**落盘一次多轮证据，分别比较在线判分、离线重评和人工裁决后的报告。覆盖「先问后交付且待复核」「先问后交付且作者确认通过」「作者判定未完成」「证据不足」「覆盖不足」以及旧版拒绝重评。落盘后改动试验的 `work/` 再重评，检查项、最早失败环节、作品差异和已有人工结论应与原证据一致。

### T5 · 一次在线冒烟，保留能复查的结论

先跑评测确定性测试，再用现有[在线验收配置脚本](../../../scripts/online_acceptance_config.py)从仓库根 `.env` 生成临时连接配置。实施检出须包含脚本所用的 `scripts/online_env.py`。配置使用未占用的目录，密钥存于系统凭据。启动本地模式服务时显式指定配置和独立状态目录。开跑前确认服务选用临时档案，其模型名、端点主机与 `.env` 一致。运行器打开试验副本后开启并核对输入采集设置。

```powershell
# 终端一，从仓库根启动验收服务
uv run pytest tests/evaluation
uv run python -m evaluation list
uv run python scripts/online_acceptance_config.py create data/test-tmp/r2-online/config.yml --env-file .env
uv run python -m hastings.server --mode local --host 127.0.0.1 --port 8947 --config data/test-tmp/r2-online/config.yml --state-dir data/test-tmp/r2-online/state

# 终端二，从仓库根运行单次试验及离线重评
uv run python -m evaluation run --base-url http://127.0.0.1:8947 --suite smoke_revise --out data/evaluation/runs/r2-smoke-1
uv run python -m evaluation score data/evaluation/runs/r2-smoke-1
```

使用现有[冒烟套件](../../../evaluation/suites/smoke_revise.yaml)跑一次第一章试验，允许包含符合 T2 的补答轮。检查实际 `model_request_started.model` 和输入快照，确认记录属于本次服务和试验。执行命令返回失败或待复核时，使用本次落盘报告判断具体原因。作者在现有质检入口提交 T1 的交付判断后，以同一次试验重评确认结论。遇到失败先核对 Trace，明确要改的产品行为、案例输入或证据采集问题，再决定是否单例复验。

**完成条件**为一次指定在线模型的试验实际改动目标结尾，保留授权区域外的受检内容和指定对白，并有同轮直接入稿回执。报告可定位对应范围登记、回执和 Trace，离线重评可复查。作者确认完整交付后，必须项结论才可记通过。若先问后改成，质量记录仍须反映提问。记录报告目录、实际分支、模型名、端点主机名、必须项和质量项结论及复核证据。

**收尾**时停止本次验收服务，并在仓库根执行 `uv run python scripts/online_acceptance_config.py cleanup data/test-tmp/r2-online/config.yml`。本机报告与证据保存在运行目录。按实际验收结果更新[设计的建设顺序](../design.md#05-建设顺序与状态)中的 R2 状态。

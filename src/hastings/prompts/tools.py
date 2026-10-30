"""工具说明：模型可见的工具描述与使用指引。

这里只放文本，不判断本轮该提供哪些工具，也不决定参数结构。业务模块按工具模式
选择工具并引用这些常量；改动这些文字等于改动模型看到的行为。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

__all__ = [
    "APPLY_CHANGE_SET_ID_PARAM_DESCRIPTION",
    "APPLY_EXPECTED_DIGEST_PARAM_DESCRIPTION",
    "APPLY_MODULE_CHANGES_DESCRIPTION",
    "APPLY_MODULE_CHANGES_GUIDELINES",
    "ASK_AUTHOR_DESCRIPTION",
    "ASK_AUTHOR_GUIDELINES",
    "ASK_AUTHOR_OPTIONS_PARAM_DESCRIPTION",
    "ASK_AUTHOR_PATHS_PARAM_DESCRIPTION",
    "ASK_AUTHOR_QUESTION_PARAM_DESCRIPTION",
    "ASSEMBLY_DIGEST_PARAM_DESCRIPTION",
    "ATTACH_MODULE_DESCRIPTION",
    "ATTACH_MODULE_GUIDELINES",
    "ATTACH_MODULE_LABEL_PARAM_DESCRIPTION",
    "AUTHORIZATION_PARAM_DESCRIPTION",
    "AUTHORIZATION_SEQ_PARAM_DESCRIPTION",
    "CARRIED_FROM_SEQ_PARAM_DESCRIPTION",
    "DELETE_FILE_DESCRIPTION",
    "DETACH_MODULE_BINDING_PARAM_DESCRIPTION",
    "DETACH_MODULE_DESCRIPTION",
    "DETACH_MODULE_GUIDELINES",
    "EDIT_BASE_DIGEST_PARAM_DESCRIPTION",
    "EDIT_BODY_PARAM_DESCRIPTION",
    "EDIT_FILE_DESCRIPTION",
    "EDIT_FILE_DIGEST_PARAM_DESCRIPTION",
    "EDIT_FILE_GUIDELINES",
    "EDIT_FILE_NEW_TEXT_PARAM_DESCRIPTION",
    "EDIT_FILE_OLD_TEXT_PARAM_DESCRIPTION",
    "EDIT_FIND_PARAM_DESCRIPTION",
    "EVIDENCE_PARAM_DESCRIPTION",
    "EVIDENCE_PATH_PARAM_DESCRIPTION",
    "EXTENDABLE_PARAM_DESCRIPTION",
    "EXTEND_ACTIONS_PARAM_DESCRIPTION",
    "EXTEND_TASK_SCOPE_DESCRIPTION",
    "EXTEND_TASK_SCOPE_GUIDELINES",
    "GIT_DIFF_DESCRIPTION",
    "GIT_DIFF_GUIDELINES",
    "GIT_LOG_DESCRIPTION",
    "GRANT_TARGET_PARAM_DESCRIPTION",
    "LIBRARY_ENTRY_PARAM_DESCRIPTION",
    "LIBRARY_PUBLISH_PARAM_DESCRIPTION",
    "LIBRARY_REVISION_PARAM_DESCRIPTION",
    "LIBRARY_SEARCH_PARAM_DESCRIPTION",
    "LIST_FILES_DESCRIPTION",
    "LIST_FILES_GUIDELINES",
    "LIST_FILES_LIMIT_PARAM_DESCRIPTION",
    "LIST_FILES_OFFSET_PARAM_DESCRIPTION",
    "LIST_MODULES_DESCRIPTION",
    "LIST_MODULES_GUIDELINES",
    "LIST_MODULES_OFFSET_PARAM_DESCRIPTION",
    "MODULE_CONTENT_GRANTS_PARAM_DESCRIPTION",
    "MODULE_GRANTS_PARAM_DESCRIPTION",
    "MODULE_PURPOSE_PARAM_DESCRIPTION",
    "MODULE_SLOT_PARAM_DESCRIPTION",
    "MOVE_FILE_DESCRIPTION",
    "PREPARE_BASE_REVISION_PARAM_DESCRIPTION",
    "PREPARE_BINDING_PARAM_DESCRIPTION",
    "PREPARE_BODY_PARAM_DESCRIPTION",
    "PREPARE_EDITS_PARAM_DESCRIPTION",
    "PREPARE_MODULE_CHANGES_DESCRIPTION",
    "PREPARE_MODULE_CHANGES_GUIDELINES",
    "PREPARE_NAME_PARAM_DESCRIPTION",
    "PREPARE_OPERATIONS_PARAM_DESCRIPTION",
    "PREPARE_OPERATION_ID_PARAM_DESCRIPTION",
    "PROBE_ECHO_PARAM_DESCRIPTION",
    "PROBE_TOOL_DESCRIPTION",
    "PROPOSE_STORY_SCENE_DESCRIPTION",
    "PROPOSE_STORY_STRUCTURE_DESCRIPTION",
    "PROPOSE_STORY_STRUCTURE_OPERATIONS_PARAM_DESCRIPTION",
    "PUBLISH_MODULE_DESCRIPTION",
    "PUBLISH_MODULE_GUIDELINES",
    "PUBLISH_MODULE_ID_PARAM_DESCRIPTION",
    "QUERY_RECORDS_CHAPTER_PATHS_PARAM_DESCRIPTION",
    "QUERY_RECORDS_CURSOR_PARAM_DESCRIPTION",
    "QUERY_RECORDS_DESCRIPTION",
    "QUERY_RECORDS_ENTITY_IDS_PARAM_DESCRIPTION",
    "QUERY_RECORDS_GUIDELINES",
    "QUERY_RECORDS_INCLUDE_PLANS_PARAM_DESCRIPTION",
    "QUERY_RECORDS_KINDS_PARAM_DESCRIPTION",
    "QUERY_RECORDS_LIMIT_PARAM_DESCRIPTION",
    "QUERY_RECORDS_MODULE_IDS_PARAM_DESCRIPTION",
    "QUERY_RECORDS_QUERY_PARAM_DESCRIPTION",
    "READ_FILE_CURSOR_PARAM_DESCRIPTION",
    "READ_FILE_DESCRIPTION",
    "READ_FILE_GUIDELINES",
    "READ_FILE_LIMIT_PARAM_DESCRIPTION",
    "READ_FILE_OFFSET_PARAM_DESCRIPTION",
    "READ_FILE_PATH_PARAM_DESCRIPTION",
    "READ_IDEA_DESCRIPTION",
    "READ_IDEA_GUIDELINES",
    "READ_IDEA_REVISION_PARAM_DESCRIPTION",
    "READ_LIBRARY_RECORD_DESCRIPTION",
    "READ_LIBRARY_RECORD_GUIDELINES",
    "READ_PREVIOUS_STORY_CANDIDATE_DESCRIPTION",
    "READ_RECORD_DESCRIPTION",
    "READ_RECORD_GUIDELINES",
    "READ_STATE_AS_OF_CHAPTER_PARAM_DESCRIPTION",
    "READ_STATE_DESCRIPTION",
    "READ_STATE_ENTITY_PARAM_DESCRIPTION",
    "READ_STORY_DESCRIPTION",
    "READ_STORY_GUIDELINES",
    "READ_STORY_NODE_IDS_PARAM_DESCRIPTION",
    "REFERENCE_QUOTE_PARAM_DESCRIPTION",
    "REFERENCE_REQUIRED_PARAM_DESCRIPTION",
    "REFERENCE_SEQ_PARAM_DESCRIPTION",
    "REGISTER_REFERENCES_DESCRIPTION",
    "REGISTER_REFERENCES_GUIDELINES",
    "REPAIR_STORY_CANDIDATE_DESCRIPTION",
    "REQUEST_REVIEW_DESCRIPTION",
    "REQUEST_REVIEW_GUIDELINES",
    "REVIEW_PARAMETERS_REQUIREMENT_PARAM_DESCRIPTION",
    "REVIEW_PATHS_PARAM_DESCRIPTION",
    "REVIEW_REQUIREMENT_PARAM_DESCRIPTION",
    "REVIEW_SEQ_PARAM_DESCRIPTION",
    "REVIEW_TARGET_PATHS_PARAM_DESCRIPTION",
    "REVIEW_UNCOVERED_PARAM_DESCRIPTION",
    "SAVE_IDEA_BODY_PARAM_DESCRIPTION",
    "SAVE_IDEA_DESCRIPTION",
    "SAVE_IDEA_GUIDELINES",
    "SAVE_REVIEW_REPORT_GUIDELINES",
    "SCOPE_VERSION_PARAM_DESCRIPTION",
    "SEARCH_IDEAS_DESCRIPTION",
    "SEARCH_IDEAS_GUIDELINES",
    "SEARCH_IDEAS_QUERY_PARAM_DESCRIPTION",
    "SEARCH_MODULE_LIBRARY_DESCRIPTION",
    "SEARCH_MODULE_LIBRARY_GUIDELINES",
    "SEARCH_MODULE_LIBRARY_QUERY_PARAM_DESCRIPTION",
    "SEARCH_TEXT_DESCRIPTION",
    "SEARCH_TEXT_GUIDELINES",
    "SEARCH_TEXT_LIMIT_PARAM_DESCRIPTION",
    "SEARCH_TEXT_QUERY_PARAM_DESCRIPTION",
    "SET_TASK_SCOPE_DESCRIPTION",
    "SET_TASK_SCOPE_GUIDELINES",
    "SUBMIT_STORY_CANDIDATE_REVIEW_DESCRIPTION",
    "TASK_DENIED_PATHS_PARAM_DESCRIPTION",
    "TASK_GRANTS_PARAM_DESCRIPTION",
    "TASK_MODE_PARAM_DESCRIPTION",
    "TASK_NOTE_PARAM_DESCRIPTION",
    "TASK_REQUIRED_MODULE_REFERENCES_PARAM_DESCRIPTION",
    "TASK_REQUIRED_REFERENCES_PARAM_DESCRIPTION",
    "TASK_SUMMARY_PARAM_DESCRIPTION",
    "UPDATE_STORY_DESCRIPTION",
    "UPDATE_STORY_GUIDELINES",
    "UPDATE_STORY_NO_CHANGE_PARAM_DESCRIPTION",
    "UPDATE_STORY_NOTE_PARAM_DESCRIPTION",
    "UPDATE_STORY_OPERATIONS_PARAM_DESCRIPTION",
    "WRITE_FILE_DESCRIPTION",
    "WRITE_FILE_DIGEST_PARAM_DESCRIPTION",
    "WRITE_FILE_GUIDELINES",
    "WRITE_FILE_TEXT_PARAM_DESCRIPTION",
    "save_review_report_description",
]

LIST_FILES_DESCRIPTION = (
    "列出正文章节、角色小传以及线索与真相文件的路径和标题（只读）。"
    "条目很多时用 offset 继续看其余部分。"
)

LIST_FILES_GUIDELINES = ("不确定有哪些文件时先调用；无参数。",)

READ_FILE_DESCRIPTION = (
    "读取受管文件的文本，返回内容、实际字符区间、行号、总行数与 digest。"
    "digest 是后续写入的凭据。长文用 offset/limit 按行分页，或带上一次返回的 "
    "next_cursor 按字符继续；单次最多 16000 字符。"
)

READ_FILE_GUIDELINES = (
    "改写前必须读；读完保留返回的 digest 供写入使用。",
    "truncated 为真时按 next_cursor 继续读，不要假设已看完全文。",
)

SEARCH_TEXT_DESCRIPTION = "在正文与设定中逐行搜索关键词，返回路径、行号与摘录（只读）。"

SEARCH_TEXT_GUIDELINES = ("找伏笔、人物首次出现等具体内容时使用。",)

REGISTER_REFERENCES_DESCRIPTION = (
    "登记作者在消息里明确要求参考的文件，或任务中发现的相关材料。"
    "作者文本来源要带作者原话短引文；发现的材料要先读过并引用其中一句原文。"
)

REGISTER_REFERENCES_GUIDELINES = (
    "作者说「参考第一章」时登记它，并设 required；只是自己搜到的线索不必设 required。",
    "客户端已选的参考由应用登记，不要重复登记。",
)

SET_TASK_SCOPE_DESCRIPTION = (
    "登记这一轮要做什么、作者在哪句话里授权、允许改哪些确切文件或模块内容。"
    "没有登记范围的写工具会被拒绝；纯讨论不必登记。"
)

SET_TASK_SCOPE_GUIDELINES = (
    "要改文件时先登记；作者没明确要求修改时用 read_only 或干脆不登记。",
    "grants、denied_paths、required_references 只写确切文件路径，不要填模块名称。",
    "模块必读参考用 required_module_references，按模块、版本与条目 ID 登记。",
    "本轮成功新建的章节可继续校订，无需追加 update；已有章节仍需登记 update。",
    "普通资料创作登记 module_content_grants；"
    "旧设定文件须有作者明确指定文件或沿用旧目录的要求。",
)

EXTEND_TASK_SCOPE_DESCRIPTION = (
    "在可发现范围内追加与原目标有关的具体文件；必须给出这句原文来自哪份文件、"
    "原文本身，并带上当前范围版本。"
)

EXTEND_TASK_SCOPE_GUIDELINES = (
    "只在登记时标记了 extendable 的任务里使用；固定范围会被拒绝。",
)

REQUEST_REVIEW_DESCRIPTION = (
    "作者明确说「改完检查」时登记检查要求；修改完成后会进入只读检查阶段。"
    "没有这句话就不要调用。"
)

REQUEST_REVIEW_GUIDELINES = ("只在作者明确要求检查时使用；普通修改不隐含开启检查。",)

WRITE_FILE_DESCRIPTION = (
    "整体写入章节或作者明确要求使用的旧设定文件。新建文件不带 digest；"
    "改写已有文件必须带 read_file 得到的 digest。模块内容用 prepare_module_changes。"
)

WRITE_FILE_GUIDELINES = ("适合新建章节或整章重写；局部修改优先 edit_file。",)

EDIT_FILE_DESCRIPTION = (
    "局部修改章节或作者明确要求使用的旧设定文件，返回新的 digest。"
    "原文出现 0 次或多次都会失败。模块条目的局部修改用 prepare_module_changes。"
)

EDIT_FILE_GUIDELINES = (
    "只对本轮获准修改的文件使用；不要为小改动重写整章。",
    "已有模块可承接的资料修改在原模块内完成，不改同名旧文件或相关章节代替。",
)

MOVE_FILE_DESCRIPTION = "在同类目录内重命名章节或角色小传（线索、真相不可移动），需要 digest。"

DELETE_FILE_DESCRIPTION = "删除章节或单个角色小传（线索、真相不可删除），需要 digest。"

GIT_DIFF_DESCRIPTION = "查看工作区相对上次提交的差异（只读）。"

GIT_DIFF_GUIDELINES = ("确认自己改了什么时使用；新文件不出现在工作区差异里。",)

GIT_LOG_DESCRIPTION = "查看最近的提交标题与时间（只读）。"

READ_STATE_DESCRIPTION = (
    "查看已有记录：domain=disclosure（默认）仅给旧版小传的身份披露，不读取模块人物。"
    "没有身份记录是合法空结果，不要按姓名拼旧路径重试。domain=roster 给位置、生死与"
    "立场，domain=timeline 给故事时间，domain=clues 给线索台账，domain=glossary 给"
    "专名。带 entity 可查某个角色的条目与原文依据。"
)

LIST_MODULES_DESCRIPTION = "列出本轮装配里的模块实例与精确版本，含条目数；不接受未装模块。"

LIST_MODULES_GUIDELINES = ("只在这里出现的模块版本可以读取；不要按名字去库里找。",)

QUERY_RECORDS_DESCRIPTION = (
    "查询本轮已装模块的条目与本作品观察：可按模块、人物、章区间与关键词。"
    "返回来源、版本、命中原因与扫描量；命中不等于读过全文。"
)

QUERY_RECORDS_GUIDELINES = (
    "先给具体模块、人物、章区间或关键词；没有命中时看 scope 与 warnings。",
    "命中只用于定位；原文用 read_record 或 read_file，不要凭摘要写作。",
)

READ_RECORD_DESCRIPTION = "按本轮装配的完整引用读取模块条目原文与区间；未装版本会被拒绝。"

READ_RECORD_GUIDELINES = (
    "module_ref 四段必须来自 list_modules/query_records；"
    "不要用库 head 代替精确版本。",
    "truncated 为真时按 next_cursor 用 start 继续读。",
)

SEARCH_MODULE_LIBRARY_DESCRIPTION = (
    "仅在作者明确要求从模块库挑选模块时使用；返回有界候选与精确版本，"
    "不自动装入、升级或修改任何内容。"
)

SEARCH_MODULE_LIBRARY_GUIDELINES = (
    "先在范围里登记 library_search；作者没有明确要求就不要搜索。",
    "搜索结果只是候选；装入要由作者确认，不能自己改装配。",
)

READ_LIBRARY_RECORD_DESCRIPTION = (
    "作者明确要求参考库内容时，只读一个精确版本的目录或条目原文；"
    "不自动装入，也不开放其他模块。"
)

READ_LIBRARY_RECORD_GUIDELINES = (
    "只在作者明确要求引用模块库内容且已登记 library_search 时使用。",
    "单轮引用不改变装配；要长期使用仍需作者确认装入。",
)

ATTACH_MODULE_DESCRIPTION = (
    "把本轮范围登记过的精确模块版本装入本作品；只改装配清单，不改库与正文。"
    "提交后本轮结束。"
)

ATTACH_MODULE_GUIDELINES = (
    "只在作者明确要求装入、且范围已登记这个精确版本时使用。",
    "不要按模块名猜版本；版本应从 list_modules/query_records 或库搜索里取。",
    "提交装配后本轮结束；写作下一轮才会看到新模块。",
)

DETACH_MODULE_DESCRIPTION = (
    "把本轮范围登记过的绑定从本作品卸下；后续默认查询与依赖展开都会排除它。"
    "正文事实不受影响，提交后本轮结束。"
)

DETACH_MODULE_GUIDELINES = (
    "先用 list_modules 拿到绑定 ID；不要按模块名猜。",
    "卸下只影响后续查询；已经写进正文的事实仍然保留。",
)

PUBLISH_MODULE_DESCRIPTION = (
    "把作者要求的条目发布到全局模块库：已有模块发布新版本，或新建一个实例。"
    "不自动装入本作品，也不改其他作品的绑定。"
)

PUBLISH_MODULE_GUIDELINES = (
    "只在作者明确要求保存或发布到模块库、且范围已登记 library_publish 时使用。",
    "发布不隐含装入；要用于本作品时另行登记装配操作。",
)

PREPARE_MODULE_CHANGES_DESCRIPTION = (
    "把本次创作的多份内容写成任务进度：可同时新建多份或修改已有模块。"
    "只校验结构与目标，不改库装配；随后用 apply_module_changes 一次应用。"
)

PREPARE_MODULE_CHANGES_GUIDELINES = (
    "先用 set_task_scope 登记 module_content_grants，再提交内容。",
    "本轮全部新建与修改操作放在同一个 operations 中，准备完整后统一应用。",
    "准备尚未应用时可另备完整变更集修订，包含全部操作，再应用完整结果。",
    "新建正文由服务适配为自由资料；不要为每个字段建模块，也不建空模块。",
    "修改前必须读取实际条目与版本，并带上 base_digest。",
    "局部修改也使用本工具；只改作者指定的信息，保留其他条目、字段与原文。",
)

APPLY_MODULE_CHANGES_DESCRIPTION = (
    "保存已准备的内容并一次性更新当前作品装配。成功后本轮进入收尾："
    "只读取结果并回复，不再继续写入。"
)

APPLY_MODULE_CHANGES_GUIDELINES = (
    "调用前确认变更集已包含本轮全部内容；应用成功后本轮只读，无法再准备遗漏内容。",
    "内容保存失败时保留生成稿，按返回的阶段说明可继续的范围。",
    "应用成功后不要继续改文件；用实际结果说明已用于本作品的内容。",
)

READ_IDEA_DESCRIPTION = "读取作者本轮选中、或本轮明确搜索到的灵感修订；未授权的灵感拒绝。"

READ_IDEA_GUIDELINES = (
    "只读本轮选中的修订；作者改过之后不要拿旧修订当当前。",
    "灵感是作者笔记，不是已发生的事实。",
)

SEARCH_IDEAS_DESCRIPTION = "仅在作者明确要求找灵感时使用；返回摘要与当前修订，不自动写入作品。"

SEARCH_IDEAS_GUIDELINES = (
    "作者没有明确要求就不要搜索灵感；普通目录与文件工具读不到灵感。",
    "搜索结果只在本轮可见，下一轮要重新获得许可。",
)

SAVE_IDEA_DESCRIPTION = "把作者明确要求记下的内容保存为灵感；不改成正式设定，也不自动采用。"

SAVE_IDEA_GUIDELINES = (
    "只在作者说记下来、保存为灵感时使用；不要自动收集讨论内容。",
    "保存后告诉作者已记录，但不要把它当成正文设定。",
)

SAVE_REVIEW_REPORT_GUIDELINES = (
    "先读完目标再判断；每条问题至少一处原文证据，跨文件冲突列双方出处。",
    "没读完或来源不可用时用 incomplete，并写明未覆盖的部分。",
)

ASK_AUTHOR_DESCRIPTION = (
    "读取相关材料后，目标仍无法唯一定位或本轮要求存在未决冲突时，"
    "记录一个必须由作者决定的问题并结束本轮，回答后继续原请求。"
    "作者已明确的新值直接采用；模块拆分、命名与已授权的人物创作不需要问。"
)

ASK_AUTHOR_GUIDELINES = (
    "能自行判断的不要问；一次只问一个必须由作者决定的问题。",
    "作者明确要求先提问时按要求提问；明确的局部替换不需要重复确认。",
    "「其余不变」时保留范围外资料，不因旧文件或章节的潜在影响阻断修改。",
    "调用后本轮结束，不要再写入或假装已经继续。",
)

READ_PREVIOUS_STORY_CANDIDATE_DESCRIPTION = (
    "读取本操作先前代次或作者回答对应的候选，核对基础版本。"
)

PROPOSE_STORY_STRUCTURE_DESCRIPTION = (
    "登记与候选正文一起提交的结构变更；操作格式与 update_story 相同。"
    "只改作者本轮点到或界面选中的对象，提交前做对象授权与图结构校验。"
)

PROPOSE_STORY_SCENE_DESCRIPTION = (
    "先完整读取并登记作者授权，再提交完整候选文本。"
    "新章节用 base_digest=absent；核对通过后才保存正式正文。"
)

READ_STORY_DESCRIPTION = (
    "读取当前方案的节点、线索、关系与约定，返回精确 ID 与修订（只读）。"
    "改结构之前先读一次，避免凭标题猜目标。"
)

READ_STORY_GUIDELINES = ("要改结构或核对线索时先读；不要凭标题猜 ID。",)

UPDATE_STORY_DESCRIPTION = (
    "提交一组结构变更。operations 的每项必须有 op 字段，取值只能是："
    "add_node、update_node、remove_node、add_clue、update_clue、remove_clue、"
    "add_thread、update_thread、remove_thread、connect、disconnect、"
    "add_constraint、update_constraint、remove_constraint、set_event_time、"
    "unset_event_time、bind_prose、unbind_prose、record_assessment、"
    "remove_assessment。"
    "add_node/update_node 的 node 必须含 id、title、action；"
    "add_clue/update_clue 的 clue 必须含 id、title（update 还要有原 clue_id）；"
    "connect 的 edge 必须含 id、from_id、to_id、kind。"
    "字段名与 read_story 返回完全一致，不要发明 description、summary 等额外字段；"
    "一次调用只描述这一批变更，失败会返回原因，可修正后重试。"
    "确认无需改动时改传 no_change，列出本轮读过的对象与理由，不要传空 operations。"
)

UPDATE_STORY_GUIDELINES = (
    "作者明确要求改结构时用；只改他要求的对象，不顺手重组整份方案。",
    "确认作者要求已经满足、确实无需改动时，用 no_change 列出本轮读过的对象与理由；"
    "不能只回复「已经存在」。",
)

SUBMIT_STORY_CANDIDATE_REVIEW_DESCRIPTION = (
    "逐项提交候选核对；所有通过结论都必须引用候选原文。"
)

REPAIR_STORY_CANDIDATE_DESCRIPTION = (
    "只修改报告指出的候选问题；一次调用，不修改约定或正式正文。"
)

PROBE_TOOL_DESCRIPTION = "连接测试用的固定工具：无论收到什么参数，都返回 pong。"

# -- 工具参数描述 ----------------------------------------------------------

LIST_FILES_OFFSET_PARAM_DESCRIPTION = "从第几条开始，默认 1"
LIST_FILES_LIMIT_PARAM_DESCRIPTION = "最多返回多少条，默认 200"
READ_FILE_PATH_PARAM_DESCRIPTION = "如 正文/0001 雨夜.md"
READ_FILE_OFFSET_PARAM_DESCRIPTION = "起始行号，默认 1"
READ_FILE_LIMIT_PARAM_DESCRIPTION = "读取行数，默认 200"
READ_FILE_CURSOR_PARAM_DESCRIPTION = "字符游标：上次返回的 next_cursor，用于继续读同一位置之后"
SEARCH_TEXT_QUERY_PARAM_DESCRIPTION = "要查找的文字"
SEARCH_TEXT_LIMIT_PARAM_DESCRIPTION = "最多条数，默认 20，上限 50"

REFERENCE_REQUIRED_PARAM_DESCRIPTION = "写作品前是否必须读完；作者明确说「参考」时为真"
REFERENCE_QUOTE_PARAM_DESCRIPTION = "作者原话短引文，或文件里的一句原文"
REFERENCE_SEQ_PARAM_DESCRIPTION = "可选：作者消息所在事件的 seq"

TASK_MODE_PARAM_DESCRIPTION = "read_only 只读；edit 允许按 grants 修改"
TASK_SUMMARY_PARAM_DESCRIPTION = "一句话说明本轮任务理解"
AUTHORIZATION_PARAM_DESCRIPTION = "作者原话里的短引文，必须与作者消息一致"
AUTHORIZATION_SEQ_PARAM_DESCRIPTION = "可选：授权那句话所在事件的 seq；不确定就省略"
CARRIED_FROM_SEQ_PARAM_DESCRIPTION = (
    "仅承接旧请求时填写那条作者消息的 seq；独立任务省略，"
    "不要用 0 占位，也不要填写当前授权消息的序号"
)
TASK_GRANTS_PARAM_DESCRIPTION = "允许的操作与确切路径；移动另带 target"
GRANT_TARGET_PARAM_DESCRIPTION = "仅 move 时填目标路径；其他操作省略"
TASK_DENIED_PATHS_PARAM_DESCRIPTION = "明确不许改的路径，例如「不动人物设定」时的小传"
TASK_REQUIRED_REFERENCES_PARAM_DESCRIPTION = (
    "必读的作品文件确切路径；模块条目填 required_module_references，不能填名称"
)
TASK_REQUIRED_MODULE_REFERENCES_PARAM_DESCRIPTION = (
    "必读的已装模块条目，引用来自 query_records；写入前须读完精确版本"
)
LIBRARY_SEARCH_PARAM_DESCRIPTION = "作者明确要求从全局模块库查找时才设 true；只开搜索"
MODULE_GRANTS_PARAM_DESCRIPTION = (
    "作者明确要求装卸时才登记；装入给模块与精确版本，卸下给绑定 ID"
)
MODULE_CONTENT_GRANTS_PARAM_DESCRIPTION = (
    "本轮要保存到当前作品的内容操作：新建用稳定槽位，修改用绑定、模块与基础版本"
)
MODULE_SLOT_PARAM_DESCRIPTION = "新建：本任务稳定的槽位，与 operation_id 一致"
MODULE_PURPOSE_PARAM_DESCRIPTION = "这次内容用来做什么"
LIBRARY_PUBLISH_PARAM_DESCRIPTION = "作者明确要求把内容发布到全局模块库时才设 true"
EXTENDABLE_PARAM_DESCRIPTION = "任务允许发现相关文件后追加范围（例如「同步相关章节」）"
EXTEND_ACTIONS_PARAM_DESCRIPTION = "可追加范围时允许的操作类别"
TASK_NOTE_PARAM_DESCRIPTION = "可选说明"

SCOPE_VERSION_PARAM_DESCRIPTION = "当前范围版本"
EVIDENCE_PATH_PARAM_DESCRIPTION = "这句原文来自哪份文件：必须本轮已读过"
EVIDENCE_PARAM_DESCRIPTION = "为什么这个文件属于本次任务：引用实际读到的原文"

REVIEW_REQUIREMENT_PARAM_DESCRIPTION = "要检查什么"
REVIEW_SEQ_PARAM_DESCRIPTION = "可选：那句话所在事件的 seq"
REVIEW_PATHS_PARAM_DESCRIPTION = "可选：要检查的文件；不填按本轮修改的目标推断"

WRITE_FILE_TEXT_PARAM_DESCRIPTION = "完整的新内容（UTF-8 文本）"
WRITE_FILE_DIGEST_PARAM_DESCRIPTION = "改写已有文件时必填"
EDIT_FILE_OLD_TEXT_PARAM_DESCRIPTION = "要被替换的原文，必须唯一"
EDIT_FILE_NEW_TEXT_PARAM_DESCRIPTION = "替换后的文字"
EDIT_FILE_DIGEST_PARAM_DESCRIPTION = "read_file 得到的 digest"

READ_STATE_ENTITY_PARAM_DESCRIPTION = (
    "仅使用不带 entity 的身份概览实际返回的单个 path，不填人物名或模块引用"
)
READ_STATE_AS_OF_CHAPTER_PARAM_DESCRIPTION = "本轮所写章号；不会超过已固定的任务时点"

LIST_MODULES_OFFSET_PARAM_DESCRIPTION = "从第几个模块开始；用上一页的 next_cursor"
QUERY_RECORDS_QUERY_PARAM_DESCRIPTION = "关键词或别名"
QUERY_RECORDS_KINDS_PARAM_DESCRIPTION = "条目种类，如 character、author_event"
QUERY_RECORDS_MODULE_IDS_PARAM_DESCRIPTION = "只查这些已装实例"
QUERY_RECORDS_ENTITY_IDS_PARAM_DESCRIPTION = "人物或作者事件的条目 ID"
QUERY_RECORDS_CHAPTER_PATHS_PARAM_DESCRIPTION = "只看这些正文观察"
QUERY_RECORDS_INCLUDE_PLANS_PARAM_DESCRIPTION = "是否返回计划类模块；默认 true"
QUERY_RECORDS_CURSOR_PARAM_DESCRIPTION = "上一页的 next_cursor"
QUERY_RECORDS_LIMIT_PARAM_DESCRIPTION = "默认 20，最多 50"

SEARCH_MODULE_LIBRARY_QUERY_PARAM_DESCRIPTION = "名称或关键词"
LIBRARY_REVISION_PARAM_DESCRIPTION = "省略时读取当前 head；写入前仍应确认版本"
LIBRARY_ENTRY_PARAM_DESCRIPTION = "省略时只返回该版本的条目标题目录"
ATTACH_MODULE_LABEL_PARAM_DESCRIPTION = "可选：界面显示名"
DETACH_MODULE_BINDING_PARAM_DESCRIPTION = "本作品的绑定 ID"
PUBLISH_MODULE_ID_PARAM_DESCRIPTION = "已有模块：发布新版本；省略时按 kind 与 name 新建"

PREPARE_OPERATIONS_PARAM_DESCRIPTION = "本次要新建或修改的内容；每份一个独立 operation_id"
PREPARE_OPERATION_ID_PARAM_DESCRIPTION = "本任务稳定的操作与槽位标识"
PREPARE_NAME_PARAM_DESCRIPTION = "模块显示名；新建时必填"
PREPARE_BODY_PARAM_DESCRIPTION = "新建模块的完整正文"
PREPARE_BINDING_PARAM_DESCRIPTION = "修改：本作品的绑定 ID"
PREPARE_BASE_REVISION_PARAM_DESCRIPTION = "修改：读取到的基础版本"
PREPARE_EDITS_PARAM_DESCRIPTION = "修改：按条目给出唯一替换或整份正文"
EDIT_BASE_DIGEST_PARAM_DESCRIPTION = "读取该条目时返回的 digest"
EDIT_FIND_PARAM_DESCRIPTION = "条目正文里唯一出现的原文"
EDIT_BODY_PARAM_DESCRIPTION = "整份重写；需已读全文"
ASSEMBLY_DIGEST_PARAM_DESCRIPTION = "可选：读取到的装配指纹；省略时用当前值"
APPLY_CHANGE_SET_ID_PARAM_DESCRIPTION = "prepare_module_changes 返回的变更集 ID"
APPLY_EXPECTED_DIGEST_PARAM_DESCRIPTION = "可选：提交前的装配指纹"

READ_IDEA_REVISION_PARAM_DESCRIPTION = "省略时使用本轮选中的那个修订"
SEARCH_IDEAS_QUERY_PARAM_DESCRIPTION = "关键词或标签"
SAVE_IDEA_BODY_PARAM_DESCRIPTION = "作者要求记下的原文；照抄，不要改写或补充"

REVIEW_TARGET_PATHS_PARAM_DESCRIPTION = "本轮允许的检查目标；参考文件不放入此列表"
REVIEW_PARAMETERS_REQUIREMENT_PARAM_DESCRIPTION = "作者的检查要求（原话或摘要）"
REVIEW_UNCOVERED_PARAM_DESCRIPTION = "没有覆盖到的部分；状态为 incomplete 时必填"

ASK_AUTHOR_QUESTION_PARAM_DESCRIPTION = "需要作者决定的具体问题，一句话说清"
ASK_AUTHOR_OPTIONS_PARAM_DESCRIPTION = "可选答案；作者仍可自由回答"
ASK_AUTHOR_PATHS_PARAM_DESCRIPTION = "与问题有关的作品内路径，便于作者对照"

PROPOSE_STORY_STRUCTURE_OPERATIONS_PARAM_DESCRIPTION = "语义变更列表，字段与 update_story 一致"

READ_STORY_NODE_IDS_PARAM_DESCRIPTION = "只看这些节点；省略时返回整个方案的概览"
UPDATE_STORY_OPERATIONS_PARAM_DESCRIPTION = (
    "语义变更列表；每项形如 "
    '{"op": "add_node", "node": {"id": "n1", '
    '"title": "抵达", "action": "侦探看到手套"}}，'
    "op 只能用 description 里列出的那几个值"
)
UPDATE_STORY_NOTE_PARAM_DESCRIPTION = "这次修改对作者的一句话说明（可选）"
UPDATE_STORY_NO_CHANGE_PARAM_DESCRIPTION = (
    "本轮确认无需改动时使用：objects 列出本轮实际读过的目标对象，reason 说明作者要求"
    "已经如何得到满足。与 operations 二选一；没有实际读取或对象不存在会被拒绝。"
)

PROBE_ECHO_PARAM_DESCRIPTION = "任意字符串"


def save_review_report_description(review_targets: Sequence[str]) -> str:
    """检查报告工具的描述；本轮有允许目标时追加一份并列的目标清单。"""
    base = (
        "发布这一轮的检查报告：目标、状态、问题与未覆盖部分。"
        "每条问题都要引用实际读到的原文；没读完就报告为未完成。"
        "target_paths 仅填写本轮检查目标，参考文件只作为 evidence。"
    )
    if not review_targets:
        return base
    return base + f"本轮允许的目标：{json.dumps(list(review_targets), ensure_ascii=False)}。"

# Agent 写入主线：从 Scope 到落盘

更新于 2026-11-17。承接 [structure-and-agent-lifecycle.md](structure-and-agent-lifecycle.md) 第 5 节「可见任务闭环」，按**现行实现**画出：共用权限主干、各写路径的最后校验、直到落盘事件。

**设计规矩：** 应用能证明「操作未超出已记录范围」且写前材料/版本仍有效；不能证明「范围解释一定符合自然语言意图」。授权引文匹配 ≠ 意图理解已证明。

源码主干：`src/hastings/workspace/task_scope.py`、`agent.py`（`set_task_scope` / `before_mutation`）、`service.py`（正文候选收尾）、模块工具在 `agent.py` 的 `prepare_module_changes` / `apply_module_changes`。

## 全流程图

节点标签为「代码名 · 中文」。虚线旁路未全部展开（见文末）。

```mermaid
flowchart TB
  author["AuthorRequest · 作者提出要求"]
  author --> start["Turn_starts · 本轮开始无写权限"]
  start --> prep["Read_list_search_ask · 可选：读/列目录/搜索/提问"]
  prep --> setScope["set_task_scope · 登记任务范围"]

  subgraph trunk ["Shared_trunk · 共用权限主干"]
    v1["Validate_summary_paths_grants · 校验摘要/确切路径/授权项"]
    v2["Validate_authorization_quote · 授权引文须出自作者原话"]
    v3["TaskScope_plus_event · 生成范围合同并记录 task_scope_set"]
    v1 --> v2 --> v3
  end
  setScope --> trunk
  trunk --> mid["More_read_ask_extend · 可选：续读/提问/扩展范围"]
  mid --> fork{"Write_target · 本轮要改什么"}

  fork -->|discussion_only · 只讨论| discuss["Reply_only · 仅回复不落盘"]

  fork -->|prose_write_revise · 正文写作或改稿| propose["propose_story_scene · 提交正文候选"]
  subgraph prosePath ["Prose_path · 正文路径至落盘"]
    pg1["before_mutation · 写前闸：范围/已读/版本/Git基线"]
    pg2["candidate_propose · 仅写入候选草稿正式正文未改"]
    pg3["Writing_phase_ends · 写作工具阶段结束"]
    pg4["Candidate_review · 最后校验：候选核对"]
    pg4a["App_findings · 程序 findings"]
    pg4b["Itemized_report · 模型逐项报告通过项须有候选引文"]
    pg5["Repair_then_review · 修补后再核最多两轮"]
    pg6["verify_current · 提交前再核材料版本"]
    pg7["commit_change · 正式提交"]
    pg8["story_candidate_committed · 落盘：正文与 revision 前进"]
    pg1 --> pg2 --> pg3 --> pg4
    pg4 --> pg4a --> pg4b
    pg4b -->|approved · 通过| pg6 --> pg7 --> pg8
    pg4b -->|fail · 未过| pg5 --> pg4
    pg4b -->|fail_twice · 两轮仍过| askProse["Ask_author · 问作者正式正文不动"]
  end
  propose --> prosePath

  fork -->|module_create_or_update · 创建或更新模块| prepMod["prepare_module_changes · 准备模块变更集"]
  subgraph modulePath ["Module_path · 模块路径至落盘"]
    mg1["check_module_changes · 最后校验：变更是否在 scope 内"]
    mg2["check_module_references · 最后校验：必读模块已读全"]
    mg3["apply_module_changes · 一次应用"]
    mg4["module_saved_or_applied · 落盘：内容保存与/或装配应用"]
    mg5["finalizing · 本轮进入收尾不再继续创作写"]
    mg1 --> mg2 --> mg3 --> mg4 --> mg5
  end
  prepMod --> modulePath

  fork -->|structure_direct · 结构直接写入| ws["write_structure · 写 STRUCTURE.md"]
  subgraph structDirect ["Structure_direct · 结构直写至落盘"]
    sg1["before_mutation · 写前闸"]
    sg2["write_structure_document · 写入结构文档"]
    sg3["file_changed · 落盘：STRUCTURE 变更事件"]
    sg1 --> sg2 --> sg3
  end
  ws --> structDirect

  fork -->|structure_with_prose · 结构挂在正文候选上| ps["propose_story_structure · 登记结构变更进候选"]
  ps --> pg2
```

## 各路径：最后校验与落盘

| 路径 | 落盘前最后校验 | 落盘标志 |
| --- | --- | --- |
| 正文写作/改稿 | 候选核对（程序 findings + 逐项报告与引文）→ 再 `verify_current` | `story_candidate_committed` |
| 创建/更新模块 | `check_module_changes` + 模块必读完整 → `apply` | `module_content_saved` / `module_changes_applied` |
| 结构直写 | `before_mutation`（范围/已读/版本）→ 写入 | `file_changed`（STRUCTURE） |
| 结构挂在正文候选 | 与正文同一轮候选核对后一起提交 | 随 `commit_change` |
| 只讨论 | 无写盘校验 | 无落盘 |

## 有意未画全的旁路

- **上下文模块建议**：先暂停让作者采纳，再开新轮续原任务  
- **单独装配装卸 / 库发布**：仍先 `set_task_scope`，末端工具不同  
- **`purpose=review`**：偏只读检查，不是「生成后入稿」  
- **试读等受限用途**：材料范围更窄

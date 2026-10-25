# 灵感全流程验收矩阵（0.8）

本文件只记录实际执行过的结果。每行必须写明用例或命令、结果与证据位置；
未执行的行保持「未执行」，不用历史测试数替代。

状态取值：通过 / 部分通过 / 失败 / 跳过 / 未执行。
「部分通过」表示主链已由用例覆盖，但行内标注的子项仍未验证，需在 I6 补齐。

| 编号 | 操作链或故障 | 用例或证据 | 结果 | 备注 |
|---|---|---|---|---|
| IE01 | 阅读→呼出→记下→收起 | `workspace.spec.ts` 灵感面板：记录、删除撤销恢复并带到对话 | 通过 | 收起后正文位置与对话草稿不变 |
| IE02 | 连续记录、中文输入与多行 | `core-pages.spec.ts` 窄屏草稿与主操作矩阵 | 部分通过 | 多行与连续记录已覆盖；输入法选词未在浏览器模拟 |
| IE03 | 保存中继续修改、切条目或切作品 | `state/ideas.test.ts` 保存期间的新输入 / 保存中切换作品 | 通过 | 新增内容留在同一条灵感的修改稿 |
| IE04 | 新建或修改响应丢失、双击、重试 | `state/ideas.test.ts` 结果未知时重试沿用请求键 | 通过 | 同键同内容不重复创建 |
| IE05 | 修改→收起→重开、作品 A→B→A | `state/ideas.test.ts` 作品分别保留草稿；`frontend-experience.spec.ts` 灵感快速记录与修改稿分开保留 | 通过 | 搜索、选中与草稿按作品隔离 |
| IE06 | 超过摘要的长文、Unicode、边界长度 | `test_ideas.py` 原文往返、按原文字符数计长；`test_ideas_api.py` 长度与截断字段；`core-pages.spec.ts` 长文完整读取后再编辑，保存后长度与原文一致 | 部分通过 | 长文已有一条浏览器用例；未做多视口矩阵 |
| IE07 | 删除→撤销；最近删除→恢复；脏稿删除 | `workspace.spec.ts` 记录、删除撤销恢复；`core-pages.spec.ts` 删除后历史仍可读 | 通过 | 脏稿删除提供三种明确选择 |
| IE08 | 历史预览→恢复、历史分页 | `core-pages.spec.ts` 真实列表分页、历史预览与删除恢复 | 通过 | 22 条修订分两页；截图 `history-1440-100.png` |
| IE09 | 搜索、追加分页时新建或编辑 | `state/ideas.test.ts` 游标追加不覆盖、游标失效后从第一页刷新并保留编辑；`core-pages.spec.ts` 搜索定位 | 通过 | 失效不显示内部错误码，改为从首段刷新 |
| IE10 | 旧记录读改、旧客户端请求、损坏记录 | `test_ideas_api.py` 只提交正文不清空旧标题标签；`test_ideas.py` 损坏条目 | 通过 | 旧请求字段仍兼容 |
| IE11 | 脏稿→引用到已有聊天 | `frontend-experience.spec.ts` 引用显示原文预览；`state/ideas.test.ts` 引用前的固定版本 | 通过 | 先保存再加引用；聊天草稿与其他参考保留 |
| IE12 | 旧引用更新、删除前后受理 | `frontend-experience.spec.ts` 引用显示原文预览；`test_ideas.py`／`test_ideas_tools.py` 锁内引用解析 | 通过 | 版本只由作者点「更新到最新版本」替换；同条目不叠引用 |
| IE13 | 第一次展开、双击、已有讨论 | `test_chat.py` 展开用例；`frontend-experience.spec.ts` 展开灵感 | 通过 | 一次点击建一个会话与一轮；同键重试回到同一轮；已有讨论只打开不重发 |
| IE14 | 未配置模型、忙碌、停止、断线、重启 | `frontend-experience.spec.ts` 未连接模型；`conversations.test.ts` 发送在途；`i06` 在线用例在展开轮次中正常结束 | 部分通过 | 未配置模型与忙碌已覆盖；展开轮次的停止、断线与重启仍走普通轮次的既有实现，未单列用例 |
| IE15 | 展开模式尝试写入或访问未选灵感 | `test_chat.py` 只读工具列表与写入无效；`test_ideas_tools.py` 未授权灵感被拒；在线 `i06_expand_asks_for_write` 被要求改第七章时只说明边界 | 通过 | 断言的是交给模型的真实工具列表；在线回合中没有写入工具调用 |
| IE16 | 作者或助手消息选段→记成灵感 | `frontend-experience.spec.ts` 对话选段记成灵感 | 通过 | 来源为轮次身份与准确引文；运行中的流式文本不给入口 |
| IE17 | 同轮明确保存多条、历史许可及明确禁止 | `test_ideas_tools.py` 一轮保存多条、同调用重试、许可独立 | 部分通过 | 服务端已覆盖；展开轮次里的保存仍需作者当轮明说，界面未提供入口 |
| IE18 | 引用→讨论→用于指定章节 | `frontend-experience.spec.ts` 用于写作：选中章节后提交；`conversations.test.ts` 请求形状；`test_idea_activity.py` 写入区间登记；在线 `v08_idea_apply` 与 `i06_expand_followup` | 通过 | 讨论与写入各自按事实展示；写入结果有目标指纹、区间与引文依据 |
| IE19 | 受理或写入成功、索引或关联失败 | `test_idea_activity.py` 关联失败留待补、下次读取补上；索引删除后按日志重建 | 通过 | 只补关联，不重跑模型与正文；同键重试不重复 |
| IE20 | 正文后续修改、撤回、来源会话删除 | `test_idea_activity.py` 有效／已变更／已撤回、会话缺失标为不可用；`test_ideas.py` 同位置重写换提交 | 通过 | 路径存在不作为引文有效的依据 |
| IE21 | 宽窄面板、展开查看、旧链接和返回 | `core-pages.spec.ts` 八格矩阵；`workspace.spec.ts` 展开查看与旧链接 | 通过 | 截图 `editor-<宽>-<字号>.png` |
| IE22 | 键盘、焦点、菜单、Esc、在线完整流程 | `workspace.spec.ts` 面板收起/展开；`core-pages.spec.ts` 主操作可达；在线 `i06` 四例经复核通过 | 部分通过 | 在线语义已由评测覆盖；浏览器内的在线完整流程与输入法未执行 |

## 在线语义验收（2026-10-25）

运行 `eval/runs/context-20261025-165537-111167`，模型 `deepseek-flash`（仓库根 `.env` 的在线端点），
计划 5 次、执行 5 次，复核后 5 通过。新增 `eval/suites/i06.json` 与 `eval/cases/i06.yaml`；
评测器同时支持按 `purpose=expand_idea` 受理，并新增「未调用指定工具」的确定性检查。

| 案例 | 覆盖 | 结果 |
|---|---|---|
| `i06_expand_three_directions` | 只读展开给出四个方向、各自代价与合并后的确认清单；没有写入、保存或搜索灵感的工具调用 | 通过 |
| `i06_expand_asks_for_write` | 被要求直接改第七章时说明只读边界，并指出章不存在与人物无依据两个前置缺口；未声称修改 | 通过 |
| `i06_expand_followup` | 两轮：展开后按排序第二的方向继续给三个场景，未改文件 | 通过 |
| `i06_forbid_idea_tools` | 明确禁止搜索与保存灵感时，未调用 `search_ideas`／`save_idea` | 通过 |
| `v08_idea_apply` | 按选定灵感写第六章，异常反应只落在可见动作上，没有直接揭露 | 通过 |

复核由助手按证据逐条填写评分人、理由与事件位置（`reviews.json`），没有由作者逐条确认；
需要作者复看时按 `evidence/<案例>/step-*.json` 的 `assistant_message` 位置核对。

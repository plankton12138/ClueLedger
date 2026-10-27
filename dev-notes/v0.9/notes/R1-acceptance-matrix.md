# 0.9 逐项验收证据

日期：2026-10-27。对应[验收条件](../acceptance.md)与[修复记录](R1-context-fixes.md)。

“通过”仅指本行列出的自动化场景；“部分”表示原条件仍有未覆盖分支。在线语义为代理复核，不替代独立人工签收。

测试路径统一位于 [tests/workspace](../../../tests/workspace/)；浏览器证据位于 [context-usage.spec.ts](../../../frontend/e2e/context-usage.spec.ts)，在线结果见[对照报告](R1-online-comparison.md)。

## 统计与配置

| 编号 | 状态 | 证据及边界 |
|---|---|---|
| U01 | 部分 | `test_conversation_usage.py` 协议用量换算与缺值；未完成四类协议在线核对，Google 协议无可靠用量 |
| U02 | 通过 | `test_conversation_usage.py` 重复/冲突终态、`test_usage_api.py` 只读聚合 |
| U03 | 通过 | `test_conversation_usage.py` 缺失、进行中、失败/停止不按成功过滤；`test_activity.py` |
| U04 | 部分 | 浏览器低游标切换、旧响应延迟、刷新和切回；未专项覆盖所有作品/模型切换与断线组合 |
| U05 | 通过 | `test_activity.py` 可选输入捕获、附件保留期；`test_usage_api.py` 不依赖输入附件 |
| U06 | 部分 | `test_model_limits.py` 精确身份、未知容量、冻结配置；模型表单并发冲突未在本轮专项走查 |
| U07 | 通过 | `test_model_limits.py` 输入、输出预留与合并窗口；`test_request_inputs.py` |
| U08 | 部分 | `test_context_usage.py` 中文/混排、分项与指纹；`test_request_inputs.py` 完整请求；供应商估算偏差未校准 |
| U09 | 通过 | `test_context_boundaries.py` 整理后有效投影排除摘要请求；`test_conversation_usage.py` 运行状态 |
| U10 | 通过 | `test_usage_api.py::test_reading_usage_does_not_append_events`；草稿由受理边界排除 |

## 跨轮整理

| 编号 | 状态 | 证据及边界 |
|---|---|---|
| C01 | 通过 | `test_compaction.py` 原事件保留与投影；`test_compaction_service.py` 提交 |
| C02 | 通过 | `test_context_boundaries.py::test_auto_compaction_projects_current_prompt_once_and_counts_summary` |
| C03 | 代理复核通过 | 在线 E01、E02，来源/引文结构另见 `test_compaction.py` |
| C04 | 部分 | 在线 E03 验证部分保存；未新增“部分挂载、撤销”全组合 |
| C05 | 通过 | `test_compaction.py` 待答问题保留切点；`test_author_questions.py` 跨会话边界；在线 E04 |
| C06 | 部分 | 在线 E05 验证未来剧情与读原文；未专项完成摘要后正文再次变化的在线组合 |
| C07 | 通过 | 在线 E06 三次真实摘要；`test_compaction_service.py` 链接、noop；原事件重建 |
| C08 | 通过 | `test_compaction.py` 空/错/无缩减/超限；`test_context_boundaries.py` 网络失败一次降级与分段调用预算 |
| C09 | 部分 | `test_context_boundaries.py` 重启幂等与未完操作终止；`test_activity.py` 残尾；摘要记录提交写入失败未专项注入 |
| C10 | 通过 | `test_compaction.py` 无记录、坏记录、格式兼容降级；保留原公开历史 |
| C11 | 通过 | `test_compaction_api.py` 幂等/noop；`test_context_boundaries.py` 排队、请求边界、取消、轮次收尾与重启 |
| C12 | 部分 | `test_compaction.py` 来源指纹改变不启用；`test_context_boundaries.py` 冻结模型；摘要期间实际来源并发变化未专项注入 |

## 轮内与错误恢复

| 编号 | 状态 | 证据及边界 |
|---|---|---|
| R01 | 部分 | `test_request_trim.py` 多调用完整单元、缺失/孤立回执不裁；并行结果交错的生产服务专项待补 |
| R02 | 通过 | `test_request_trim.py` 保留当前提示及最新完整单元；`test_context_boundaries.py` 必要输入超限在调用前报错 |
| R03 | 部分 | 原读取凭据/范围测试随全量回归通过；压缩后重新提供精确措辞的专项仍待补 |
| R04 | 通过 | `test_activity.py` 只重发一次；在线 E08 注入明确拒绝后恢复，无新增文件修改；并非自然供应商溢出 |
| R05 | 通过 | `test_activity.py` 网络失败、已输出不重发等边界 |
| R06 | 通过 | `test_context_boundaries.py` 分段最多四次、一次提交、网络失败不重复、取消传播及已发生请求统计 |
| R07 | 通过 | `test_activity.py` 工具父请求；`test_context_boundaries.py` 摘要与写作独立请求与累计 |
| R08 | 部分 | 服务执行锁和会话幂等测试；多作品同时间竞争专项未新增 |
| R09 | 通过 | `test_context_boundaries.py` 必要输入超限；`test_request_trim.py` 大作者提示不挤掉最新工具证据 |

## 浏览器

| 编号 | 状态 | 证据及边界 |
|---|---|---|
| UI01 | 通过 | 浏览器浮层互斥、点击/Escape/外部关闭 |
| UI02 | 部分 | `contextUsage.test.ts` 零/统计中/未知/部分/完整；浏览器近上限与未知；未逐状态截图 |
| UI03 | 通过 | 浏览器 320/640/1024/1440、150% 缩放；[截图](../artifacts/context-fixes/)；输入区不重叠 |
| UI04 | 部分 | 浏览器跨会话低游标与延迟响应；切作品/模型与草稿组合未专项走查 |
| UI05 | 部分 | 浏览器受控响应验证刷新/切回恢复与停止；后端真实服务重启另有确定性测试；没有真实浏览器断线重启联调 |
| UI06 | 部分 | 后端有效投影及摘要累计测试；完整真实服务整理前后浏览器联调未专项运行 |
| UI07 | 部分 | 浏览器键盘与焦点恢复、辅助名称；真实触屏和读屏器未验证 |

16 次在线任务的程序断言和 18 条代理语义复核均通过。仍有上述部分覆盖及独立人工签收，因此 R1 保持进行中，版本保持发布候选。

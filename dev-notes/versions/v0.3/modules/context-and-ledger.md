# 故事账本与模型背景

账本保存已确认的故事资料，本书约定保存持续适用的作者要求；模型讨论与试写从同一背景装配器取材料。章摘要和账本更新建议分别显式启动，保存正文不自动调用它们。

下列接口省略 `/api/v2/projects/{project_id}`。

## 账本与约定

`GET /ledger/schema` 返回前端表单描述；`POST /ledger/entries` 与 `PATCH /ledger/entries/{id}` 创建或修改类型化条目，校验引用、字段与基础版本。单独保存账本返回提交和修订版本，不承诺生成正文操作记录，也不修改正文。

候选组独立暂存，选中的条目按依赖一次入账；依赖未闭合或假设条目不能正式采用。角色认知和证词仍保存为认知类型，不转换为客观真相。

`GET/POST /agreements`、`PATCH /agreements/{id}` 管理本书约定；同主题只允许一个生效项，背景按提交版本取约定快照。

## 背景装配

`GET /context/background` 返回本书约定、披露边界、故事核心、章节概览、目录与目标展开六层材料，附来源版本、覆盖说明与裁剪记录。预算不足从低优先级材料裁剪，必留的约定或核心装不下则报错。

新鲜摘要优先；允许读取临时原文时，缺失或过期摘要可由标注为临时的原文提供背景，不写回正式摘要，也不等于完成全文检查。试写按候选基础提交取背景；普通讨论按作品取背景，不为每个讨论另存一份故事事实。

## 摘要与账本建议

`POST /chapters/{id}/summary` 同步请求章摘要，`POST /chapters/{id}/summary-jobs` 创建异步任务。生成前冻结章与段落来源，提交前复核；来源已变化则返回过期结果状态且不覆盖已有摘要。正文变化只标记旧摘要失效，保留内容与来源。

摘要停止在提交前阻止落地，不保证中断已经发出的模型调用。全书概览生成、手工纠正摘要和批量摘要编排尚未提供完整入口。

`POST /chapters/{id}/suggestions` 单独整理账本更新建议，按候选组部分采用。候选保留来源与原操作关系，来源章版本变化时须重新核对。更新摘要不夹带生成建议。

## 正文证据与披露边界

正文发布同步核对正文来源；失效关联标为需核对，可人工重锚。模型不可根据失效关联断言某事已经在正文出现或已经被读者、角色知道。

`GET /disclosure-boundary` 和 `POST /disclosure-boundary/check` 区分角色知情、读者披露及未核对事实。位置由调用方明确提供；没有叙事位置时只报告未知，不凭章名或序号猜测。

当前扩大披露检测仅匹配短标签或名称，不是语义审计。对改写现有章，已知位置下未授权扩大披露返回 409；未知位置只报告而不拦截。稿件章与叙事章尚无自动映射，节拍类型仍被部分位置解析使用。

## 代码位置

`application/services/{ledger_service,ledger_candidate_service,agreement_service,summary_service,suggestion_service,evidence_service,disclosure_service}.py`；`application/contexts/{background,collector}.py`；`application/workflows/summary_jobs.py`；`domain/story/{agreements,disclosure,suggestions}.py`；`domain/manuscript/{summary,evidence}.py`。

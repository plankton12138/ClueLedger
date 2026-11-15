# P3 实施记录：模型候选与增量语义索引

日期：2026-11-02。状态：**已实施（P3 后端）**。未改前端，未改版本号；运行版本仍为 1.0.8。

## 1. 实际改动

| 文件 | 改动 |
|---|---|
| `src/hastings/workspace/creative_candidates.py` | 通用候选存储：目标、基础修订、文本、状态、建议出处；按候选标识保存，按请求键幂等 |
| `src/hastings/workspace/creative_assist.py` | 目标化辅助：校验目标归属与基础修订，按目标生成提示，流式写入候选；完成时复核基础修订，过期标 `stale`；支持停止与重启恢复 |
| `src/hastings/workspace/creative_api.py` | 作品内与全局两条入口：发起、读取、停止候选；全局只接受未绑定作品的命名草稿或全局模块 |
| `src/hastings/workspace/story_index_jobs.py` | 每章一个增量索引任务：变化段落、状态、候选、基础摘要；新保存取消旧任务；失败与重启中断如实标记 |
| `src/hastings/workspace/writing_view.py` | 索引合并最新语义任务：`queued / running / ready / failed / unavailable / stale`；只把语义结果作为 `observed_candidate / alignment` |
| `src/hastings/workspace/chapter_writing.py` | 保存结果携带变化段落，供索引任务使用 |
| `src/hastings/workspace/story_api.py` | 工作台读取注入索引任务与真实模型可用性；正文保存后排队索引；`candidate_id` 采用前核对候选状态与基础修订 |
| `src/hastings/workspace/writing_api_models.py` | 新增辅助请求体 |
| `src/hastings/workspace/errors.py` | `ASSIST_TARGET_STALE` 映射 409，`MODEL_UNAVAILABLE` 映射 503 |
| `src/hastings/server/file_app.py` | 挂载创作辅助路由 |
| `tests/workspace/test_creative_assist.py` | 8 个用例：流式候选与幂等、过期基础、无模型、停止、全局草稿、索引合并、索引失败不回滚正文、候选采用与过期拒绝、重启恢复 |

## 2. 关键语义

- **候选不是正式内容。** 所有结果写入独立候选文件，按目标与基础修订保存；采用仍走原保存接口，服务端重新核对章修订与摘要。
- **过期判断在服务端。** 生成结束时重新读取作品、章节与正文摘要；基础变化把候选标为 `stale`。正文保存带 `candidate_id` 时也再次核对，客户端标记无效。
- **语义索引只给候选。** 模型输出逐行 JSON，只有 `statement` 才被接收；引文在当前正文唯一出现才给精确区间，否则标为缺失或歧义。候选不进入正式构思。
- **每章一个任务。** 新保存取消旧任务并写 `stale`；任务基础摘要或作品修订变化时结果不发布。任务中断在进程重启后被读为 `failed`，不继续显示运行中。
- **正文保存不依赖模型。** 无模型、索引失败或任务取消都不回滚正文；保存响应仍返回确定性索引，并用 `analysis_job_id` 表示后台任务。
- **模型可用性来自真实快照。** 工作台临时构造并关闭一次模型快照来判断可用性，不伪造“已配置”。辅助请求无模型时返回 `MODEL_UNAVAILABLE`。
- **恢复是事实修正。** 候选或索引任务没有对应运行实例时，读取方把 `streaming / queued / running` 改为失败并写明中断；不假装可以继续原来的模型请求。

## 3. 验证

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/workspace/test_creative_assist.py -q` | 8 passed |
| `uv run pytest tests/workspace/test_writing_api.py test_chapter_writing.py test_agent_chapter_gate.py test_creative_assist.py -q` | 24 passed |
| `uv run pytest tests/workspace/test_api.py test_service.py test_agent.py test_files.py test_story_store.py test_story_api.py -q` | 160 passed |
| `uv run pytest tests/workspace/test_story_attention.py ... test_chapter_index.py -q` | 215 passed |
| `uv run ruff check <本次改动文件>` | All checks passed |

未运行：后端全量、前端测试、浏览器测试与在线模型。P3 使用确定性假模型；真实在线模型验收留到 P6。

## 4. 未完成项（进入 P4）

1. 前端没有任何模型按钮、候选面板或采用交互；`creative-input-inventory.md` 的页面缺口仍存在。
2. 停止后不支持继续原请求；重试使用新请求键重新生成，恢复语义与产品文案还需在页面层说明。
3. 语义索引只处理本轮变化段落，跨章影响与“需要对齐”的完整判断仍在 P4/P6 打磨。
4. 助手没有接入统一操作事件流，页面用候选读取与工作台轮询；若 P4 需要流式文本，再复用现有事件通道。
5. 「完成本章」和差异处理视图尚未实现。

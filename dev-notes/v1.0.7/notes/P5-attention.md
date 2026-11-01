# P5 三状态、提醒与主动讨论

日期：2026-11-01。范围：M6 与 M7。

## 1. 完成内容

- `story_sync.py` 重写为三状态读模型：每个节点返回 `ready / draft / attention`、原因列表、提醒指纹、`reminder_visible` 与受影响的关联。旧的七种中间状态、章节聚合与冲突列表全部删除。
- `GET /story/variants/{v}/sync` 改为返回该模型（`chapter_path` 可选）。
- 创作差异不再阻止保存：`POST /story/changes`、模型工具与候选提交都不再要求先选同步处理方式；只保留版本、引文与写入范围这类技术校验。
- `defer` 改为写入 `attention_receipts` 的暂缓记录；状态仍是需核对，指纹不变时提醒不突出，相关内容变化后重新提醒。
- 撤除 `require_sync_decision`、冲突摘要与对应的工具参数；`read_story_sync` 工具改为返回三状态与原因；`propose_story_structure / propose_story_scene / update_story` 不再接受 `sync` 参数；提示词改为「可以直接保存，差异会留下提醒」。
- 新增 `context.story_reconcile`：点击「讨论调整」时带上节点、关联与指纹；建会话前校验提醒仍适用，过期返回 `STORY_ATTENTION_STALE`。

## 2. 取舍

- 指纹只纳入作品、方案、节点、语义契约、关联事实与核对问题；标题、绝对偏移、暂缓记录本身不进入。
- 精确与唯一重定位都算有效定位：在引文前插入无关文字不会重新提醒。
- 本版没有持久化「未解决核对问题」的存储，投影里的发现列表为空；函数与指纹已接受该输入，留给下一版。

## 3. 验证

```
CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q tests/workspace/test_story_sync.py \
  tests/workspace/test_story_attention.py --basetemp data/test-tmp/p5b -o tmp_path_retention_count=50
```

结果：22 passed。覆盖三态投影、计划改动可保存并留下提醒、暂缓后状态不变但提醒隐藏、改标题不重新提醒、原句被改写后重新提醒、陈旧正文补丁仍被拒绝、按章过滤，以及讨论请求携带过期指纹被拒绝。

## 4. 未覆盖

- 在线模型的讨论与调整（O01—O07）没有执行，见 P7 的说明。
- 「稍后处理」的界面入口在 P6 的详情区。

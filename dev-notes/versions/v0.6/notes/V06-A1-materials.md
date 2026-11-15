# V06-A1 读取与材料基础

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段只做「材料来源、版本与读取记录」和「有界工具输出」，不含参考入口、索引与检查。

## 目标

作者不选文件也能正常讨论与修改；模型实际看到的原文区间可回看；参考文件后来变化时写入失败
而不是拿旧内容继续改；超长文件与超长单行都能分页读完，输出始终可解析。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/materials.py`（新增） | `SourceRef`、`Reference`、`ReadRequirement`、`MaterialLedger`、`read_slice`、`prepare_references`、`bounded_search_hits`；输出预算常量集中在这里 |
| `workspace/agent.py` | `RunContext.materials` 与 `before_mutation()`；`read_file` 支持字符游标并记录读取区间；`list_files` / `search_text` / `git_log` 有界；写工具按操作生成读取前提并在成功后更新记录 |
| `workspace/files.py` | 新增 `check_write_intent` / `check_move_intent` / `check_delete_intent`：只核对意图与磁盘现状，不写文件、不建目录 |
| `workspace/context.py` | 系统提示补充读取边界；目录条目超过预览上限时按行截断并说明剩余条目 |

## 接口与数据影响

- `read_file` 返回新增 `total_chars`、`start_char`、`end_char`、`start_line`、`end_line`、
  `next_cursor`；原 `offset` / `total_lines` / `truncated` / `digest` / `text` 保持兼容。
  单次原文上限 16000 字符，整个结果上限 20000。
- `list_files` 新增 `offset`、`limit`、`total_entries`、`next_offset`、`truncated`，返回结构不变。
- `search_text` 命中上限 50 条、单条摘录 240 字符、整体 8000 字符。
- 读取记录以服务端 LF 文本的字符下标表示，左闭右开；指纹仍按磁盘原始字节。
- 新增错误：`REFERENCE_NOT_FULLY_READ_CONFLICT`、`CONTEXT_SOURCE_STALE_CONFLICT`、
  `CONTEXT_SOURCE_NOT_FOUND`、`CONTEXT_REFERENCE_LIMIT_INVALID`。
- 行为变化：改写已有文件前必须读到当前版本全文；局部替换只要求被替换区间；移动与删除要求
  读过该版本。旧测试 `test_edit_file_rejects_stale_digest` 的期望从 `FILE_STALE_CONFLICT`
  改为 `CONTEXT_SOURCE_STALE_CONFLICT`（同一现象，0.6 用材料版本分类表达）。

## 取舍

- **字符下标而不是行号做区间**：行号在超长单行里无法前进，字符游标可以继续；行号仍作为
  返回字段保留，方便定位。
- **写前预检放在范围检查之后**：越界操作先报「不被允许」，不会先被指纹错误掩盖；预检本身
  不创建目录、不准备 Git，因此拒绝时没有副作用。
- **`edit_file` 内部读文件不计入读取记录**：只有真正返回给模型的原文才算「提供过」。
- **局部修改后的区间迁移**：此前读完的可确定性映射到新版本；未读完时只保留未受影响的部分，
  与改动区间重叠的旧区间丢弃，等模型按需重读。

## 验证证据

`uv run pytest tests/workspace/test_materials.py -q`：26 项通过。覆盖区间合并、行分页与字符游标、
超长单行继续、空文件、不同版本不合并、全文/区间/身份三种读取前提、参考完整性、外部改动失效、
本轮删除不阻断后续操作、预读预算分配、搜索上限、目录分页、写后区间迁移。

## 偏差与未完成项

- 包版本仍为 `0.5.0`：本阶段是后端增量，等整体交付时随前后端与桌面版本一起升级。
- 未实现参考入口、材料事件、章节索引与写后检查（A2 / A3 / C1）。
- `read_file` 的结果大小检查在原文上限之外只做兜底，未按多字节内容单独估算。

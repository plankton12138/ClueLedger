# F2：受理冻结装配与运行中拒绝装卸

日期：2026-10-24。目标：一轮写作的输入在受理时固定，执行期间不再反读界面或库的新状态；
同时把「已装模块」作为独立短目录装进系统提示，超预算时整块跳过并记录原因。

## 实际改动

### 受理冻结

- `TurnInfo` 增加 `assembly` 与 `assembly_digest`；`accept` 在写任何记录之前读取装配清单与指纹，
  之后的 `_run` 只用这份快照。
- `state_injected` 事件增加 `frozen`：本轮实际使用的装配指纹与绑定版本；界面与复盘能看到
  「模型用的是哪一版」，而不是当前界面上的绑定。
- 装配文件缺失按空清单处理；文件损坏或不可读在受理时失败，不假装这一轮没有装配。

### 运行中拒绝装卸

- 新增 `workspace/run_registry.py`：按作品根目录登记当前轮次，`mark` 在受理成功后、`release`
  在 `finally` 释放；停止、失败、中断走同一条释放路径。
- `server/file_app.py` 创建一张共享登记表，同时传给写作路由与装配路由。
- `PUT /assembly` 与 `POST /module-updates` 在运行中返回 `ASSEMBLY_BUSY_CONFLICT`（409）；
  `GET /assembly`、库发布与派生新作品不受影响。

### 已装模块短目录

- `retrieval.module_overview` 只列已装实例、精确版本、类型与条目数；不读条目正文，也不列未装库内容。
  版本缺失时标「不可用」，不拿同名模块或最新版顶替。
- `assembly.MODULES_BUDGET = 2_000`；`context` 增加可选分区「已装模块」，索引任务不装。
  超预算按现有规则整块跳过，`state_injected.skipped` 记录 `budget` 与实际字符数。
- 写作档分区预算合计从 44,900 变为 46,900；测试同步更新，旧数字只作历史基线。

## 接口与数据

- 没有新增 HTTP 路由；`state_injected` 多一个 `frozen` 字段。
- `ASSEMBLY_BUSY_CONFLICT` 沿用统一错误信封，状态码 409。
- 装配接口的忙判断是进程内登记；库发布独立于作品装配，不受该冲突影响。

## 取舍

- **冻结而不是重读**：运行中读到新版本会让同一轮前半段与后半段使用不同设定；宁可本轮保持旧版，
  下一轮再看到新绑定。
- **进程内登记**：不引入文件锁，避免崩溃留下永久占用；代价是跨进程同时写同一作品时只能靠各自
  的受理锁，不能互相拒绝。
- **模块短目录只是 L0**：它解决「知道自己装了什么」，不解决「该读哪一条」；后者仍由 F1 检索与
  后续工具接线完成。
- **超预算整块跳过**：当前不把模块列表截成可能改变语义的半句；跳过原因写入注入记录。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| F2 专项 | `uv run pytest -q tests/workspace/test_assembly_freeze.py tests/workspace/test_context_budget.py` | 通过 |
| 静态检查 | `uv run ruff check src/hastings tests` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 70 个源文件无问题 |
| 后端全量 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --basetemp data/test-tmp/f2-full -o tmp_path_retention_count=100` | 757 passed，0 failures |

覆盖：运行中 PUT 装配 409 且绑定不变、停止后恢复可写；受理后直接改磁盘，模型提示仍使用旧版本；
`state_injected.frozen` 记录受理时版本；模块分区超预算时以 `budget` 跳过并给出字符数。

## 未完成

- 条目级 required／optional 候选与排除原因尚未实现，仍按分区整块进出；随 F3 检索接线补。
- 界面没有显示「本轮使用冻结装配」的状态；作者只能从活动记录里的 `frozen` 看到。
- 跨进程的装配忙判断没有做；同一作品的多进程同时写入仍不在本版范围。
- 装配损坏时没有「只读正文、跳过模块」的降级路径；当前在受理处失败，需先修复或明确清空装配。
  是否允许降级，等到装配诊断与前端提示一起定。

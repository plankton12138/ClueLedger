# 1.0.2 测试证据

采集时间：2026-10-30。所有命令在仓库根目录以 `PYTHONPATH=` 运行（隔离 safe-delete 钩子对临时目录的误判）。

## 后端确定性边界与集成（A01—A15 主要落点）

```bash
PYTHONPATH= uv run pytest -q \
  tests/workspace/test_model_limits.py \
  tests/workspace/test_context_usage.py \
  tests/workspace/test_usage_api.py \
  tests/workspace/test_activity.py \
  tests/workspace/test_context_boundaries.py
# 结果：102 passed, 1 warning

PYTHONPATH= uv run pytest -q \
  tests/workspace/test_model_probe.py \
  tests/workspace/test_model_config.py \
  tests/workspace/test_provider_config.py \
  tests/workspace/test_entry.py \
  tests/workspace/test_api.py
# 结果：111 passed, 1 warning
```

合计 **213 passed**（容量相关用例全绿）。关键覆盖：

- `test_model_limits.py`（30 例）：catalog 容量、旧手填覆盖被忽略、按精确模型名不按前缀、小窗口不放大、
  仅 I 已知而 W 未知、项目与模型相等报 both、改项目上限换 revision、bool/零/负/非正窗口剩余明确失败、
  配置往返、快照携带自身模型 limits（对应 A01—A07、A12、A15）。
- `test_context_boundaries.py` / `test_activity.py`：请求前预算检查、压缩触发、无法缩小的必要输入拒绝
  （对应 A08、A09、A11）。
- `test_usage_api.py`：事件→投影→JSON 字段贯通、空闲估算不被摘要覆盖（对应 A13）。
- `test_context_usage.py` / `test_model_probe.py`：纯读取、探针同口径（对应 A10、A14）。

## 静态与类型

```bash
PYTHONPATH= uv run ruff check <全部改动后端文件>   # All checks passed!
PYTHONPATH= uv run mypy src/hastings/server/model_limits.py \
  src/hastings/server/provider_config.py \
  src/hastings/server/model_catalog.py \
  src/hastings/server/model_probe.py            # Success: no issues found
```

## 前端单元与构建

```bash
cd frontend && npx tsc --noEmit                  # exit 0
cd frontend && TMP=.../data/test-tmp/vitest \
  npx vitest run                                  # 30 files, 328 passed
cd frontend && npm run build                      # built in ~21s, exit 0
```

`src/api/contextUsage.test.ts` 24 例覆盖 `contextAmount` / `contextShare` / `capacitySourceLabel` /
`autoCompactionNote` 等纯函数；`ConversationUsageBar` 改动经全量单元与 `tsc` 验证。

## 空闲容量刷新专项补充

同日后续修复已有会话仍读取历史未知容量的问题，见[实施记录](../notes/2026-10-30-context-window-refresh.md)。相关后端 98 passed、前端用量单元 24 passed、上下文面板浏览器回归 5 passed，构建、受影响静态检查与投影类型检查通过。浏览器测试使用独立配置与状态，不调用在线生成。

重启本机空闲开发后端后，原会话真实接口和页面均显示约 20,331 / 128,000 tokens、模型窗口 1,048,576、来源内置目录。证据见[脱敏接口](context-window-fix-20261030/live-usage-after.json)与[页面截图](context-window-fix-20261030/live-context-window.png)。

## 未执行（见 implementation.md「未执行项」）

- 验收 §3 在线验证：开发期不调在线模型，推迟到 P6。
- 验收 §2 其余生命周期与在线场景：上下文面板的确定性浏览器回归与本机显示联调已完成，不能替代在线生成验证。

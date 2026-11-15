# D2 浏览器验证记录

日期：2026-10-22。模型：配置中选择的在线 `deepseek-flash`。端口：`8946`。
本文件汇总命令与实际结果，事件、页面文字和 trace 保留在本机数据目录，不作为随仓库分发的附件。

## 首轮

在 `frontend/` 执行：

```powershell
$env:UV_CACHE_DIR='C:\Projects\Agent\myagent\.uv-cache'
$env:HASTINGS_E2E_PORT='8946'
npx playwright test --project=online --output=../data/test-tmp/playwright-d2-fixed-20261022
```

结果：2 通过、1 失败，退出码 1；没有自动重试。

| 流程 | 结果 |
|---|---|
| 可选参考发送、范围展示与引用草稿切换 | 通过，8.7 秒 |
| 改完检查会形成报告并与磁盘版本一致 | 失败，`items[0].target_digests is not iterable`；公开报告响应缺少目标版本字段 |
| 索引面板生成、外部改源失效与重新读取 | 通过，19.4 秒 |

首轮本地证据路径（相对仓库根目录）：

- `data/test-tmp/e2e-model/scope-flow-1792681543977.json`
- `data/test-tmp/e2e-model/first-failure-1792681587209.json`
- `data/test-tmp/e2e-model/index-stale-flow-1792681608473.json`
- `data/test-tmp/playwright-d2-fixed-20261022/model-flows-在线模型-改完检查会形成报告并与磁盘版本一致-online/trace.zip`

此前一次受限环境启动因 uv 默认缓存目录不可读而结束，没有执行模型用例，不计为在线验收。

## 修复后定向复测

公开报告响应补充 `target_digests`，前端类型同步，报告接口 17 项回归与前端构建通过后执行：

```powershell
$env:UV_CACHE_DIR='C:\Projects\Agent\myagent\.uv-cache'
$env:HASTINGS_E2E_PORT='8946'
npx playwright test --project=online --grep '改完检查会形成报告' --output=../data/test-tmp/playwright-d2-review-fixed-20261022
```

结果：1 通过，流程耗时 30.1 秒，命令总耗时 38.2 秒，退出码 0。
该流程核对完整报告、已保存目标版本、页面展示、刷新回看及外部修改后的过期提示。
本地事件证据为 `data/test-tmp/e2e-model/review-flow-1792681847130.json`。

三条流程分别已有通过证据；并未在补字段后重新执行全部三条。

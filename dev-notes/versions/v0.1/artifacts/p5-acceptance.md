# P5 验收记录（T7）

日期：2026-10-09。环境：Windows，`uv run python -m hastings.server`（默认 127.0.0.1:8000），模型 deepseek-flash（真实端点）。

验收方式说明：本阶段首次具备浏览器自动化能力，T7 采用**真实浏览器操作**（导航 / 点击 / 表单填充 / 键盘 / 快照）走完整 UI 路径，证据 = 浏览器快照文本 + DOM 读取 + REST 响应 + 落盘文件；不再像 P4 那样以 REST transcript 替代。

## DoD 逐条

### 1. 三栏创作台：正文居中阅读模式 ✅

- 打开 `http://127.0.0.1:8000/?w=web-acceptance`，浏览器快照确认三栏：左「主编对话」（可折叠）、中「羊皮纸正文」（章节下拉 / 字数 / 上一章·下一章）、右「故事账本」。
- 阅读态正文渲染正确：首字下沉 + 分段落，末尾「煤气灯又颤了一下。十二点五十分。」，字数 891。

### 2. 编辑模式：dirty 徽标、显式保存落盘、放弃草稿 ✅（放弃草稿见下注）

- 点「编辑」页签 → 切换为 `textarea[aria-label="正文编辑"]`（载入当前正文 932 字），面板顶部出现红色锁定横幅「✎ 作者编辑中 · 本章对 Agent 写入已锁定（write_chapter 将被拒绝）」。
- 经 `browser_fill` 把末尾「十二点五十分」改为「凌晨一点零七分」→ 出现「● 未保存」徽标、`保存 ⌘S` 按钮由 disabled 转为可用。
- 按 Ctrl+S → 保存成功（无错误横幅）；落盘副作用见 §3 归档证据。
- 注：「放弃草稿」按钮与 `releaseEditMark` 由 vitest/store 剧本覆盖，未在浏览器逐步操作。

### 3. 保存后自动对账：同步卡片 + 账本更新 + 手动重跑 ✅（重跑见下注）

- 保存后左侧对话流出现同步卡片，状态迁移 `对账中…` → `✓ 已同步`；卡片明细「◈ 对账完成 · 第1章 · 检查 1 处段落变更，账本更新 6 处」。
- 对账轮工具链（对话流可见）：`read_chapter` → 5×`bible_query`（timeline/method/character/clue/foreshadow）→ `bible_upsert 时间线事件「伊斯坦布尔旅馆初次交锋」已写入` → `timeline_check 时间线自洽`。
- **账本更新（核心断言）**：时间线 `t_end` 由 `00:50` → `01:07`，`note` 同步重写并记录旧值修正说明：

  ```json
  {"name":"伊斯坦布尔旅馆初次交锋","day":1,"t_start":"00:00","t_end":"01:07","chapter":"ch01",
   "note":"…正文结尾明确时刻为凌晨一点零七分（末段时间锚点），故事件结束时刻由 00:50 修正为 01:07…"}
  ```

- 归档（先归档后写主文件）：新增 `chapters/.revisions/ch01/001.md` + `001.json`：

  ```json
  {"ts":1791531653.89,"hunks":[{"kind":"modified","old_index":20,"new_index":20,
   "old_text":"煤气灯又颤了一下。十二点五十分。","new_text":"煤气灯又颤了一下。凌晨一点零七分。"}],
   "stats":{"chars":933,"paragraphs":21}}
  ```

- 对账视图（「对账」页签）：段落级色块 + 段内字符高亮正确——删除「十二点五十」（红删除线）+ 新增「凌晨一点零七」（绿底），共享后缀「分。」；页脚「对账视图：1 处段落变更」。
- 注：「重新同步」按钮（`sync` 命令）+ 手动重跑由 vitest/socket + ws 单测覆盖，未在浏览器重复触发（避免再次消耗一次真实 LLM 对账轮）。

### 4. 互斥：agent 写作中正文只读 ✅

- 经 Composer 发送「继续写第2章…」→ 顶栏出现「写作中」；此时「编辑」页签 `disabled=true`、`textarea` 不出现 → 正文保持只读，用户无法进入编辑态。
- 该轮运行实际产出：新增 `ch02`（`GET chapters` → `["ch01","ch02"]`）+ 时间线新条目「波洛登上东方快车」+ `bible_upsert 线索「船长靴跟的湿泥」`——证明运行确为真实写工具调用。
- 服务端 `is_running → 409` 半边由 REST 单测（T3）钉住。

### 5. 泄压阀 ✅（自动化覆盖）

- 空 diff →「无变更」正常结果、对账失败 → `failed` + 重试、过期提交 → 409：均由 pytest（rest/ws/reconcile）与 vitest（store/socket）剧本钉住，未在浏览器制造故障。

### 6. 红线 ✅

- `git diff --stat -- src/tau_agent src/tau_ai` 为空；`hastings`/`frontend` 零 `import tau_coding`（仅 docstring 提及）。
- REST 内容写入口 grep 复核：`@router.put …/chapters/{id}`（守门管线）为唯一正文写端点；`POST /api/workspaces`（案卷元数据创建）、`PUT/DELETE …/edit-mark`（锁，非内容）另计——符合 D3「写路径必须经守门管道」红线措辞演进。
- 工作区运行数据（`stories/`）不入库（`git status` 仅 `?? .vscode/`）。

### 7. 七道门全绿 ✅

- ruff check ✅ / ruff format --check ✅（121 files）/ mypy ✅（67 source files，零豁免）/ pytest ✅（333 passed）/ `npm run build`（含 tsc --noEmit）✅ / vitest ✅（89 passed）。

### 8. 浏览器人工验收存档 ✅（构建笔记 / R3 见 T8）

- 本文件即存档。构建笔记与 R3 blocker 清零属 T8。

## 验收中发现的唯一异常（非代码缺陷）

**现象**：首轮浏览器验收点击「编辑」即报「章节 ch01 加锁失败：Method Not Allowed」，`PUT .../edit-mark` 返回 405。

**定位**：探查运行中服务的 `openapi.json`，其章节路由仅有 `GET chapters` 与 `GET chapters/{chapter_id}`——缺少 T2/T5 新增的 PUT 与 edit-mark 路由。该 405 由静态托管挂载对非 GET 请求兜底返回，并非路由顺序问题，亦非代码缺陷：**运行中的 dev server 是早于 T2/T5 后端改动启动的陈旧进程**。

**处置**：停止陈旧进程，以当前代码重启 `uv run python -m hastings.server`，路由齐全（`GET,PUT chapters/{id}`、`PUT,DELETE edit-mark`），重跑验收全部通过。

**教训**：涉及后端契约新增的阶段验收，重启服务是前置条件——陈旧进程会把「缺路由」伪装成业务报错，浪费一轮排查。验收清单应含「重启服务后核对 openapi 路由」一步。
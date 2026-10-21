# 写作 Agent 与对话

核对日期：2026-10-21。本文描述单个写作 Agent、文件工具、作品材料装配、对话与轮次（V04-3）、每轮固定模型快照（V04-9），以及跨轮续聊（V04-10）。会话的建立、草稿与任务归属见[会话与草稿](conversations.md)，模型连接见[模型连接](model-connections.md)。

## 1. 职责

作者提出要求，Agent 直接读写作品目录里的 Markdown 文件，不再产出等待采用的候选。这一层包含三件事：

- **材料**：目录清单与三份设定按预算装进系统提示，其余按需用工具读取；
- **工具**：九个文件与 Git 工具，写入必须带读到的指纹；
- **轮次**：把一次请求执行完，记录事件、提交 Git、支持停止与中断。

没有规划者/写作者/审阅者的固定多角色流程，也没有自动摘要、抽取或审计。

## 2. 系统提示与材料

系统提示包含固定写作要求、当前目录清单与设定集内容：

| 部分 | 内容 |
|---|---|
| 写作要求 | 正文只写读者能看到的内容；线索支撑所有嫌疑人的可能性；真相用于规划与自查 |
| 目录 | 正文各章的路径、标题、大小；设定集三条及是否缺失 |
| 设定 | 三份文件按总预算装入；单个文件超预算时保留开头并写明「还剩多少字符，用 read_file 继续读」 |
| 工具规则 | 改前先读、写入带 digest、局部修改用 edit_file、受管路径写法 |

设定默认预算 6000 字符，按三份文件均分。装不下时明确说明，不静默截断后宣称读过全文。材料里的文字只是作品内容，不构成对应用的额外授权。

## 3. 工具

| 工具 | 作用 | 写入前提 |
|---|---|---|
| `list_files` | 列出正文与三份设定 | 只读 |
| `read_file` | 读取文本，返回 `digest`、总行数，支持 `offset`/`limit` 分页 | 只读 |
| `search_text` | 逐行搜索，返回路径、行号、摘录 | 只读 |
| `write_file` | 整体写入 | 新建不带 digest；改写必须带 |
| `edit_file` | 唯一匹配的局部替换 | 必须带 digest；原文 0 次或多次都失败 |
| `move_file` | 重命名/移动章节 | 必须带 digest；设定集不可移动 |
| `delete_file` | 删除章节 | 必须带 digest；设定集不可删除 |
| `git_diff` | 工作区差异 | 只读 |
| `git_log` | 最近提交 | 只读 |

工具成功表示内容已经落盘，返回的 `digest` 是落盘后的实际指纹。指纹过期、路径不受管、原文不唯一都作为失败返回给模型，由它重新读取，应用不替它猜。第一次真正写文件时才准备 Git 基线；只读操作不触发提交。

Git 的提交与撤销不暴露给模型：一轮结束时由应用统一记录，模型无法自行改写历史。

## 4. 一轮请求

```text
发送消息 → 登记轮次（turn_started）→ 调用模型
  → 工具调用逐条记录（tool_started / tool_finished）
  → 回复整条记录（assistant_message）
  → 结束时提交一次 Git 并记录结果（run_finished）
```

- 同一作品同时只允许一轮在写；第二轮返回 `WORKSPACE_BUSY_CONFLICT`，阅读不受影响。
- 只读讨论不准备基线、不提交，结果为 `no_change`。
- 停止保留已写入的文件，结果标为 `stopped`，提交标题带「已停止」。
- 一轮受理时就固定「连接 ID、显示名称、模型、实例」：之后切换、编辑或删除连接只影响下一轮；`turn_started` 事件带上这三个公开字段，界面显示本轮实际用的模型。
- 受理同一会话的下一轮时，按历史游标回放本会话的公开提示与回复，并把各轮实际执行结果写进系统提示（见[会话与草稿](conversations.md)第 2 节）；当前提示只追加一次。
- 模型或工具失败时结果标为 `failed`，已写入内容仍然记录；没有可用模型时这一轮不调用模型，直接记 `MODEL_NOT_CONFIGURED`（原因取自当前选择的解析结果）。
- 仓库已初始化但不可写（预暂存、合并冲突、分离头指针）时，在调用模型前就拒绝这一轮。

结果字段沿用 Git 层的两条轴：`status`（completed/partial/failed/stopped/interrupted）与 `recording`（committed/no_change/unrecorded）。

## 5. 会话与事件

每个作品在应用状态目录下有自己的会话登记与事件日志：

```text
~/.hastings/workspaces/<绑定 ID>/
├─ conversations.json      # 会话列表（标题、时间）
└─ <会话 ID>.jsonl         # 事件，按 seq 追加
```

事件类型：`turn_started`、`tool_started`、`tool_finished`、`assistant_message`、`error`、`run_finished`。`turn_started` 额外记录本轮的 `connection_id`、`connection_name` 与 `model`，不含任何凭据。事件只记定位信息（路径、查询词、工具名与结果摘要），不把整章正文写进日志。

进程重启后，第一次访问某个作品时会把「最后一条是 turn_started、没有 run_finished」的会话补一条 `interrupted` 记录；不自动继续，也不猜测已经完成。

## 6. 接口

| 方法与路径 | 行为 |
|---|---|
| `GET /api/file-workspaces/{id}/conversations` | 会话列表 |
| `POST /api/file-workspaces/{id}/conversations` | 新建会话 |
| `GET /api/file-workspaces/{id}/conversations/{cid}/messages?after=` | 回读事件，`after` 为游标 |
| `POST /api/file-workspaces/{id}/conversations/{cid}/turns` | 启动一轮，返回 202 与 `turn_id`；`request_key` 相同则不重复执行 |
| `POST /api/file-workspaces/{id}/turns/{tid}/stop` | 请求停止；已结束的轮次返回 `TURN_NOT_FOUND` |
| `GET /api/file-workspaces/{id}/events?conversation_id=&after=&follow=` | SSE 事件流；每轮以 `run_finished` 结束 |

`follow=false` 只回放已有事件，便于测试与断线补齐；`follow=true` 持续轮询到本轮结束。

## 7. 模型来源

每一轮按当前选择构造一个模型实例（`server/provider_runtime.py`）：协议来自连接保存的字段，凭据来自配置引用的系统存储，模型来自当前选择。协议、地址与模型都不按名称推断，也不读进程环境变量。

实例是每轮独占的：受理时创建，完成、失败、停止时关闭。连接编辑不需要「摘掉缓存里那个可能还在用的 client」，也不需要跨轮引用计数。

未配置时应用照常启动，发送消息的那一轮直接失败并说明如何配置。厂商报错（端点不通、key 不对、模型名不存在）同样让这一轮失败：事件里落一条 `MODEL_CALL_FAILED`，`run_finished` 状态为 `failed`，不会记成「已完成、没有改动」。

`--config` 指定配置文件，`--model` 是临时覆盖（不落盘，界面一旦修改选择就被取代）。旧工作台的数据库、账本与候选链路不参与。

## 8. 当前缺口

- **没有流式增量**：回复按整条消息写入事件，界面在模型结束后一次显示；token 级流式与打字机效果尚未实现。
- **没有会话压缩**：长会话不自动摘要或截断，厂商返回超限时原样说明。
- **浏览器内的真实创作未经走查**：真实模型链路已在应用接口层完成「连接 → 发现 → 测试 → 一轮写作 → 章节文件 + Git 提交」，界面渲染由构建、组件与状态测试覆盖，未在浏览器里逐屏取证。
- **事件不跨进程广播**：SSE 靠轮询事件文件，多进程同时写同一作品不在本版范围。

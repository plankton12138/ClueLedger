# Hastings 0.4 修改计划：Markdown 文件与 Git

> 编写日期：2026-10-20。状态：实施目标，尚未实现。
> 产品依据：[0.4 产品方案](../../product-design/v0.4/README.md)。现有代码依据：[0.3 模块导航](../v0.3/modules/README.md)。
> 0.3 已合并到 `main`，0.4 工作分支为 `codex/v0.4`。本次只开立计划，包版本暂为 `0.3.0`；V04-1 同步升级到 `0.4.0`。
> 本文的类型、路径、接口和示例是目标设计，不表示已经存在。阶段结果与验证另记在对应 `notes/` 中。

## 1. 目标与设计取舍

一部作品对应一个本地工作目录。Markdown 文件保存当前内容，Git 保存历史。Agent 通过读、搜索、写文件完成工作，不再生成等待采用的领域提案。作者在文件已修改后查看差异、继续调整或撤销。

这次调整覆盖内容模型、版本、Agent 工具、接口和前端数据来源。不能只把旧数据库内容换成 Markdown 导出，同时继续依赖原有的发布、候选、副本和账本链路。

### 1.1 必须保持的约束

1. 作品只有正文和设定集。正文一章一个文件；设定集只包含角色小传、线索、真相，首版各一个固定文件。
2. 磁盘文件是当前内容依据，包含尚未提交的外部修改；Git 记录实际文件变化，不再维护 Artifact、Revision、Commit 等自建版本对象。
3. 修改要求明确后直接写文件，不再增加采用或发布确认。普通讨论可以完全不写文件。
4. 应用只允许在当前作品的受管 Markdown 路径内操作；模型不能直接操作 `.git`、运行任意命令或修改其他工作目录。
5. 一个作品同时最多一个应用写任务。对话、导入、历史撤销共享这个限制；阅读不受阻。
6. 停止、失败或崩溃不自动恢复已经写入的文件。必须展示实际保存和 Git 记录情况，恢复后不自动重新调用模型。
7. 不自动生成摘要、抽取设定、审计或学习。不得通过迁移或兼容层重新引入旧工作流。

### 1.2 明确减少的能力

不再提供段落稳定 ID、角色认知对象、读者披露状态、证据锚点、依赖关系、图谱、跨章数据库事务和段落三方合并。跨文件写入可能部分完成，失败结果必须列出实际变化。

阅读器保留只读，支持普通 Markdown；不在本版增加应用内完整编辑器、Obsidian 插件兼容、分支管理、远程 Git 或多人协作。

## 2. 文件结构与身份

```text
作品目录/
├─ .git/
├─ .gitignore
├─ 正文/
│  ├─ 0001 雨夜.md
│  └─ 0002 来信.md
└─ 设定集/
   ├─ 角色小传.md
   ├─ 线索.md
   └─ 真相.md
```

- 作品名称默认取目录名；本地工作区编号仅用于绑定界面和聊天，不写入作品内容。
- 正文章节使用相对路径定位，按文件名排序。应用新建采用四位序号前缀，标题取首个一级标题，缺少时回退文件名。重命名即路径变化，不另外保存章节 ID 或顺序清单。
- 首版只管理 `正文/` 的直接子级 `.md` 文件和设定集三个固定路径。设定集不支持新增第四种文件，也不允许通过改名绕开类别。
- 文件使用 UTF-8；接受 UTF-8 BOM 和常见换行输入，应用写入使用 UTF-8、LF。读取时针对实际字节计算指纹，以识别外部改动。
- 不要求 YAML 头部、固定条目字段或数据库索引。设定内部使用 Markdown 标题和普通段落。
- `.obsidian/` 等外部工具文件不作为作品内容展示、搜索或提交。技术文件不构成额外内容类别。
- 聊天、运行结果、最近目录与阅读位置放在应用状态目录，模型凭据沿用专用配置存储；不得复制正文到这些记录中作为另一份当前稿。

新建目录时创建正文目录和三份空设定文档，初始化 Git。打开已有目录只检查和读取，不擅自改写文档；缺少受管文件时可按正常创建文件操作补齐。尚未初始化 Git 的目录可阅读，开始修改前由“启用版本记录”操作初始化，不能悄悄采用上级仓库。

## 3. 最小运行结构与旧代码去向

目标结构保持一个小包，不再为每种内容分别建立领域模型、仓库端口和发布服务：

```text
src/hastings/workspace/
├─ files.py       # 目录规则、读写、搜索、导入
├─ git.py         # 真实 Git 命令、差异、提交、最新操作撤销
├─ context.py     # 文件背景和原文读取范围
├─ agent.py       # 单个写作 Agent 与文件工具
├─ service.py     # 一轮请求、写入串行、结果与版本记录
├─ state.py       # 工作目录登记、聊天、运行事件
└─ api.py         # 工作目录接口和事件流
```

这些是职责划分，不要求每个文件增加对应接口类、工厂或第二实现。只在已有复用代码确实有边界时抽取公共部分。

| 现有代码 | 0.4 处理 |
|---|---|
| `src/tau_agent/`、`src/tau_ai/` | 复用现有模型与工具调用循环，不为 0.4 修改其通用语义 |
| `server/provider_config.py`、`model_access.py`、`provider_catalog.py`、凭据与本地模型能力 | 复用连接、模型选择、超时和凭据处理；从旧业务装配中拆出需要的路由 |
| `server/__main__.py`、`server/app.py`、`server/rest.py` | 新增独立文件应用装配；迁出仍在旧 REST 模块中的模型管理路由，避免为了模型设置挂载整套旧业务 |
| `server/v2.py`、`v2_agents.py`、`infrastructure/agents/tau_runner.py` | 新默认链路不装配；不能把只产出提案的旧工作者改名后当成直接写文件 Agent |
| `domain/manuscript/`、`domain/story/`、`infrastructure/repository/` | 退出新内容链路；旧数据导出按需单独使用读取能力 |
| 发布、候选、副本、摘要、建议、修订暂存、覆盖、Outbox 与关联撤销 | 不迁移到新运行链路；由文件写入和 Git 替代对应必要行为 |
| `interfaces/importers/manuscript_text.py` | 可复用 UTF-8 解码及分章纯函数；导入直接创建章文件，不建立预览候选或段落对象 |
| `infrastructure/conversations/` 与旧聊天领域 | 只复用独立可拆的流式处理思路；不继续依赖任务草案、候选、账本或 Repository |
| `frontend/src/components/desk/`、模型设置和通用界面组件 | 保留视觉布局，替换数据来源；设定集改为三个固定文档 |
| `frontend/src/state/v2.ts`、`v2socket.ts`、旧聊天任务与候选卡 | 默认页面移除依赖，改为工作目录、文件和运行状态 |

旧源码在新闭环形成之前保留以便核对，V04-5 再清理没有调用者的业务代码和测试。旧数据库、用户目录、个人笔记及历史文档不因源码清理而删除。

## 4. 模块方案与代码示例

以下片段只展示关键边界；省略的辅助函数需要在对应阶段实现和验证，不可直接当作完整生产代码。

### M01 文件工作区

用途：让阅读器、Agent 和外部编辑器操作同一份内容。列表和搜索直接从目录构建；内容读取返回路径、文本和字节指纹，不返回段落对象。

```python
from dataclasses import dataclass
from pathlib import PurePosixPath

SETTING_PATHS = frozenset({
    "设定集/角色小传.md", "设定集/线索.md", "设定集/真相.md",
})

def is_managed_path(path: str) -> bool:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or "\\" in path:
        return False
    return path in SETTING_PATHS or (
        len(p.parts) == 2 and p.parts[0] == "正文" and p.suffix == ".md"
    )

@dataclass(frozen=True)
class FileView:
    path: str
    text: str
    digest: str
```

实际访问还须校验 Windows 盘符、非法文件名、大小写冲突、解析后的根目录归属，并拒绝符号链接或目录联接绕出根目录。上述词法函数不是完整路径保护。

写入前核对上次读取的指纹，写入通过同目录临时文件替换完成，防止半个文件。`expected_digest=None` 仅表示创建不存在的文件，不能表示无条件覆盖。删除与移动只作用于正文章文件，也要核对来源和目标；三个设定文件只允许创建和改写。缺少、过期、不可解码和不可写分别返回可理解的错误。

导入 `.md` 默认一个文件一章，`.txt` 可沿用已有分章解析；默认新增，重名不覆盖。导入属于直接文件修改，同样生成 Git 记录，不恢复候选采用流程。

### M02 Git 历史

用途：保存真实文件历史，统一处理差异、外部编辑记录和撤销。作品仓库与开发本应用的源码仓库互相独立，不复用其分支或提交。

```python
import subprocess
from pathlib import Path

def run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        shell=False, capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise GitFailure(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout

def stage_changed_files(root: Path, paths: list[str]) -> None:
    # paths 已经逐个通过受管路径校验，包括被删除文件的旧路径。
    if paths:
        run_git(root, "add", "--", *paths)
```

禁止 `git add .`、`reset --hard`、`clean`、强制推送和重写历史。参数通过列表传入，中文、空格和路径特殊字符不得通过拼接 shell 处理。提交身份仅对本次命令提供，不修改作者全局 Git 配置。

**一轮修改的记录顺序：**

1. 验证 Git 根目录等于作品根目录、存在可用分支、没有合并冲突。检测到作者预先暂存的任何改动时停止自动写任务，不消费其暂存区；仍允许阅读和讨论。
2. 若受管文件已有未记录的外部修改，先形成独立“外部修改”提交并显示结果，得到干净的内容基线。非受管文件不加入提交。
3. Agent 按文件执行修改；每次写入前核对指纹。任务结束复查基线 HEAD 和文件状态，按实际受管变化创建一次提交；没有变化不提交。
4. 停止或失败但已经写入时，同样尝试记录一次标明“部分完成”的提交。Git 失败时保留文件，结果记为 `unrecorded`，允许单独补记历史，不自动重跑模型。

运行期间外部 HEAD、暂存区或相关文件变化时停止继续写入或自动提交，重新读取后再操作；不把未知改动混成模型结果。本版不承诺与任意外部编辑器之间的跨文件事务隔离；极小的并发窗口通过结果复核报告，不以自建版本仓库补偿。

差异直接读取 Git 的文件与行级差异。最新应用提交的撤销使用真实 `git revert`，前提是它仍为 HEAD、受管文件及暂存区干净；不满足时返回冲突说明。Git 命令失败或冲突时清理本次 Git 操作的中间状态并保留原工作文件，验证这一边界后才开放撤销按钮。不实现历史任意操作的自动三方撤销。

### M03 直接修改的写作 Agent

用途：让模型围绕文章完成任务，首版使用一个 Agent 和一组通用文件工具，不设规划者、写作者、审阅者的固定多角色流程。

```python
harness = AgentHarness(AgentHarnessConfig(
    provider=provider,
    model=selection.model,
    system=build_writing_instructions(context),
    tools=make_file_tools(workspace, run),
    max_turns=24,
))
async for event in harness.prompt(instruction):
    await session.append_event(event)
```

目标工具仅包括 `list_files`、`read_file`、`search_text`、`write_file`、`edit_file`、`move_file`、`delete_file` 和只读的 `git_diff`、`git_log`。`edit_file` 用唯一匹配的原文替换完成局部修改，避免每次重写长章；匹配不唯一或指纹过期时拒绝。Git 提交和撤销由应用协调，不给模型任意 Git 命令或系统 shell。读取按行或长度分页，写入携带读取指纹；操作已经保存才回报工具成功。

普通讨论先理解需求，可以只读取和回答；明确要求“写第一章”“修改某章”后直接写入，不产出候选卡。模型工具事件、回复、实际变更与错误进入同一个对话结果；重试创建新轮次并重新读取文件，不重放旧写调用。

指令要求线索保持读者观察角度，真相保存实际经过。模型可以同时读取两者，但必须区分“作者知道”与“正文已经告诉读者”。这属于创作要求与结果检查，不建立新一套披露状态机。

### M04 文件上下文

用途：为模型提供足够的作品材料，不把整书机械塞进提示词，也不先实现摘要系统。

```python
def initial_context(files, selected_path: str | None) -> dict:
    return {
        "catalog": files.list_managed(),
        "settings": files.read_settings_with_budget(),
        "selected_path": selected_path,
        "reading_rule": "改写前读取目标原文；材料不全时继续搜索、读取。",
    }
```

优先提供三个设定文件和目录；当前章及相关章按任务读取。设定过长时保留目录、明确未装入部分并让 Agent 分段读取，不能静默截断后宣称全文检查。记录实际读取路径及指纹，当前选中文章只是定位帮助，不自动扩大写入范围。

Markdown 内的文字属于作品材料，不把其中的命令或链接作为额外授权。创作要求来自当前作者对话和应用规则，不增加第四个“本书约定”文件。

### M05 轮次协调与应用状态

用途：把一个作者请求对应到实际文件和 Git 结果，同时保持失败处理容易理解。只维护必要的聊天与运行事件，不建立持久工作副本、任务草案、暂存发布或独立业务操作对象。

```python
async with workspace.write_lock:
    run = await state.record_started(run_id)
    try:
        # 文件工具在首次实际修改前才调用 prepare_content_baseline，
        # 并把基线记入 run；只读讨论不会触发提交或要求 Git 可写。
        await agent.execute(instruction, run=run)
    finally:
        # finish_run 内分别处理成功、停止、失败及 Git 记录失败。
        result = finish_run(run)
        await state.record_finished(run_id, result)
```

版本准备延迟到首次实际修改之前；无修改的讨论不记录外部改动，也不改变 HEAD。每次成功写入记录路径与写后指纹，结束时与磁盘核对，再计算实际差异；外部变更不能只凭“文件在任务期间变化”就归属给模型。

采用应用状态目录中的小型登记文件与会话 JSONL；记录事件、聊天和必要的运行定位，不保存第二套正文。完整事件结果至少区分 `completed`、`partial`、`failed`、`stopped`、`interrupted`；另用 `recording` 区分 `committed`、`no_change`、`unrecorded`，避免把保存状态和执行状态混为一谈。

进程重启将未结束轮次显示为中断，从磁盘和 Git 回读实际变化。无法可靠归属的未提交变化明确标注，作者可查看并补记，不猜测已经全部完成，不自动恢复或继续。

### M06 API 与前端

用途：界面直接反映工作目录，不沿用章节 ID、账本 kind、候选接受等旧接口数据形状。

新契约采用 `/api/file-workspaces`，与旧 `/api/workspaces`、`/api/v2` 区分；不按产品版本机械改为 `/api/v4`。以下路径均为目标接口：

| 方法与路径 | 职责 | 前端入口 |
|---|---|---|
| `GET/POST /api/file-workspaces` | 最近目录列表；按服务器本地路径创建或打开，返回本地绑定 ID | 顶栏选择、打开或新建目录 |
| `GET /{id}/files`、`GET /{id}/file?path=…` | 两块内容目录；文件原文、指纹 | 章节目录、正文、三个设定入口 |
| `GET /{id}/search?q=…` | 受管文本搜索与位置 | 阅读区查找、Agent 共用服务 |
| `POST /{id}/imports` | 上传正文并直接新增章文件 | 导入正文 |
| `POST /{id}/git/init` | 对已有目录启用版本记录 | 目录状态提示 |
| `GET /{id}/git/status`、`GET /{id}/git/diff`、`GET /{id}/git/log` | 保存状态、未记录差异、提交历史 | 改动与历史抽屉 |
| `GET /{id}/git/commits/{commit}/diff` | 读取指定提交差异 | 对话结果、历史条目 |
| `POST /{id}/git/record` | 单独补记文件历史，不调用模型 | 未记录改动的补记按钮 |
| `POST /{id}/git/revert` | 以预期 HEAD 撤销最新应用提交 | 最新结果中的撤销按钮 |
| `GET/POST /{id}/conversations` | 会话列表和新建 | 左栏对话历史 |
| `GET /{id}/conversations/{cid}/messages` | 回读聊天、运行结果 | 对话栏 |
| `POST /{id}/conversations/{cid}/turns` | 发送作者消息与启动一轮 | 对话输入框 |
| `POST /{id}/turns/{tid}/stop` | 停止，保留已写文件 | 对话停止按钮 |
| `GET /{id}/events?after=…` | 带游标的 SSE 事件与补齐 | 回复、工具过程、文件更新 |

除表格第一行外，路径均省略 `/api/file-workspaces`。发送重试复用请求键避免重复执行；显式重试失败轮次使用新请求键并注明 `retry_of`。创建会话、历史、停止和恢复必须有实际接口，不能只做模拟卡片。

```typescript
type SettingPath =
  | "设定集/角色小传.md"
  | "设定集/线索.md"
  | "设定集/真相.md";

interface RunResult {
  status: "completed" | "partial" | "failed" | "stopped" | "interrupted";
  recording: "committed" | "no_change" | "unrecorded";
  changed_paths: string[];
  commit: string | null;
}
```

前端新建 `api/workspace.ts`、`state/workspace.ts`，让 DeskShell 只订阅新状态；聊天工具过程和结果替换任务建议、候选与采用卡。正文和设定使用同一个只读 Markdown 阅读器，禁用内嵌 HTML 执行，不加载外部可执行内容。

文件写入事件后回读相关文件；切回窗口和点击刷新时重新扫描目录，首版不要求文件系统监听服务。外部编辑不会触发模型。切换作品后取消旧订阅，异步结果只更新其所属目录。

打开目录首版使用服务器本地路径输入，不把浏览器文件上传当作已取得本地目录持续读写权限。后端默认本机监听；远程任意目录浏览和远程托管不进入本版。

模型设置与本地模型功能保持已有可用入口；拆出其路由后验证不再间接装配旧账本、RAG 和正文接口。未接好真实 API 的按钮不能作为阶段完成。

实施期间拟用 `uv run python -m hastings.server --workspace <目录>` 显式启动新应用，旧启动方式暂留用于基线核对；该参数目前不存在。新应用只挂载文件工作区、必要的模型接口和前端，不同时挂载旧作品接口。V04-5 再将无参数启动切到新目录选择页，并退出旧业务默认装配。

### M07 旧作品导出与旧链路退出

用途：保留旧作品的可取用性，同时让新应用可以完全不打开旧数据库运行。导出是单次显式工具，不是新内容数据库的兼容写通道。

```python
def export_old_project(reader, project_id: str, target):
    target.require_empty_content_directory()
    snapshot = reader.current_project_snapshot(project_id)
    for index, chapter in enumerate(snapshot.chapters, start=1):
        target.create_chapter(index, chapter.title, chapter.text)
    target.write_three_settings(map_supported_settings(snapshot))
    return build_export_report(snapshot, target)
```

读取旧作品当前正式内容，把活动段落按顺序还原为章文件；不搬运工作副本、候选、旧任务或旧版本历史。角色条目写入角色小传，真实线索资料按可确认的读者观察与真实含义分别整理；无法确定归属时必须列为待人工整理，不凭字段名猜测已经披露。真实经过与手法写入真相。

其他旧对象及未映射字段写入应用状态目录的导出报告，列出内容与来源，不能静默丢弃，也不能生成第四种设定文档。旧数据库只读保留；新目录已有正文时拒绝覆盖。导出后由作者检查三份设定，再开启新创作。

若用户没有旧作品要迁移，导出工具可以后置，不阻塞新目录闭环；切换说明必须明确旧作品不会自动出现。旧数据库格式支持范围在实施时按真实数据核对，不能声称支持所有历史版本。

## 5. 实施批次与退出条件

每阶段只记录本阶段的目标、实际改动、契约与数据影响、取舍、验证、偏差和未完成项。模块说明只写已经实现的行为；不复制本计划或累计历史测试数量。

| 阶段 | 修改范围与顺序 | 退出条件 |
|---|---|---|
| V04-0 版本开立 | 收拢旧修改、合入 main、创建分支、写方案与导航 | 已完成，见[记录](notes/v04-0-version-baseline.md)；不表示运行功能已开工 |
| V04-1 文件基础 | M01；独立新工作区服务入口、目录读写与搜索、三份设定；同步包元数据 | 无模型和无旧数据库即可创建/打开目录、读写中文文件；外部编辑可回读；路径越界和过期覆盖被拒绝 |
| V04-2 Git 版本闭环 | M02、M05 必要协调；真实 Git 初始化、外部修改基线、差异、提交和最新撤销 | 真实临时仓库验证成功、无变化、部分完成、Git 失败与冲突；旧数据库不参与 |
| V04-3 Agent 与对话 | M03、M04、M05；复用模型连接、文件工具、单 Agent、聊天与事件、停止和中断 | 作者一句“写第一章”可直接创建文件并形成 Git 记录，无采用步骤；假模型工具测试通过，真实模型单独取证 |
| V04-4 前端接线 | M06；目录选择、Markdown 稿纸、三个设定入口、对话文件结果、差异历史、导入与外部刷新 | 浏览器经真实后端完成打开、创作、改写、读差异、撤销、刷新外部修改；无仅存在于前端的能力 |
| V04-5 默认切换与清理 | M07 按需导出；新运行默认、旧依赖退出、文档和测试整理 | 新链路不装配旧 Repository/候选/账本；启动不创建旧库；旧数据保留；运行说明与验收限制对应实测 |

阶段依赖：V04-1 → V04-2 → V04-3 → V04-4 → V04-5。先跑通文件到 Git 的最小闭环，再接模型和界面，不同时改写所有旧模块。

V04-1 包版本需同时修改 `pyproject.toml`、`src/hastings/__init__.py`、`uv.lock` 中本地包版本，以及 `frontend/package.json`、`package-lock.json`；通过包管理器更新锁文件并确认无无关依赖升级。产品版本与接口路径分别管理。

## 6. 验证与验收

测试以真实目录、真实 Git 和可控的模型工具调用为基础，不为每个薄封装增加镜像测试。阶段只运行相关回归；默认切换时再检查完整保留能力。

| 场景 | 必须证明的结果 |
|---|---|
| 新作品 | 自动建立两块内容、三份设定与独立 Git；不创建内容数据库 |
| 空作品写首章 | 明确创作请求后直接生成 `正文/*.md`，刷新仍在，有一次实际提交，没有候选采用 |
| 联动修改 | 一轮同时修改角色小传和某章，真实差异包含两文件，正常结束形成一个提交 |
| 三类设定 | 界面只有三个入口，模型不能新建时间线、第四类设定或旁路文件 |
| 纯讨论 | 可以只回答，文件和 HEAD 都不变，不生成空提交 |
| 外部编辑 | 外部写入后界面刷新可见；下一轮先单独记录作者改动；读取后又被修改的文件不被静默覆盖 |
| Git 独立性 | 上级仓库、未初始化、预暂存、未合并、中文空格路径、无全局身份等条件行为明确 |
| 记录失败 | 文件已保存但提交失败时显示 unrecorded，补记只执行 Git，不重跑模型 |
| 停止或失败 | 已保存文件仍在、可查看差异；未写文件不能显示已修改；部分改动尽力记录且准确标注 |
| 重启 | 未结束轮次显示中断，真实文件与 Git 可回读，不自动继续任务 |
| 最新撤销 | 干净且 HEAD 匹配时生成反向提交；有后续改动时拒绝，无未清理的冲突中间状态 |
| 路径边界 | 绝对路径、上级跳转、Windows 盘符、链接与目录联接、`.git`、非受管文件均不可借工具改写 |
| 接口一致性 | 目录、文件、聊天、流式事件、导入、差异和撤销均由浏览器调用真实 API；模拟器结果单独标注 |
| 模型能力 | 真实模型实际调用读写工具完成一次生成和一次改写；固定文本假模型通过不替代此证据 |
| 旧数据 | 原库不改写；显式导出按当前支持范围核对正文和三类设定，未映射资料可见 |

建议新测试目录为 `tests/workspace/`，前端测试按 `workspace` 模块组织，端到端使用临时作品目录。真实模型使用专门临时作品，不能把个人作品当测试材料。

每阶段运行相关 `uv run pytest ...`、前端测试与受影响构建。源码清理后再跑仍保留的整套检查；删除退役测试时记录其对应能力已退出，不把少跑测试当作质量提升。真实模型、独立服务和浏览器证据分别标注。

## 7. 文档与完成标准

实现时按需建立 `modules/`，只记录文件工作区、Git、Agent/对话和写作台等实际职责。V04-5 建立 `release.md`，说明 Git 安装、启动目录、应用状态位置、旧库处理、备份范围和当前限制；不要提前宣称可用。

完成切换时同步 [开发导航](../README.md)、[后端导航](../../src/hastings/README.md)、[前端说明](../../frontend/README.md)、[产品入口](../../product-design/README.md)和 AGENTS 中的当前实现入口。0.1–0.3 文档保留在历史入口，不批量改写历史决定。

0.4 完成的判断：不打开旧数据库，作者也能在普通目录中创作、修改和阅读文章；退出应用后，Markdown 与 Git 仍足以解释作品当前内容及已记录历史。聊天和模型配置的备份范围另行说明，不能声称 Git 包含全部应用状态。

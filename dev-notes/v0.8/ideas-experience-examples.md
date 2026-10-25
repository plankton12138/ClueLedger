# 0.8 灵感改造：分模块实现示例

日期：2026-10-25。状态：**设计示例，未实施、未编译验证**。
配套任务、边界与验收见[开发计划](ideas-experience-plan.md)。示例侧重职责和关键分支，
不是可直接复制的完整补丁；辅助函数、错误映射、锁、存储与事件调用应接项目既有实现。

## 1. 正文优先的契约与列表

为什么：记录只需要原文；列表预览和历史兼容字段不能反过来成为必填项。

```ts
type IdeaDraft = {
  key: string;
  workspaceId: string;
  ideaId?: string;
  body: string;
  editSequence: number;
  base?: { revisionId: string; digest: string };
};

type CreateIdeaInput = {
  body: string;
  request_key: string;
  origin?: {
    role: "user" | "assistant";
    conversation_id: string;
    message_seq: number;
    quote: string;
  };
};

type IdeaListItem = {
  idea_id: string;
  head_revision_id: string;
  excerpt: string;
  excerpt_truncated: boolean;
  body_chars: number;
  created_at: string;
  updated_at: string;
};

// 仅供显示：不写入 title，不作为编辑原文。
function referencePreview(body: string): string {
  const text = body.replace(/\s+/gu, " ").trim();
  const chars = Array.from(text);
  return chars.slice(0, 48).join("") + (chars.length > 48 ? "…" : "");
}
```

服务端继续接受旧 `title`、`tags`。新更新输入必须区分“字段未提交”和“提交空值”，
否则保存正文会清空旧标签。摘要的长度与截断按 Unicode 字符计算；进入编辑前读取完整修订。

## 2. 保存命令与后续动作

为什么：保存期间作者可能修改内容、切换条目或切换作品；下一步不能依赖全局当前选择。

```ts
type SavedIdea = Readonly<{
  workspaceId: string;
  ideaId: string;
  revisionId: string;
  body: string;
}>;

type SaveResult =
  | { status: "saved" | "unchanged"; value: SavedIdea }
  | { status: "failed"; error: string };

type SaveCommand = Readonly<{
  id: string;
  workspaceId: string;
  draftKey: string;
  sequence: number;
  ideaId?: string;
  input: {
    body: string;
    request_key: string;
    expected_revision?: string;
    expected_digest?: string;
  };
}>;

async function saveThenReference(target: DiscussionTarget): Promise<void> {
  // target 在点击时冻结，含作品、会话或独立的新会话草稿标识。
  const result = await saveExactDraft(target.workspaceId, target.draftKey);
  if (result.status === "failed") return;
  if (!targetStillAvailable(target)) return;
  attachReference(target, result.value); // 使用返回修订，不能重新读取 selected。
  // 作者已离开原作用域时，只更新对应草稿槽，不强行跳转或抢焦点。
  if (sameInteractionScope(target)) revealComposer(target);
}
```

`saveExactDraft` 先捕获草稿、序号和基线，再获取或创建固定命令。
同一命令进行中返回同一 Promise；结果不明重放同一请求键，不改输入。
成功后按原草稿键合并结果：

```ts
function reconcileSaved(
  command: SaveCommand, view: SavedView, current: IdeaDraft,
): IdeaDraft {
  return {
    ...current,
    ideaId: view.idea_id,
    base: { revisionId: view.revision_id, digest: view.digest },
    // 新增输入留在同一条灵感的修改稿中，避免重复新建。
    body: current.editSequence === command.sequence ? view.body : current.body,
  };
}
```

创建且内容未变化时，面板消费成功结果后建立新的空白记录草稿；
内容已变化时保留修改稿，下一次提交更新已创建条目。不能把它当成第二条全新记录。
返回 `SavedIdea.body` 必须来自服务端成功保存的文本，不能拿当前脏稿显示成已保存版本。
删除与恢复采用相同命令管理方式，但有各自的载荷与成功处理。

## 3. 唯一悬浮容器与内容复用

为什么：悬浮、窄屏与展开查看只是空间不同，草稿和数据不应该随容器切换重建。

```tsx
function IdeasSurface() {
  const surface = useIdeasSurface();
  const presentation = useIdeaPresentation(); // 基于实际空间及字体比例。
  return (
    <IdeaSurfaceFrame
      open={surface.open}
      presentation={presentation}
      modal={presentation === "compact"}
      onClose={surface.hide}
      returnFocus={surface.originElement}
    >
      <IdeasContent workspaceId={surface.workspaceId} />
    </IdeaSurfaceFrame>
  );
}

function IdeasContent({ workspaceId }: { workspaceId: string }) {
  return (
    <section aria-label="灵感">
      <IdeaSurfaceToolbar />
      <IdeaCapture workspaceId={workspaceId} />
      <IdeaRecords workspaceId={workspaceId} />
    </section>
  );
}
```

`IdeasContent` 不以 presentation 作为 `key`；`hide` 不调用草稿取消。
在外壳只挂载一个容器，旧路由只改变其展现和选中状态。需要模态的窄屏容器才管理背景不可交互。
现有 `AppDialog` 默认是模态，不能直接给宽屏面板套用并假定背景仍可操作。

输入处理：

```tsx
<textarea
  aria-label="记录灵感"
  value={draft.body}
  onChange={event => updateBody(event.target.value)}
  onKeyDown={event => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      void submitCapturedDraft();
    }
  }}
/>
```

搜索和菜单也应有可理解的名称；无障碍名称不等于重新加回可见的表单属性。

## 4. 固定版本引用与展开命令

为什么：原文可以继续修改，但已选和已发送的内容需要可解释、可重放。

```ts
type IdeaReferenceDraft = {
  workspaceId: string;
  ideaId: string;
  revisionId: string;
  preview: string;
};

type ExpansionCommand = Readonly<{
  requestKey: string;
  idea: SavedIdea;
  text: string;
}>;

async function sendExpansion(command: ExpansionCommand) {
  return createConversationWithText(command.idea.workspaceId, {
    text: command.text,
    request_key: command.requestKey,
    purpose: "expand_idea",
    context: {
      ...EMPTY_CONTEXT,
      idea_references: [{
        idea_id: command.idea.ideaId,
        revision_id: command.idea.revisionId,
      }],
    },
  });
}
```

此调用使用专用命令对象，不通过覆盖 `useConversationsStore` 的当前输入来间接发送。
前端 `createConversationWithText` 的现有类型和服务端请求需一起扩展；示例字段不能提前单边上线。
受理成功后才选中返回会话，并把原来位置记录为返回目标。
再次打开已有关联使用 `openConversation`，不是重放 `sendExpansion`。
“重放同一未知结果”和“生成失败后再试一次”是两种操作：前者保持原请求键，后者确认终态后创建新轮次。

## 5. 服务端引用解析与能力限制

为什么：已删除记录的新引用应被阻止；已经受理的版本则应继续稳定。

```python
def resolve_reference(self, idea_id: str, revision_id: str) -> dict[str, object]:
    # 示意：接入现有锁、读取和错误工厂，不另造存储实现。
    with self._lock(idea_id):
        document = self._read(idea_id)
        head = _revision(document, str(document["head_revision_id"]))
        selected = _revision(document, revision_id)
        if head["deleted"] or selected["deleted"]:
            raise reference_unavailable(idea_id, revision_id)
        return _idea_view(document, selected)
```

受理层将结果原文冻结到本轮上下文；执行中不再检查当前 head 来替换或撤销已受理版本。
新接口校验与工具检查都要有测试，不能仅靠前端禁用按钮。

能力组装示意：

```python
if purpose == "expand_idea":
    tools = build_idea_discussion_tools(run)
    # 基础只有授权灵感读取。搜索与保存需分别检查本轮明确许可。
    # 不注册正文写入、装配、发布、索引等工具。
    tools = enforce_idea_discussion_capabilities(tools, run)
else:
    tools = build_existing_tools(run, mode=existing_mode(purpose))
```

实际应在最终工具装配处检查，包含动态工具；权限依赖结构化用途而非工具名称字符串黑名单。
普通会话仍使用原执行路径，“用于写作”不是在讨论模式里临时放开写入能力。
新增用途必须进入请求去重、日志和事件重放，旧用途回归必须保持通过。

## 6. 活动事实与采用关联

为什么：发给模型、模型展开和真正改稿是不同事实，需要分别依据事件与实际修改记录。

```ts
type IdeaActivity =
  | {
      kind: "referenced" | "expanded";
      eventId: string;
      revisionId: string;
      conversationId: string;
      turnId: string;
      createdAt: string;
      runState: "accepted" | "running" | "completed" | "failed" | "stopped";
    }
  | {
      kind: "applied";
      eventId: string;
      revisionId: string;
      targetPath: string;
      targetDigest: string;
      start: number;
      end: number;
      quote: string;
      turnId: string;
      validity: "current" | "changed" | "reverted";
    };

type IdeaActivityPage = {
  items: IdeaActivity[];
  next_cursor: string | null;
  complete: boolean; // false 不等于没有活动，可能仍在按批重建派生索引。
};
```

事件身份以作品、会话、轮次、灵感版本和事实种类生成，不能在每次展示时随机创建。
其他既有作者关联可单独映射为 `linked` 类展示；不要把无法核对的旧关联硬转成 `applied`。

写入关联示意：

```python
if author_requested_application and verified_write_span is not None:
    # 来源是明确采用请求和文件实际修改结果，不是模型的完成声明。
    record_verified_application(
        idea_reference=accepted_source,
        write_span=verified_write_span,
        request_key=stable_application_key,
    )
```

落地时要先把实际写入结果记录进现有可靠事件，再派生使用关联；
关联写入失败可据此修复。不能先记“已采用”再等文件写入，也不能因补关联失败重做修改。
正文区间的当前位置核验复用现有字符范围和指纹规范，历史采用事实与当前有效性分别展示。

## 7. 对话选段与模型保存

为什么：一段讨论值得保留时应能直接留下原文；保存工具也不应重新引入标题和标签。

```ts
await createIdea(workspaceId, {
  body: selection.text,
  request_key: selectionCommand.requestKey,
  origin: {
    role: selection.role,
    conversation_id: selection.conversationId,
    message_seq: selection.messageSeq,
    quote: selection.text,
  },
});
```

保存的是选定消息的实际文本，不是重构后的 Markdown 或其他消息里的相同引文；
流式消息尚未形成稳定定位时，先等待稳定定位或明确不带不可靠来源，不能伪造序号。
网络重试保持本次命令；作者另一次独立“记成灵感”是新操作，不按正文全局去重。

模型保存仍校验本轮许可，但新 schema 仅公开 `body`。
幂等键应基于稳定的轮次和保存调用身份，而不是固定 `turn_id:idea`：

```python
# 示意：operation_id 在调用日志首次登记后保持稳定，重放时复用。
request_key = f"{run.turn_id}:idea:{operation_id}"
created = idea_store.create(
    title="", body=body, tags=[], request_key=request_key,
)
```

多个保存调用分别形成命令；同一调用重试必须复用操作身份。
工具原生调用 ID 如无法跨执行恢复保持稳定，应由既有调用日志登记持久操作身份后再写入。
去重解决重试，不替作者判断两个相同句子是否应该合并。

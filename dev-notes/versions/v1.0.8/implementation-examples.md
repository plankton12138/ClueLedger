# v1.0.8 分模块实现示例

日期：2026-11-02。以下用于固定设计边界，**均为拟议代码片段**，不是可直接运行的当前 API。省略的领域函数和组件需要按真实基线实现；不要复制示例后绕开现有授权、联合提交与来源校验。

## M1：同一对象、不同阅读范围

把来源解析交给领域服务，页面不推断正文事实。聚合层不调用模型，也不直接写数据库。

```python
def chapter_context(snapshot, path, cursor, limit):
    chapter = snapshot.require_prose(path)
    bindings = snapshot.resolve_bindings(path)  # 包含有效、歧义与失效来源
    evidence = snapshot.resolve_clue_evidence(path)
    rows = project_chapter_items(
        bindings=bindings,
        evidence=evidence,
        plans=snapshot.explicit_plans_for(chapter.number),
    )
    # 合并同一对象；保留同章多次出现和来源身份，不能按标题去重。
    rows = merge_object_occurrences(rows)
    return paginate_revision_bound(
        rows, cursor=cursor, limit=limit,
        revision=snapshot.view_revision(chapter.digest),
    )
```

计划只产生 planned 项；缺少原文不能伪造 observed 项。失效引用仍进入 unresolved 项。

## M2：打开讨论不发送，发送才冻结

准备引用和执行任务分开。界面切换不得修改已发请求；失败重试也不得生成第二次提交。

```typescript
function discuss(focus: DiscussionFocus) {
  discussionDraft.setFocus(focus);
  auxiliary.setMode("chat");
  // 不创建会话、不发核对请求、不调用模型。
}

async function sendDraft() {
  const request = structuredClone({
    text: discussionDraft.text,
    context: { ...baseContext(), discussion_focus: discussionDraft.focus },
    request_key: crypto.randomUUID(),
  });
  pendingRequests.keep(request);
  await sendOrCreateConversation(request);
}

async function retry(requestKey: string) {
  await sendOrCreateConversation(pendingRequests.get(requestKey));
}
```

`DiscussionFocus` 见[契约](data-and-api.md)。`baseContext`、`sendOrCreateConversation` 等为拟议适配函数，必须接现有首次发送、续聊和状态持久化。不能另建只支持演示的聊天服务。

## M3：调整明确字段，保留完整对象

模型的意图修改最终仍进入现有变更操作。内部最小改动合并由服务端完成，不要求作者维护隐藏属性。

```python
def prepare_node_adjustment(store, request, grant):
    grant.require_node(request.node_id)
    snapshot = store.read_current()
    require_revision(snapshot.revision, request.base_revision)
    current = snapshot.node(request.node_id)
    fields = validate_adjustment_fields(request.fields)
    # 此处返回领域对象，模型校验全部字段；禁止无校验地覆盖。
    changed = type(current).model_validate({
        **current.model_dump(), **fields,
    })
    return UpdateNode(node_id=current.id, node=changed)

# 上述 operation 交给现有联合提交服务：再次校验修订、授权和读取依赖。
# 不在 prepare 阶段写文件；提交失败不能留下部分正文或账本变化。
```

拟议 `validate_adjustment_fields` 必须使用允许字段清单、区分未提供与显式清空，并禁止修改对象身份。探索任务拿不到写授权，字段校验不能替代权限检查。采用建议时引用具体建议版本；未经采用的提议只保留在对话或任务草稿。

## M4：共用只读详情

组件没有字段 setter、保存或分类按钮。标题、原文和作者意图保留各自语义。

```tsx
function ObjectReadDetail({ view, onDiscuss, onLocate }: ReadDetailProps) {
  return <article aria-label={view.title}>
    <h2>{view.title}</h2>
    {view.intent && <section aria-label="作者安排"><p>{view.intent}</p></section>}
    {view.occurrences.length ? view.occurrences.map(source =>
      <SourceExcerpt key={source.key} source={source}
        onLocate={() => onLocate(source)} />
    ) : <p>暂无已登记正文出处</p>}
    {view.notice && <SourceNotice notice={view.notice} />}
    <button onClick={() => onDiscuss(view.focus)}>讨论调整</button>
  </article>;
}
```

`SourceExcerpt` 需要明确失效来源，失效时不可定位到猜测位置。无作者意图时不渲染空字段；正文原句不能被无依据的摘要替代。构思和线索可使用不同只读段落排版，但共用来源和讨论行为。

## M5：防止快速切章的旧响应覆盖

请求键必须包含作品、方案、章节和当前已知修订；切章时清除旧章视图，保留未发送的对话草稿。

```typescript
useEffect(() => {
  const controller = new AbortController();
  let active = true;
  setView(null);
  setError(null);
  readChapterContext(workspaceId, variantId, path, controller.signal)
    .then(view => { if (active) setView(view); })
    .catch(error => {
      if (active && error.name !== "AbortError") setError(error);
    });
  return () => { active = false; controller.abort(); };
}, [workspaceId, variantId, path, readRevision]);
```

此处 `readChapterContext` 是新接口的拟议客户端包装，必须真实实现。生产代码同时接入读取缓存与统一失效，不能仅靠组件重新挂载保证一致。

## M6：以真实回执刷新双布局

回执状态下列命名仅为前端适配类型，不表示后端已有同名字段。适配层须将现有正式提交、任务草稿和错误结果逐一映射，不能从回复文本识别成功。

```typescript
function onChangeResult(result: ChangeResult) {
  if (result.kind === "committed") {
    readingCache.invalidateWorkspace(result.workspaceId, result.variantId);
    chatReceipts.showCommitted(result.changeId, result.summary);
  } else if (result.kind === "proposal") {
    chatReceipts.showProposal(result.proposalRef);
  } else if (result.kind === "unchanged") {
    chatReceipts.showNoChange(result.reason);
  } else {
    chatReceipts.showFailure(result.error);
  }
}
```

第一版可按受影响作品 / 方案整体失效，之后有证据再细化缓存范围。撤销成功进入相同失效路径，按钮放在对话回执；撤销失败不能在浏览器先回滚再假装成功。

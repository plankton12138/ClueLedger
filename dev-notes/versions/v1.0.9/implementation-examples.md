# v1.0.9 分模块实现示例

日期：2026-11-02。以下代码用于固定设计边界，**全部是拟议片段，不是当前可直接调用的 API**。实施时必须接入现有修订、授权、材料、联合提交、回执和恢复服务；不能复制示例后建立一条绕过现有校验的简化路径。

## M1：让最小构思成为领域规则

作者输入自然语言，内部结构负责验证“能否开始”，而不是让页面检查字段数量。

```python
from pydantic import BaseModel


class ChapterPlan(BaseModel):
    id: str
    title: str | None = None
    intent: str = ""
    information_move_ids: tuple[str, ...] = ()
    revision: str
    confirmed_revision: str | None = None
    prose_path: str | None = None


class StoryVariant(BaseModel):
    format_version: int = 3
    revision: str
    chapter_plans: tuple[ChapterPlan, ...] = ()
    information_moves: tuple[InformationMove, ...] = ()

    def chapter_start_problem(self, chapter_id: str) -> str | None:
        chapter = next((row for row in self.chapter_plans if row.id == chapter_id), None)
        if chapter is None:
            return "章节构思不存在"
        if not chapter.intent.strip():
            return "还没有说明本章要改变什么"
        move_ids = {move.id for move in self.information_moves if move.chapter_id == chapter_id}
        if not move_ids.intersection(chapter.information_move_ids):
            return "还没有说明本章的信息变化"
        if chapter.confirmed_revision != chapter.revision:
            return "章节构思尚未按当前修订确认"
        return None
```

`InformationMove.effect` 可以为空；开始条件只要求存在真实的信息变化，不要求作者选择“引入 / 回收”等内部作用。`revision` 只由本章意图和信息变化计算，不包含确认字段本身。确认修订由服务端写入，客户端不能自行把 `confirmed_revision` 设置成本章当前修订。其他章节变化不会让本章无故失去确认。

## M2：所有正文写入共用一个策略

文件服务接收注入的策略，使 HTTP、Agent 和章节创建不会各写一份门槛逻辑。预检发生在任何文件或 Git 副作用之前。

```python
class ChapterWritePolicy:
    def __init__(self, story_store: StoryStore) -> None:
        self._story_store = story_store

    def require_confirmed(self, path: str, *, expected_chapter_revision: str | None) -> None:
        if managed_kind(path) != "chapter":
            return
        if expected_chapter_revision is None:
            raise WorkspaceError(
                code="CHAPTER_PLAN_STALE",
                where=path,
                why="这次正文写入没有绑定章节构思修订",
                fix="重新读取本章构思后再保存正文",
            )
        snapshot = self._story_store.read_current()
        chapter = snapshot.chapter_for_path(path)
        if chapter is None or snapshot.chapter_start_problem(chapter.id):
            raise WorkspaceError(
                code="CHAPTER_PLAN_REQUIRED",
                where=path,
                why="本章尚无已确认构思",
                fix="先写一句本章变化，或选择“交给模型构思”",
            )
        require_revision(chapter.revision, expected_chapter_revision, where=path)


class WorkspaceFiles:
    def __init__(self, root: Path, chapter_policy: ChapterWritePolicy) -> None:
        self._root = root
        self._chapter_policy = chapter_policy

    def check_write_intent(
        self,
        path: str,
        *,
        expected_digest: str | None,
        expected_chapter_revision: str | None = None,
    ) -> None:
        self._chapter_policy.require_confirmed(
            path, expected_chapter_revision=expected_chapter_revision,
        )
        self._check_expected(self._target(path), path, expected_digest)
```

章节第一次创建不能直接调用普通 `write`。它由事务服务分配路径并在同一提交中确认构思：

```python
def start_chapter(command: StartChapter, unit: StoryProseUnitOfWork) -> StartReceipt:
    with unit.lock(command.workspace_id, command.chapter_id):
        cached = unit.requests.result(command.request_key)
        if cached:
            return StartReceipt.model_validate(cached)

        story = unit.story.read_current()
        chapter = story.chapter(command.chapter_id)
        require_revision(story.revision, command.expected_story_revision)
        require_revision(chapter.revision, command.expected_chapter_revision)
        require_startable(story, command.chapter_id)
        path = unit.files.reserve_chapter_path(chapter.title)
        start_grant = unit.chapter_policy.authorize_start(
            chapter_id=chapter.id,
            chapter_revision=chapter.revision,
            path=path,
            request_key=command.request_key,
        )

        change = joint_change(
            confirm_chapter(command.chapter_id, chapter.revision),
            create_prose(path, initial_chapter_text(story, command.chapter_id)),
        )
        receipt = unit.commit(
            change,
            request_key=command.request_key,
            transient_start_grant=start_grant,
        )
        return StartReceipt.from_commit(receipt, path=path)
```

实际实现要复用现有联合提交与回滚服务，不能用两个顺序调用替代事务。

## M3：先返回确定性索引，再异步分析新语义

保存正文的成功不依赖模型。已有锚点的变化随保存完成，只有计划外内容和语义差异进入任务队列。

```python
async def save_chapter_prose(command: SaveProse, services: Services) -> ProseSaveResult:
    services.chapter_policy.require_confirmed(
        command.path,
        expected_chapter_revision=command.expected_chapter_revision,
    )
    before = services.files.read(command.path)
    require_digest(before.digest, command.expected_digest)

    receipt = services.prose_commit.commit(command, before=before)
    deterministic = services.index.refresh_anchors(
        chapter_id=command.chapter_id,
        before=before,
        after=receipt.file,
        plan_revision=receipt.plan_revision,
        chapter_revision=receipt.chapter_revision,
    )

    changed_ranges = changed_paragraph_ranges(before.text, receipt.file.text)
    job_id = None
    if changed_ranges and services.models.available_for("story_index"):
        job_id = services.index_jobs.replace_latest(
            chapter_id=command.chapter_id,
            context_revision=services.index.context_revision(command.chapter_id),
            prose_digest=receipt.file.digest,
            changed_ranges=changed_ranges,
        )

    return ProseSaveResult(
        path=receipt.file.path,
        prose_digest=receipt.file.digest,
        plan_revision=receipt.plan_revision,
        chapter_revision=receipt.chapter_revision,
        index_revision=deterministic.revision,
        index=deterministic,
        analysis_job_id=job_id,
        committed_change_id=receipt.change_id,
    )
```

任务发布前再次核对基础修订：

```python
def publish_index_candidate(job: IndexJob, result: SemanticIndexResult, store: WorkbenchStore) -> None:
    current = store.revisions(job.chapter_id)
    if (current.context_revision, current.prose_digest) != (
        job.context_revision, job.prose_digest,
    ):
        store.finish_job(job.id, status="stale")
        return
    store.publish_candidates(job, validate_quotes(result, current.text))
```

模型返回的引文仍由服务端在当前原文中验证。关键词相似、找不到原句或多处歧义都不能建立正式绑定。

## M4：一个通用模型入口，不覆盖正在输入的内容

创作输入组件把人工草稿、模型请求和候选分开。按钮文案由目标语义决定，不显示含糊的统一标签。

```tsx
type CreativeTextFieldProps = {
  label: string;
  value: string;
  onChange(value: string): void;
  assist: {
    label: string;
    kind: AssistKind;
    target: CreativeTarget;
    base: AssistBase;
  };
  model: { available: boolean; disabledReason?: string };
};

function CreativeTextField({ label, value, onChange, assist, model }: CreativeTextFieldProps) {
  const [candidate, setCandidate] = useState<CreativeTextCandidate | null>(null);
  const draftRevision = useDraftRevision(value);

  async function generate() {
    const result = await createCreativeAssist({
      kind: assist.kind,
      target: assist.target,
      base: { ...assist.base, draft_revision: draftRevision },
      request_key: requestKey(assist.target, draftRevision),
    });
    setCandidate(result); // 不调用 onChange，不覆盖输入。
  }

  return <section className="creative-field">
    <label>{label}<textarea value={value} onChange={e => onChange(e.target.value)} /></label>
    <Button
      onClick={() => void generate()}
      disabled={!model.available}
      disabledReason={model.disabledReason}
    >{assist.label}</Button>
    {candidate && <ModelCandidate
      candidate={candidate}
      stale={candidate.base.draft_revision !== draftRevision}
      onUse={text => onChange(text)}
      onDiscard={() => setCandidate(null)}
    />}
  </section>;
}
```

服务端根据目标形成任务意图，客户端不维护隐藏长提示词：

```python
ASSIST_TASKS: dict[AssistKind, Callable[[AssistContext], str]] = {
    "chapter_plan": lambda c: (
        "根据当前正式全书意图，为选中章节提出一个最小构思候选："
        "说明本章变化和至少一项信息变化；未知真相保持未知。"
    ),
    "next_action": lambda c: (
        "根据当前选择给出三个可由作者发送的下一步指令；只提出选择，不执行修改。"
    ),
}
```

实际任务仍通过现有模型选择、材料装配、事件、停止和恢复链路。`author_note` 为空是合法输入。

## M5：路由只表达选择，不表达旧页面模式

统一页面根据当前选择和领域状态决定显示全书、构思门槛或正文，而不是根据 `story / clues / chapter` 切换应用。

```typescript
export type WritingPage = {
  kind: "writing";
  workspaceId: string;
  selection?:
    | { kind: "book" }
    | { kind: "chapter"; chapterId: string; informationId?: string };
};

function normalizeLegacyWriting(params: URLSearchParams): WritingPage["selection"] {
  // 旧 mode 只用于回落，不恢复旧页面。
  const chapterId = params.get("chapter");
  return chapterId
    ? { kind: "chapter", chapterId }
    : { kind: "book" };
}
```

工作台只有一个主区域：

```tsx
function WritingWorkbench({ view }: { view: WritingWorkbenchView }) {
  if (view.selection.kind === "book") {
    return <BookPlanView book={view.book} />;
  }
  if (!view.chapter) return <NotFound />;
  if (!view.chapter.plan.confirmed || !view.chapter.prose) {
    return <ChapterPlanGate chapter={view.chapter} model={view.model_support} />;
  }
  return <ChapterManuscript
    plan={view.chapter.plan}
    prose={view.chapter.prose}
    index={view.chapter.index}
  />;
}
```

正文编辑状态把本地草稿与服务端版本分开，冲突不清空草稿：

```typescript
async function saveDraft(draft: LocalChapterDraft) {
  try {
    const result = await saveChapterProse(draft.chapterId, {
      text: draft.text,
      expected_digest: draft.baseDigest,
      expected_chapter_revision: draft.chapterRevision,
      draft_revision: draft.revision,
      request_key: draft.requestKey,
    });
    chapterDrafts.markSaved(draft, result);
    workbench.acceptDeterministicIndex(result);
  } catch (error) {
    if (isApiError(error, "PROSE_DIGEST_MISMATCH")) {
      chapterDrafts.keepLocalAndShowConflict(draft, error.current);
      return;
    }
    chapterDrafts.keepLocalAndShowError(draft, error);
  }
}
```

自动保存只合并已经结束的输入事务，中文输入法 composition 期间不得发送。

## M6：页边索引接受的结果必须属于当前正文

页边索引按原句位置排序和定位。响应匹配不只检查章节 ID，还检查计划、正文和索引修订。

```typescript
function belongsToCurrentView(
  incoming: ChapterInformationIndex,
  current: WritingWorkbenchView,
): boolean {
  const chapter = current.chapter;
  return Boolean(
    chapter?.prose &&
    incoming.chapter_revision === chapter.plan.revision &&
    incoming.context_revision === chapter.index.context_revision &&
    incoming.prose_digest === chapter.prose.digest,
  );
}

function onIndexUpdated(incoming: ChapterInformationIndex) {
  const current = useWritingWorkbenchStore.getState().view;
  if (!current || !belongsToCurrentView(incoming, current)) return;
  useWritingWorkbenchStore.getState().replaceIndex(incoming);
}
```

辅助区一次只显示索引或对话：

```tsx
function WritingAuxiliary({ mode, index, chat }: Props) {
  return <aside className="writing-auxiliary">
    {mode === "chat"
      ? <ConversationPanel {...chat} />
      : <ChapterMarginIndex index={index} />}
  </aside>;
}
```

窄屏不是把两个组件挤成窄栏，而是正文、索引详情和对话逐层进入。页面状态保存光标、选择、滚动和未发送草稿；返回不重新读取并覆盖本地编辑。

## M7：Aqua 由 token 和状态组成

材质集中在共享控件，不在业务组件复制渐变。以下颜色是实施起点，最终以视觉回归和对比度检查为准。

```css
:root {
  --aqua-chrome-top: #f7f8f9;
  --aqua-chrome-mid: #d9dde1;
  --aqua-chrome-bottom: #b8bec5;
  --aqua-border: #7d8791;
  --aqua-inner-light: rgb(255 255 255 / 0.84);
  --aqua-blue-top: #80c8ff;
  --aqua-blue-mid: #1688ee;
  --aqua-blue-bottom: #0063bd;
  --aqua-selection: #c9e7ff;
  --paper: #fffdf7;
  --paper-ink: #25231f;
  --focus-ring: #147fd1;
}

.ui-button {
  border: 1px solid var(--aqua-border);
  border-radius: 0.65rem;
  background: linear-gradient(
    to bottom,
    var(--aqua-chrome-top),
    var(--aqua-chrome-mid) 52%,
    var(--aqua-chrome-bottom)
  );
  box-shadow:
    inset 0 1px var(--aqua-inner-light),
    0 1px 1px rgb(32 43 54 / 0.18);
}

.ui-button[data-variant="primary"] {
  color: white;
  background: linear-gradient(
    to bottom,
    var(--aqua-blue-top),
    var(--aqua-blue-mid) 48%,
    var(--aqua-blue-bottom)
  );
}

.ui-button:active:not(:disabled) {
  transform: translateY(1px);
  box-shadow: inset 0 2px 4px rgb(0 47 91 / 0.34);
}

.ui-button:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--focus-ring) 42%, transparent);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; }
}
```

正文稿纸不使用蓝色高光或强阴影。Aqua 的辨识度来自 chrome、控件层级与按压反馈，不是内容卡片数量。

## M8：用消费者矩阵控制删除

旧接口只有在 UI、Agent、上下文、候选、恢复和测试都没有消费者后才能删除。消费者清单可以生成，但最终判断必须人工复核。

```python
@pytest.mark.parametrize(
    ("path", "expected_kind"),
    [
        ("#/workspaces/ws1/writing?mode=story", "writing"),
        ("#/workspaces/ws1/writing?mode=clues", "writing"),
        ("#/workspaces/ws1/writing?mode=chapter", "writing"),
    ],
)
def test_legacy_modes_fall_back_to_one_workbench(browser, path, expected_kind):
    browser.open(path)
    assert browser.page_kind() == expected_kind
    assert browser.nav_labels() == ["写作", "模块库"]
    assert not browser.has_text("关系图")


def test_every_chapter_mutation_runs_plan_policy(instrumented_workspace):
    for mutate in instrumented_workspace.chapter_mutation_entrypoints():
        result = mutate(without_confirmed_plan=True)
        assert result.error_code == "CHAPTER_PLAN_REQUIRED"
        assert instrumented_workspace.files_unchanged()
```

前一个示例实际应由前端路由测试和浏览器测试分别实现；后一个示例需要 P0 清单显式登记入口，不能靠运行时反射猜测所有调用方。版本文件只在验收通过后统一修改。

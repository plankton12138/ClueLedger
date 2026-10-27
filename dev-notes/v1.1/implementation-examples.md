# 1.1 分模块实现示例

> 2026-10-27 版本调整：原 1.0 修订学习计划顺延为 1.1 预留；优先完成 [1.0 悬疑线索设计与写作联动](../../product-design/v1.0/README.md)。本计划尚未实施，实施前按 1.0 实际交付重新核对基线。

日期：2026-10-26。状态：**设计示例，尚未接入生产**。

目的在于把模块边界和失败处理具体化。标注 Protocol 的依赖需在生产中用真实文件、Git、记录库和
provider 适配；示例不能替代[数据契约](data-and-recovery.md)、[接口](api-and-frontend.md)或正式验收。
Python 示例分别检查语法，纯逻辑块另做独立验证；TypeScript 块按各自作用域检查。
本文不包含生产迁移和所有平台处理，不宣称可直接复制后发布。

## 1. 编辑与结果类型

完整字节指纹用于并发，规范化文本用于展示，不能混用两者。

```python
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

FileState = Literal["saved", "no_change", "not_saved"]
RecordingState = Literal["committed", "no_change", "unrecorded"]


@dataclass(frozen=True)
class TextSnapshot:
    raw: bytes
    text: str
    digest: str

    @classmethod
    def read(cls, raw: bytes) -> TextSnapshot:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return cls(raw=raw, text=text, digest=sha256(raw).hexdigest())


@dataclass(frozen=True)
class SaveCommand:
    session_id: str
    base_id: str
    expected_digest: str
    text: str
    client_sequence: int
    request_key: str


@dataclass(frozen=True)
class SaveResult:
    edit_id: str
    file_status: FileState
    recording_status: RecordingState
    digest: str | None
    commit: str | None
    recovery: Literal["not_needed", "required"]
```

生产输入仍复用 Workspace 的 NUL、路径、编码、元信息与大小校验。
来源由具体服务写入，SaveCommand 不接收 human/actor 之类可提升来源的客户端字段。

## 2. 原子写入占用

这里的锁只保护登记操作，持续占用通过 owner 表表达；不会一直持 threading 锁等待模型。
所有写入口共用同一实例，不再各自先 is_busy 再 mark。

```python
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from collections.abc import Iterator


class Busy(RuntimeError):
    pass


class WriteCoordinator:
    def __init__(self) -> None:
        self._guard = RLock()
        self._owners: dict[str, str] = {}

    @staticmethod
    def key(root: Path) -> str:
        return str(root.resolve())

    def claim(self, root: Path, owner: str) -> None:
        key = self.key(root)
        with self._guard:
            if key in self._owners:
                raise Busy("WORKSPACE_BUSY_CONFLICT")
            self._owners[key] = owner

    def release(self, root: Path, owner: str) -> None:
        key = self.key(root)
        with self._guard:
            if self._owners.get(key) == owner:
                del self._owners[key]

    def require_owner(self, root: Path, owner: str) -> None:
        with self._guard:
            if self._owners.get(self.key(root)) != owner:
                raise Busy("WORKSPACE_OWNER_CONFLICT")

    @contextmanager
    def reserve(self, root: Path, owner: str) -> Iterator[None]:
        self.claim(root, owner)
        try:
            yield
        finally:
            self.release(root, owner)
```

前台轮次受理成功后保留 owner 至 finally 收尾；受理失败释放。
轮内工具使用 require_owner，不重复 claim；人工保存使用 reserve 包裹保存阶段。
跨进程和外部工具仍需指纹/HEAD 核对，不能由此声称全部互斥。
改名/删除另用生命周期 generation 封住后台发布，不能只调用 is_busy。

## 3. 短事务与决策幂等

网络请求和 Git 操作都不放入数据库事务。下面 once 只用于同事务可完成的作者决策，
文件保存要用下一节的阶段意图，不能用 once 包住所有外部副作用。

```python
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(path, timeout=1.5, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def payload_hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def once(
    con: sqlite3.Connection,
    key: str,
    payload: dict[str, object],
    apply: Callable[[], dict[str, object]],
) -> dict[str, object]:
    digest = payload_hash(payload)
    old = con.execute(
        "SELECT payload_hash, receipt_json FROM decisions WHERE request_key=?",
        (key,),
    ).fetchone()
    if old:
        if old["payload_hash"] != digest:
            raise ValueError("REQUEST_KEY_CONFLICT")
        return json.loads(old["receipt_json"])
    receipt = apply()
    # receipt 只保存决策 ID、规则 ID、版本和提交时状态，不缓存可被忘记的规则正文。
    con.execute(
        "INSERT INTO decisions VALUES(?, ?, ?)",
        (key, digest, json.dumps(receipt, ensure_ascii=False)),
    )
    return receipt
```

表 DDL、格式迁移和 journal_mode 在显式初始化中执行；GET 不通过 connect 创建空库。
SQLite 的事务和备份行为以 [Python 3.12 文档](https://docs.python.org/3.12/library/sqlite3.html)核对，
实现时不混用默认隐式事务与显式 BEGIN。

## 4. 文件保存编排

下例定义必要适配边界，展示「同键不再次写入」「意图先于文件」「写后异常返回实际状态」。
生产 journal 必须在 prepared 阶段保存完整快照和预期 HEAD，reconcile 不产生外部写入。

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Command:
    key: str
    payload_hash: str
    base_digest: str
    text: str


@dataclass(frozen=True)
class Result:
    edit_id: str
    file: str
    recording: str
    recovery: str


class Journal(Protocol):
    def lookup(self, key: str, payload_hash: str) -> str | None: ...
    def prepare(self, command: Command, before: bytes, head: str | None) -> str: ...
    def file_written(self, edit_id: str, digest: str) -> None: ...
    def finish(self, edit_id: str, commit: str | None) -> Result: ...
    def reconcile(self, edit_id: str) -> Result: ...


class Files(Protocol):
    def read_raw(self) -> tuple[bytes, str]: ...
    def write_cas(self, text: str, expected_digest: str) -> str: ...


class Versions(Protocol):
    def prepare_baseline(self) -> str | None: ...
    def record_verified(self, edit_id: str, expected_head: str | None) -> str | None: ...


def save_under_claim(
    command: Command, journal: Journal, files: Files, versions: Versions
) -> Result:
    previous = journal.lookup(command.key, command.payload_hash)
    if previous is not None:
        return journal.reconcile(previous)  # 包含已完成重放，不重写或再次提交
    raw, current_digest = files.read_raw()
    if current_digest != command.base_digest:
        raise ValueError("FILE_STALE_CONFLICT")
    # adapter 单独标记未知改动；Git 不可用允许 head=None 并留下原因。
    head = versions.prepare_baseline()
    raw_after_baseline, digest_after_baseline = files.read_raw()
    if digest_after_baseline != current_digest or raw_after_baseline != raw:
        raise ValueError("FILE_STALE_CONFLICT")
    edit_id = journal.prepare(command, raw, head)
    try:
        digest = files.write_cas(command.text, current_digest)
        journal.file_written(edit_id, digest)
        commit = versions.record_verified(edit_id, head)
        return journal.finish(edit_id, commit)
    except Exception:
        # 文件或 Git 可能已经改变，由原文、快照、提交核对实际结果。
        # 即使这里状态库不可写，reconcile 也需返回可验证结果和 recovery=required。
        return journal.reconcile(edit_id)
```

adapter 不得将所有 Git 错误都当可提交；暂存区/外部 HEAD 竞争保持未记录。
write_cas 复用 Workspace.write，但外部软件不受本锁控制，写前/写后检查不能消除所有竞争窗口。
服务器异常中断后的恢复走同一个 reconcile，不调用 save_under_claim 重做。
无变化快速路径在生产入口中先完成，不在本片段重复。

## 5. 段落对齐和字符差异

精细差异有界，保留 Unicode code point 坐标。下面是核心对齐示例；
生产还需基于唯一删除/插入段匹配移动，保留重复段消歧、拆并关系及 coarse 状态。

```python
from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass(frozen=True)
class Paragraph:
    start: int
    end: int
    text: str


def paragraphs(text: str) -> list[Paragraph]:
    return [
        Paragraph(m.start(), m.end(), m.group())
        for m in re.finditer(r"\S.*?(?=\n[ \t]*\n|\Z)", text, flags=re.S)
    ]


def align(before: str, after: str) -> list[dict[str, object]]:
    left, right = paragraphs(before), paragraphs(after)
    if len(left) + len(right) > 2000:
        return [{"kind": "coarse", "before": before, "after": after}]
    matcher = SequenceMatcher(None, [p.text for p in left], [p.text for p in right],
                              autojunk=False)
    return [
        {"kind": tag, "before": left[i:j], "after": right[k:l]}
        for tag, i, j, k, l in matcher.get_opcodes()
        if tag != "equal"
    ]


def inline(before: str, after: str) -> list[tuple[str, str, str]]:
    if max(len(before), len(after)) > 4000:
        return [("coarse", before, after)]
    return [
        (tag, before[i:j], after[k:l])
        for tag, i, j, k, l in SequenceMatcher(
            None, before, after, autojunk=False
        ).get_opcodes()
    ]
```

SequenceMatcher 的最坏开销仍需在 D1 量测，生产增加全文字符预算和总耗时预算，
不能把每段各未超限当作整章总成本可接受。差异异步生成，不阻塞文件保存。

## 6. 同处修改的证据沿革

组的身份来自文档和对齐沿革，不能仅用相同句子 hash；无法确定关联时先需核对。

```python
from dataclasses import dataclass, replace
from typing import Literal


@dataclass(frozen=True)
class EvidenceGroup:
    group_id: str
    baseline_hash: str
    latest_hash: str
    status: Literal["valid", "no_net_change", "needs_review"]
    independent_support: int


def advance(group: EvidenceGroup, before_hash: str, after_hash: str) -> EvidenceGroup:
    if before_hash != group.latest_hash:
        return replace(group, status="needs_review", independent_support=0)
    changed = after_hash != group.baseline_hash
    return replace(
        group,
        latest_hash=after_hash,
        status="valid" if changed else "no_net_change",
        independent_support=int(changed),
    )
```

三次保存仍至多一组独立支持，改回起点为零；外部或模型变化不能借这段函数变成人工来源。
归并任务只分析组的当前净变化，不能让中间候选继续贡献额外支持。

## 7. 只读分析输出与证据核对

分类和规则分开，模型不能返回 active 状态。程序核对引文，作者与在线评测判断理解是否成立。

```python
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rule: str = Field(min_length=1, max_length=220)
    conditions: list[str] = Field(max_length=5)
    exceptions: list[str] = Field(max_length=5)
    citations: list[Citation] = Field(min_length=1, max_length=8)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    classification: Literal["expression", "correction", "plot", "format", "uncertain"]
    candidates: list[Candidate] = Field(max_length=3)
    insufficient_context: bool = False


def validate_analysis(raw: str, sources: dict[str, str]) -> Analysis:
    output = Analysis.model_validate_json(raw)
    if output.classification != "expression" and output.candidates:
        raise ValueError("NON_STYLE_CANDIDATE")
    for candidate in output.candidates:
        for cite in candidate.citations:
            source = sources.get(cite.source_id)
            if source is None or not 0 <= cite.start < cite.end <= len(source):
                raise ValueError("EVIDENCE_RANGE_INVALID")
            if source[cite.start:cite.end] != cite.quote:
                raise ValueError("EVIDENCE_QUOTE_INVALID")
    return output
```

生产按片段分别分类；一个批次可同时包含事实和表达修改，只把后者的引用交给候选。
sources 必须来自冻结包的精确版本和可用区间，不能用当前磁盘内容替代。
人物范围由已知引用和作者决策确定，不让模型提供任意跨作品 ID。

## 8. 可取消的后台执行

下面展示领取与发布边界。实际 dispatcher 负责前台优先与事件取消；
analyze 适配器复用 RecordedProvider、固定模型和真实 request_id，内部不持数据库事务。

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Lease:
    job_id: str
    fence: int
    source_epoch: int
    generation: int


class JobStore(Protocol):
    def claim(self) -> Lease | None: ...
    def defer(self, lease: Lease, reason: str) -> None: ...
    def was_sent(self, lease: Lease) -> bool: ...
    def publish_if_current(self, lease: Lease, value: dict[str, object]) -> bool: ...
    def interrupt(self, lease: Lease, sent: bool) -> None: ...
    def fail(self, lease: Lease, reason: str) -> None: ...


async def run_one(
    store: JobStore,
    foreground_pending: Callable[[], bool],
    cancel: asyncio.Event,
    analyze: Callable[[Lease, asyncio.Event], Awaitable[dict[str, object]]],
) -> bool:
    if foreground_pending():
        return False
    lease = store.claim()
    if lease is None:
        return False
    if foreground_pending() or cancel.is_set():
        store.defer(lease, "foreground")
        return False
    try:
        async with asyncio.timeout(120):
            value = await analyze(lease, cancel)
        if cancel.is_set():
            store.interrupt(lease, sent=store.was_sent(lease))
            return False
        # 事务中核对 fence、取消、generation、证据有效性和预算占用。
        return store.publish_if_current(lease, value)
    except asyncio.CancelledError:
        store.interrupt(lease, sent=store.was_sent(lease))
        raise
    except Exception as error:
        store.fail(lease, type(error).__name__)
        return False
```

dispatcher 在前台请求受理时设置 cancel 并停止新的后台发出；
发布时再次检查持久取消状态，避免检查与取消之间的竞争。
实际发送位置登记 was_sent，不能把拿到租约等同已调用模型。
running 重启若已发送则 interrupted，需显式 retry；未发送可退回 pending。
清理模型快照用 finally/aclose，实际用量即使停止也要留下，不依赖输入附件。

## 9. 决策版本与忘记

同一事务中进行条件更新、epoch、排除和 outbox。外部清理稍后执行，但排除先立即生效。
示例聚焦状态转换；生产保存完整确认快照、历史版本和人物/章界校验。

```python
import sqlite3
import json
from uuid import uuid4


TRANSITIONS = {
    "confirm": {"pending": "active"},
    "revise": {"active": "active", "paused": "paused"},
    "dismiss": {"pending": "dismissed"},
    "pause": {"active": "paused"},
    "resume": {"paused": "active"},
    "forget": {s: "forgotten" for s in ("pending", "active", "paused", "dismissed", "superseded")},
}


def apply_decision(
    con: sqlite3.Connection,
    preference_id: str,
    expected_revision: int,
    action: str,
    rule: str | None = None,
) -> dict[str, object]:
    row = con.execute(
        "SELECT revision, status, rule FROM preferences WHERE preference_id=?",
        (preference_id,),
    ).fetchone()
    if row is None:
        raise ValueError("PREFERENCE_NOT_FOUND")
    if row["revision"] != expected_revision:
        raise ValueError("PREFERENCE_REVISION_CONFLICT")
    next_status = TRANSITIONS.get(action, {}).get(row["status"])
    if next_status is None:
        raise ValueError("PREFERENCE_TRANSITION_INVALID")
    next_rule = rule if rule is not None else row["rule"]
    if action in ("confirm", "revise") and not str(next_rule or "").strip():
        raise ValueError("PREFERENCE_RULE_EMPTY")
    if action == "forget":
        con.execute(
            "INSERT OR IGNORE INTO exclusions(group_id) "
            "SELECT group_id FROM preference_evidence WHERE preference_id=?",
            (preference_id,),
        )
        next_rule = None
        con.execute("DELETE FROM preference_versions WHERE preference_id=?", (preference_id,))
    changed = con.execute(
        "UPDATE preferences SET revision=revision+1, status=?, rule=? "
        "WHERE preference_id=? AND revision=?",
        (next_status, next_rule, preference_id, expected_revision),
    )
    if changed.rowcount != 1:
        raise ValueError("PREFERENCE_REVISION_CONFLICT")
    con.execute("UPDATE meta SET preference_epoch=preference_epoch+1")
    decision_id = "decision_" + uuid4().hex
    receipt = {"decision_id": decision_id, "preference_id": preference_id,
               "revision": expected_revision + 1, "status_at_commit": next_status}
    con.execute(
        "INSERT INTO outbox(kind, payload) VALUES(?, ?)",
        ("preference_cleanup" if action == "forget" else "preference_changed",
         json.dumps(receipt)),
    )
    return receipt
```

生产在 transaction 内使用 once 包装 apply_decision。
遗忘后重放旧确认只返回无正文的原决策回执，详情按当前忘记状态脱敏，不能从请求缓存复活旧规则。
revise 已停用规则保持停用，避免修改文字时无意重新启用。

## 10. 按范围选择及预算

条件标签来自任务明确信息或已登记范围，不让模型以相似人名证明身份。
语义相关性可以帮助排序，但不能越过状态、作品、人物和章界这些确定性过滤。

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    revision: int
    workspace_uid: str
    text: str
    chapter_from: int
    character_ref: str | None = None
    required_tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Target:
    workspace_uid: str
    purpose: str
    chapter: int | None
    characters: frozenset[str]
    tags: frozenset[str]


def select_rules(
    frozen: tuple[Rule, ...],
    currently_active: set[tuple[str, int]],
    target: Target,
    budget: int,
) -> tuple[list[Rule], list[tuple[str, str]]]:
    selected: list[Rule] = []
    excluded: list[tuple[str, str]] = []
    used = len("写作偏好\n")
    for rule in frozen:
        reason = ""
        if target.purpose != "writer":
            reason = "purpose"
        elif rule.workspace_uid != target.workspace_uid:
            reason = "workspace"
        elif (rule.id, rule.revision) not in currently_active:
            reason = "revoked"
        elif target.chapter is None or target.chapter < rule.chapter_from:
            reason = "chapter"
        elif rule.character_ref and rule.character_ref not in target.characters:
            reason = "character"
        elif not rule.required_tags <= target.tags:
            reason = "condition"
        block = "- " + rule.text + "\n"
        if not reason and used + len(block) > budget:
            reason = "budget"
        if reason:
            excluded.append((rule.id, reason))
        else:
            selected.append(rule)
            used += len(block)
    return selected, excluded
```

生产扩展 chapter_to、场景锚点、声明优先级、有效证据、冲突和示例过滤。
排序在选择前固定，超限整条排除，不截断规则。这里的字符预算仅是分区上限，
真实请求仍通过 0.9 的完整输入/token 预算。后文示例另行过滤，不能因规则通过就一并装入。

## 11. 请求编译与回执同源

现有 harness 的 system 不能被后台直接改写。由应用层请求编译器替换自己拥有的分区，
再交给 0.9 的预算与压缩处理；不能仅在任务开始注入一次便声称之后的实际使用正确。

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Partition:
    key: str
    text: str
    refs: tuple[tuple[str, int], ...] = ()


def compile_preferences(
    existing: tuple[Partition, ...],
    selected: tuple[tuple[str, int, str], ...],
) -> tuple[Partition, ...]:
    kept = tuple(p for p in existing if p.key != "writing_preferences")
    if not selected:
        return kept
    text = "写作偏好（适用于正文；当前任务、事实与范围要求优先）\n"
    text += "".join("- " + body + "\n" for _, _, body in selected)
    return kept + (Partition(
        key="writing_preferences",
        text=text,
        refs=tuple((identity, revision) for identity, revision, _ in selected),
    ),)
```

同一编译结果同时产生 provider 输入和 request_id 回执；重复编译只保留一个偏好分区。
忘记相关来源段和旧摘要由有效上下文投影排除，而不是只删除这个新分区。
目标后来确定时可在受理 allowlist 内重选，每次发送前重新检查撤销状态。

## 12. 路由输入

输入只暴露作者操作需要的字段，来源、快照、可生效状态由服务器决定。
错误转为现有 WorkspaceError 信封，不能把 Python ValueError 原样泄漏给界面。

```python
from pydantic import BaseModel, ConfigDict, Field


class SaveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    expected_digest: str = Field(min_length=1)
    text: str
    client_sequence: int = Field(ge=0)
    request_key: str = Field(min_length=1, max_length=128)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_draft_revision: int = Field(ge=0)
    client_sequence: int = Field(ge=0)
    text: str
    request_key: str = Field(min_length=1, max_length=128)
```

路由首先根据当前 workspace_id 找到绑定与服务，再校验 session 属于其稳定 UID。
响应不返回主机凭据，路径仍由 Workspace 受管规则核对。

## 13. 草稿自动保存

正文草稿存在服务端，避免桌面动态端口带来的 browser origin 变化。
输入合并，但已发出且响应未知的请求保留原请求键与载荷，先核对原请求再推进新文本。

```typescript
type Snapshot = { text: string; sequence: number };
type DraftRequest = Snapshot & { expectedRevision: number; requestKey: string };
type DraftAck = { revision: number; sequence: number };
type Persist = (request: DraftRequest) => Promise<DraftAck>;

export class DraftAutosaver {
  private pending: Snapshot | null = null;
  private attempt: DraftRequest | null = null;
  private running: Promise<void> | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private revision = 0;
  public persistedSequence = 0;

  constructor(
    private persist: Persist,
    private onError: (error: unknown) => void,
  ) {}

  enqueue(snapshot: Snapshot): void {
    this.pending = snapshot;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.flush().catch(this.onError); }, 300);
  }

  flush(): Promise<void> {
    if (this.running) return this.running;
    this.running = this.drain().finally(() => { this.running = null; });
    return this.running;
  }

  private async drain(): Promise<void> {
    while (this.attempt || this.pending) {
      if (!this.attempt) {
        const next = this.pending!;
        this.pending = null;
        this.attempt = { ...next, expectedRevision: this.revision,
          requestKey: crypto.randomUUID() };
      }
      const attempt = this.attempt;
      // persist 的未知结果恢复必须重放同键；不能暗中换 expectedRevision 后覆盖。
      const ack = await this.persist(attempt);
      if (ack.sequence !== attempt.sequence) throw new Error("DRAFT_ACK_MISMATCH");
      this.revision = ack.revision;
      this.persistedSequence = Math.max(this.persistedSequence, ack.sequence);
      this.attempt = null;
    }
  }
}
```

生产增加连续输入 2s 的最迟持久化、初始化 revision、停用/关闭清理和已持久化状态通知。
失败时 attempt 保留，用户重试继续同键；服务端冲突需查询并保留副本，不自动提高期望版本。
定时器异常必须消费，不能造成未处理 Promise rejection。

## 14. 保存响应不能清掉新输入

归属与请求键同时匹配才消费成功响应，较高 sequence 的输入继续保留。

```typescript
type Submitted = { key: string; sequence: number; text: string };
type Editor = {
  identity: string;
  sequence: number;
  text: string;
  baseText: string;
  baseDigest: string;
  submitted: Submitted | null;
};
type SaveAck = {
  identity: string;
  requestKey: string;
  clientSequence: number;
  digest: string;
};

export function applySaved(current: Editor, ack: SaveAck): Editor {
  const sent = current.submitted;
  if (!sent || ack.identity !== current.identity || ack.requestKey !== sent.key
      || ack.clientSequence !== sent.sequence) return current;
  return {
    ...current,
    baseText: sent.text,
    baseDigest: ack.digest,
    text: current.sequence > sent.sequence ? current.text : sent.text,
    submitted: null,
  };
}
```

ack.identity 是客户端请求归属与服务端 UID/session 共同核对后的键，不能取界面当前项目临时拼接。
file.saved 但 recording.unrecorded 同样推进文件基线，另外保留补记状态。
not_saved 不调用 applySaved；recovery_required 不伪装正常全部成功。

## 15. 编辑组件的可见状态

组件消费领域状态，不自行推测「保存完成」或把分析失败写成正文失败。
生产使用已有 Button、错误信封和字号体系；下面只展示数据与动作关系。

```tsx
import { useId } from "react";

type Props = {
  text: string;
  draftPersisted: boolean;
  fileSaved: boolean;
  recording: "committed" | "unrecorded" | "no_change";
  saving: boolean;
  onText: (text: string) => void;
  onSave: () => void;
  onRecord: () => void;
};

export function ManuscriptEditor(props: Props) {
  const inputId = useId();
  return <section aria-label="正文编辑">
    <label htmlFor={inputId}>正文</label>
    <textarea id={inputId} value={props.text}
      onChange={event => props.onText(event.target.value)} />
    <p role="status">{props.draftPersisted ? "草稿已保存" : "草稿尚未持久化"}</p>
    <button type="button" onClick={props.onSave} disabled={props.saving}>
      {props.saving ? "保存中…" : "保存正文"}
    </button>
    {props.fileSaved && props.recording === "unrecorded" &&
      <div role="status">正文已保存，版本尚未记录。
        <button type="button" onClick={props.onRecord}>补记版本</button>
      </div>}
  </section>;
}
```

偏好面板复用同样的受控模式：value 绑定精确 preference_id/revision，
决定提交后保留新输入，冲突不清空；不能从后台候选变更自动调用确认。

## 16. 评测接线

拟扩展 step action，保留旧案例缺省为 writer。新增手工编辑通过生产编辑服务执行；
不能把 external_updates 写进文件后标记成人工来源。以下为新 schema 的目标写法，当前尚不能运行：

```yaml
id: revision_emotion
steps:
  - action: manual_edit
    path: 正文/0007 暗潮.md
    replace:
      before: 林岚很紧张，她害怕许砚继续追问。
      after: 林岚把杯沿转了半圈。「你刚才说到哪儿了？」
  - action: analyze_revisions
    expected_terminal: completed
  - action: preference_decision
    select_by_source: latest_edit
    decision: confirm
  - action: writer
    text: 续写许砚拿出收据之后的对话，保留收据上的日期。
    selected: 正文/0007 暗潮.md
    checks:
      - id: preference_applied
        kind: manual
        rubric: 新对白采用已确认的条件化表达，未改变人物事实或提前披露。
```

rubric、期望和判定只由评测器读取，不进入模型证据包或提示。
三个变体为 no_preferences / rules_only / rules_with_examples，
必须在 schema、runtime 和报告中显式登记，并保留每个实际请求的选择回执。

## 17. 本轮检查结果

2026-10-26 本轮实际完成：

- 7 份新增计划文档的本地链接、代码围栏、编码和格式检查通过。
- 12 段 Python 示例语法和定义加载通过；另检查原始指纹、并发 claim、幂等和回滚、
  中文差异、证据沿革、伪造引文拒绝、停用规则修改、忘记、章界/撤销过滤和来源字段拒绝。
- 2 段 TypeScript 和 1 段 TSX 示例在项目现有类型依赖下通过 strict、noEmit 检查。
- JSON 与 YAML 示意内容可解析；YAML 新评测 schema 尚未接入生产。

这些结果仅证明设计示例的上述检查，不包含生产集成、完整静态类型检查、
浏览器业务、桌面或在线语义验收，不能作为功能交付证据。

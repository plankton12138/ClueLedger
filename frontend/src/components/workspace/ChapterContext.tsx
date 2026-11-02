/**
 * 正文页的「本章相关」面板。
 *
 * 首屏只发一次聚合请求；后端已经按实际出现、明确计划和失效引用分组，前端不再按标题或
 * 关键词猜关联。切章时中止旧请求，旧响应不能覆盖新章节。
 */

import { useEffect, useState } from "react";
import { AlertCircle, MessageSquare, RefreshCw, X } from "lucide-react";

import { readChapterContext, type ChapterContextItem, type ChapterContextView } from "../../api/story";
import { errorMessage } from "../../api/errors";
import { useStoryStore } from "../../state/story";
import { Button } from "../ui/Button";

const PLACEMENT_LABEL = {
  observed: "正文出现",
  planned: "本章安排",
  unresolved: "引用失效",
} as const;

const STATE_LABEL: Record<string, string> = {
  exact: "可定位",
  relocated: "已重新定位",
  missing: "原句已不在正文",
  ambiguous: "原句有多处匹配",
  unavailable: "正文暂不可读",
};

function ItemRow({
  item,
  onSelect,
}: {
  item: ChapterContextItem;
  onSelect: (item: ChapterContextItem) => void;
}) {
  return (
    <button
      type="button"
      className="chapter-context-item"
      aria-label={`${item.title}，${PLACEMENT_LABEL[item.placement]}`}
      onClick={() => onSelect(item)}
    >
      <span className="chapter-context-item-head">
        <span className="chapter-context-kind">{item.object.kind === "node" ? "构思" : "线索"}</span>
        <span className="chapter-context-title">{item.title}</span>
      </span>
      {item.occurrences.length > 0 ? (
        <span className="chapter-context-quotes">
          {item.occurrences.map((row, index) => (
            <span key={`${row.quote}:${index}`} className="chapter-context-quote">
              <span className="truncate">{row.quote}</span>
              {row.state !== "exact" && (
                <span className={`chapter-context-state ${row.state}`}>
                  {STATE_LABEL[row.state] ?? row.state}
                </span>
              )}
            </span>
          ))}
        </span>
      ) : (
        <span className="chapter-context-hint">还没有正文出处，仅按本章计划列出。</span>
      )}
    </button>
  );
}

export default function ChapterContext({
  workspaceId,
  chapterPath,
  variantId,
  onSelectNode,
  onSelectClue,
  onDiscussChapter,
  onClose,
}: {
  workspaceId: string;
  chapterPath: string;
  variantId: string | null;
  onSelectNode: (nodeId: string) => void;
  onSelectClue: (clueId: string) => void;
  onDiscussChapter: () => void;
  onClose: () => void;
}) {
  const revision = useStoryStore(state => `${state.graph?.revision ?? ""}:${state.invalidation}`);
  const [view, setView] = useState<ChapterContextView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setView(null);
    setError(null);
    setLoading(true);
    readChapterContext(workspaceId, chapterPath, {
      variantId,
      signal: controller.signal,
    })
      .then(next => {
        if (active) setView(next);
      })
      .catch(cause => {
        if (active && (cause as { name?: string }).name !== "AbortError") {
          setError(errorMessage(cause, "读取本章相关"));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [workspaceId, chapterPath, variantId, revision, epoch]);

  async function loadMore() {
    if (!view?.next_cursor || loading) return;
    setLoading(true);
    setError(null);
    try {
      const next = await readChapterContext(workspaceId, chapterPath, {
        variantId,
        cursor: view.next_cursor,
      });
      setView(current => (current
        ? { ...next, items: [...current.items, ...next.items] }
        : next));
    } catch (cause) {
      setError(errorMessage(cause, "继续读取本章相关"));
    } finally {
      setLoading(false);
    }
  }

  function select(item: ChapterContextItem) {
    if (item.object.kind === "node") onSelectNode(item.object.id);
    else onSelectClue(item.object.id);
  }

  return (
    <div className="chapter-context" aria-label="本章相关">
      <header className="workspace-detail-head">
        <div>
          <h2>本章相关</h2>
          <p className="workspace-detail-status">只列本章有效关联、明确计划和失效引用。</p>
        </div>
        <div className="flex items-center gap-1">
          <Button size="compact" variant="quiet" onClick={onDiscussChapter}>
            <MessageSquare size={15} aria-hidden="true" />讨论本章
          </Button>
          <Button size="compact" aria-label="关闭本章相关" onClick={onClose}>
            <X size={15} aria-hidden="true" />
          </Button>
        </div>
      </header>

      {error && (
        <div className="workspace-detail-error" role="alert">
          <AlertCircle size={14} aria-hidden="true" />
          <span>{error}</span>
          <Button size="compact" variant="quiet" onClick={() => setEpoch(value => value + 1)}>
            <RefreshCw size={13} aria-hidden="true" />重试
          </Button>
        </div>
      )}

      {!view && loading && <p className="workspace-detail-hint">正在读取本章相关…</p>}
      {!view && !loading && !error && (
        <p className="workspace-detail-hint">本章还没有已登记的构思或线索。</p>
      )}

      {view && (
        <div className="chapter-context-groups">
          {(["observed", "planned", "unresolved"] as const).map(placement => {
            const rows = view.items.filter(item => item.placement === placement);
            if (rows.length === 0) return null;
            return (
              <section key={placement} className="chapter-context-group" aria-label={PLACEMENT_LABEL[placement]}>
                <h3>{PLACEMENT_LABEL[placement]}</h3>
                {rows.map(item => (
                  <ItemRow key={`${item.object.kind}:${item.object.id}`} item={item} onSelect={select} />
                ))}
              </section>
            );
          })}
          {view.items.length === 0 && (
            <p className="workspace-detail-hint">
              本章还没有已登记的构思或线索。可以在对话里说「讨论本章」，或在正文里选段后讨论。
            </p>
          )}
          {view.next_cursor && (
            <Button size="compact" disabled={loading} onClick={() => void loadMore()}>
              加载更多
            </Button>
          )}
          {view.truncated && <p className="workspace-detail-hint">已分页显示，未一次列出全部条目。</p>}
        </div>
      )}
    </div>
  );
}

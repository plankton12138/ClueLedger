/**
 * 顶部全局搜索（P6 T11，计划 D9）：防抖 200ms + 前端本地索引 + 分组下拉 + 跨面板跳转。
 *
 * 索引复用既有 REST 数据（章节标题+正文、角色/线索/伏笔账本），无新端点；
 * 排序/摘要等纯逻辑在 state/search.ts（可测），本组件只做调度与呈现。
 * 点击结果经 store 的 nav 请求跳转：章节 → 创作台定位该章；账本 → 切对应
 * Tab + 高亮目标行（ManuscriptPanel / BiblePanel 各自消费后 clearNav）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchBible, fetchChapter, fetchChapters } from "../api/rest";
import {
  buildChapterDoc,
  buildLedgerDocs,
  searchIndex,
  SEARCH_GROUP_LABELS,
  SEARCH_GROUP_ORDER,
  type SearchDoc,
  type SearchHit,
  type SearchKind,
} from "../state/search";
import { useChatStore } from "../state/store";

interface SearchIndex {
  slug: string;
  docs: SearchDoc[];
}

/** 命中分组展示顺序的条目渲染（组头 + 组内按钮列表）。 */
function HitGroup({ kind, hits, onJump }: { kind: SearchKind; hits: SearchHit[]; onJump: (h: SearchHit) => void }) {
  return (
    <div className="border-t border-line/50 first:border-t-0">
      <div className="px-3 pt-1.5 pb-0.5 text-[10px] tracking-widest text-gold-soft">
        {SEARCH_GROUP_LABELS[kind]}
      </div>
      {hits.map((hit, i) => (
        <button
          key={`${hit.doc.name}-${i}`}
          type="button"
          onClick={() => onJump(hit)}
          className="block w-full px-3 py-1.5 text-left transition-colors hover:bg-gold/10"
        >
          <span className="text-xs text-paper">{hit.doc.name}</span>
          {hit.snippet !== "" && (
            <span className="ml-2 text-[10px] text-paper/45">{hit.snippet}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export default function SearchBox() {
  const slug = useChatStore((s) => s.slug);
  const navigate = useChatStore((s) => s.navigate);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState<SearchIndex | null>(null);
  const [indexState, setIndexState] = useState<"idle" | "loading" | "error">("idle");
  const [indexError, setIndexError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 索引构建（懒触发：首次聚焦时）：章节列表 + 各章正文 + 三类账本，Promise.all 并行。
  // 失败（账本损坏 422 / IO）→ 错误文案进下拉（三要素透传），不阻塞输入框继续打字。
  const buildIndex = useCallback(async (workspaceSlug: string) => {
    setIndexState("loading");
    setIndexError(null);
    try {
      const [chapterIds, characterBody, clueBody, foreshadowBody] = await Promise.all([
        fetchChapters(workspaceSlug),
        fetchBible(workspaceSlug, "character"),
        fetchBible(workspaceSlug, "clue"),
        fetchBible(workspaceSlug, "foreshadow"),
      ]);
      const chapters = await Promise.all(
        chapterIds.map((id) => fetchChapter(workspaceSlug, id)),
      );
      const docs: SearchDoc[] = [
        ...chapters.map((ch, i) => buildChapterDoc(chapterIds[i] as string, ch.content)),
        ...buildLedgerDocs("character", characterBody.entities),
        ...buildLedgerDocs("clue", clueBody.entities),
        ...buildLedgerDocs("foreshadow", foreshadowBody.entities),
      ];
      setIndex({ slug: workspaceSlug, docs });
      setIndexState("idle");
    } catch (exc) {
      setIndexState("error");
      setIndexError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  // 工作区切换 → 清空查询与索引（索引只属于当前 slug），下一次聚焦重建。
  useEffect(() => {
    setQuery("");
    setDebounced("");
    setOpen(false);
    setIndex(null);
    setIndexState("idle");
    setIndexError(null);
  }, [slug]);

  // 防抖（D9：200ms）：输入停顿后才算一次检索。
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => setDebounced(query.trim()), 200);
    return () => window.clearTimeout(timer);
  }, [query, open]);

  // 聚焦：展开下拉；索引缺失/过期（换过工作区）时重建。
  function handleFocus(): void {
    setOpen(true);
    if (slug !== null && (index === null || index.slug !== slug)) {
      void buildIndex(slug);
    }
  }

  // 点击下拉外 → 收起（mousedown 先于 blur，用 contains 判定）。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current !== null && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // 跳转：章节 → studio 定位章节；账本 → studio 切对应 Tab + 高亮行。跳完收起并清空。
  function jump(hit: SearchHit): void {
    const doc = hit.doc;
    if (doc.kind === "chapter" && doc.chapterId !== undefined) {
      navigate({ kind: "chapter", chapterId: doc.chapterId });
    } else {
      navigate({ kind: "bible", type: doc.kind as "character" | "clue" | "foreshadow", name: doc.name });
    }
    setOpen(false);
    setQuery("");
    setDebounced("");
  }

  const hits = index !== null && index.slug === slug ? searchIndex(index.docs, debounced) : [];
  const showDropdown = open && debounced !== "";

  return (
    <div ref={rootRef} className="relative w-52 min-w-0">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={handleFocus}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && hits.length > 0) jump(hits[0] as SearchHit);
        }}
        disabled={slug === null}
        placeholder={slug === null ? "选择案卷后搜索" : "搜索章节、角色、线索、伏笔…"}
        aria-label="全局搜索"
        className="w-full border border-line bg-transparent px-2 py-1 text-xs text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none disabled:opacity-40"
      />
      {showDropdown && (
        <div className="absolute top-full left-0 z-50 mt-1 max-h-80 w-[22rem] overflow-auto border border-line bg-ink shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
          {indexState === "loading" ? (
            <p className="px-3 py-2 text-xs text-paper/40">索引构建中…</p>
          ) : indexState === "error" ? (
            <p className="px-3 py-2 text-xs leading-relaxed text-overdue">{indexError}</p>
          ) : hits.length === 0 ? (
            <p className="px-3 py-2 text-xs text-paper/40">无匹配结果</p>
          ) : (
            SEARCH_GROUP_ORDER.map((kind) => {
              const group = hits.filter((h) => h.doc.kind === kind);
              return group.length === 0 ? null : (
                <HitGroup key={kind} kind={kind} hits={group} onJump={jump} />
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

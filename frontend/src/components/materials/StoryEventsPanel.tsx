/**
 * 章节与事件（0.8 E）。
 *
 * 两种顺序读同一份 `story-index.json` 投影：章节顺序按章与原文区间，故事顺序只对
 * 能证明的先后排序。不连通的事件、未知时间与冲突分别显示，不用章号伪造故事顺序；
 * 旧版章节概览与时间锚点只读兼容，不再单独维护第三份时间线正文。
 */

import { useEffect, useRef, useLayoutEffect, useState } from "react";

import {
  fetchStoryEvents,
  type StoryChapterRow,
  type StoryEvent,
  type StoryEvents,
  type StoryOrder,
  type StoryPlan,
} from "../../api/storyMaterials";
import { errorMessage } from "../../api/errors";
import { Button } from "../ui/Button";
import { usePagePositions } from "../../state/pagePositions";
import { COVERAGE_LABEL, COVERAGE_TONE } from "./labels";

const STATUS_LABEL: Record<string, string> = {
  valid: "当前有效",
  stale: "依据已改动",
  missing: "还没整理",
};

const STATUS_TONE: Record<string, string> = {
  valid: "text-green",
  stale: "text-amber",
  missing: "text-mute",
};

const SOURCE_LABEL: Record<string, string> = {
  story_index: "",
  legacy: "旧版概览",
  none: "",
};

interface Props {
  workspaceId: string;
  order: StoryOrder;
  chapter: number | null;
  /** 资料任务结束或作者手动刷新时递增；只影响重新读取，不改变当前筛选。 */
  refreshKey?: number;
  onOrderChange: (order: StoryOrder) => void;
  onOpenSource: (path: string) => void;
  onReload: () => void;
}

export default function StoryEventsPanel({
  workspaceId,
  order,
  chapter,
  refreshKey = 0,
  onOrderChange,
  onOpenSource,
  onReload,
}: Props) {
  const [data, setData] = useState<StoryEvents | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dataKey, setDataKey] = useState("");
  const currentKey = `${workspaceId}:${order}:${chapter ?? ""}`;
  const scroll = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { if (scroll.current) scroll.current.scrollTop = usePagePositions.getState().offsets[currentKey] ?? 0; }, [currentKey, data]);
  const [hiddenPlans, setHiddenPlans] = useState<string[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void fetchStoryEvents(workspaceId, order, {
      asOfChapter: chapter,
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) { setData(result); setDataKey(currentKey); }
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        setError(describe(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  return () => controller.abort();
  }, [workspaceId, order, chapter, refreshKey]);

  const visibleData = dataKey === currentKey ? data : null;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex flex-none items-center gap-1 border-b border-panel-edge px-3 py-1.5">
        <div className="story-order-control">
          <Button
            size="compact" variant={order === "chapter" ? "primary" : "secondary"}
            aria-pressed={order === "chapter"}
            onClick={() => onOrderChange("chapter")}
          >
            按章节
          </Button>
          <Button
            size="compact" variant={order === "story" ? "primary" : "secondary"}
            aria-pressed={order === "story"}
            onClick={() => onOrderChange("story")}
          >
            按故事时间
          </Button>
        </div>

      </div>

      {visibleData && visibleData.plan_modules.length > 0 && (
        <div className="flex flex-none flex-wrap items-center gap-1 border-b border-panel-edge px-3 py-1 text-xs">
          <span className="text-mute">计划叠加：</span>
          {visibleData.plan_modules.map((module) => {
            const hidden = hiddenPlans.includes(module.module_id);
            return (
              <Button
                key={module.module_id}
                className={`rounded border px-1.5 py-0.5 ${
                  hidden
                    ? "border-panel-edge text-mute"
                    : "border-amber-edge bg-amber-bg text-amber"
                }`}
                aria-pressed={!hidden}
                onClick={() =>
                  setHiddenPlans((current) =>
                    hidden
                      ? current.filter((item) => item !== module.module_id)
                      : [...current, module.module_id],
                  )
                }
              >
                {module.name || module.module_id}
              </Button>
            );
          })}
        </div>
      )}

      {error && (
        <div className="flex flex-none items-center gap-2 border-b border-red-edge bg-red-bg px-3 py-1.5 text-sm text-red">
          <span className="min-w-0 flex-1">{error}</span>
          <Button className="underline" onClick={onReload}>
            重试
          </Button>
        </div>
      )}

      <div ref={scroll} className="min-h-0 flex-1 overflow-auto" onScroll={event => { if (event.currentTarget.clientHeight && visibleData) usePagePositions.getState().offset(currentKey, event.currentTarget.scrollTop); }}>
        {loading && !visibleData && <p className="px-3 py-2 text-sm text-mute">正在读取事件…</p>}
        {visibleData?.warnings.length ? (
          <ul className="space-y-1 border-b border-row-edge px-3 py-2 text-xs text-amber">
            {visibleData.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        ) : null}
        {visibleData && (
          <PlanList
            plans={visibleData.plans.filter((plan) => !hiddenPlans.includes(plan.module_id))}
          />
        )}
        {visibleData &&
          (order === "chapter" ? (
            <ChapterList rows={visibleData.chapters} onOpenSource={onOpenSource} />
          ) : (
            <StoryList data={visibleData} onOpenSource={onOpenSource} />
          ))}
      </div>
    </div>
  );
}

function ChapterList({
  rows,
  onOpenSource,
}: {
  rows: StoryChapterRow[];
  onOpenSource: (path: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="px-3 py-2 text-sm text-mute">这个时点下还没有章节。</p>;
  }
  return (
    <div>
      {rows.map((row) => (
        <section key={row.path} className="border-b border-row-edge">
          <header className="flex flex-wrap items-baseline gap-2 px-3 py-1.5">
            <h3 className="text-sm font-bold">
              第 {row.chapter} 章｜{row.title}
            </h3>
            <span className={`text-xs ${STATUS_TONE[row.status] ?? "text-mute"}`}>
              {STATUS_LABEL[row.status] ?? row.status}
            </span>
            {row.coverage && (
              <span className={`text-xs ${COVERAGE_TONE[row.coverage.outcome]}`}>
                {COVERAGE_LABEL[row.coverage.outcome]}
              </span>
            )}
            {SOURCE_LABEL[row.source] && (
              <span className="text-xs text-mute">{SOURCE_LABEL[row.source]}</span>
            )}
            <div className="flex-1" />
            <Button
              className="text-xs text-sub underline"
              onClick={() => onOpenSource(row.path)}
            >
              打开原文
            </Button>
          </header>
          {row.summary && (
            <p className="px-3 pb-1 text-sm leading-5 whitespace-pre-wrap">{row.summary}</p>
          )}
          {row.anchor && (
            <p className="px-3 pb-1 text-sm leading-5 text-sub">
              时间：{row.anchor.when || "未知"}
              {row.anchor.span ? ` · 跨度 ${row.anchor.span}` : ""}
              {row.anchor.location ? ` · ${row.anchor.location}` : ""}
              <span className="ml-1 text-xs text-mute">（旧版时间锚点）</span>
            </p>
          )}
          {row.events.length > 0 && (
            <ul className="border-t border-row-edge">
              {row.events.map((event) => (
                <EventRow key={event.id} event={event} onOpenSource={onOpenSource} />
              ))}
            </ul>
          )}
          {row.legacy_entries && (
            <LegacyEntries entries={row.legacy_entries} />
          )}
        </section>
      ))}
    </div>
  );
}

function LegacyEntries({
  entries,
}: {
  entries: { events: string[]; characters: string[]; places: string[]; clues: string[] };
}) {
  const groups = [
    ["事件", entries.events],
    ["人物", entries.characters],
    ["时间地点", entries.places],
    ["线索", entries.clues],
  ] as const;
  return (
    <div className="px-3 pb-2">
      {groups.map(([label, rows]) =>
        rows.length ? (
          <div key={label} className="mt-1">
            <h4 className="text-xs font-bold text-sub">{label}</h4>
            <ul className="list-disc pl-4 text-sm leading-5">
              {rows.map((row, index) => (
                <li key={index}>{row}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  );
}

function StoryList({
  data,
  onOpenSource,
}: {
  data: StoryEvents;
  onOpenSource: (path: string) => void;
}) {
  const empty = data.groups.length === 0 && data.unknown.length === 0;
  if (empty) {
    return (
      <p className="px-3 py-2 text-sm text-mute">
        还没有带时间关系的事件。按章节整理后，这里只展示能证明的先后。
      </p>
    );
  }
  return (
    <div>
      {data.groups.map((group, index) => (
        <section key={index} className="border-b border-row-edge">
          <h3 className="toc-head px-3 py-1 text-xs font-bold tracking-wide text-sub">
            {group.provisional ? "先后未确定的一组" : "可证明的先后"}
          </h3>
          <ul>
            {group.events.map((event) => (
              <EventRow key={event.id} event={event} onOpenSource={onOpenSource} />
            ))}
          </ul>
        </section>
      ))}
      {data.unknown.length > 0 && (
        <section className="border-b border-row-edge">
          <h3 className="toc-head px-3 py-1 text-xs font-bold tracking-wide text-sub">
            时间关系未知
          </h3>
          <ul>
            {data.unknown.map((event) => (
              <EventRow key={event.id} event={event} onOpenSource={onOpenSource} />
            ))}
          </ul>
        </section>
      )}
      {data.conflicts.map((conflict, index) => (
        <section key={index} className="border-b border-row-edge bg-amber-bg px-3 py-2">
          <h3 className="text-xs font-bold text-amber">{conflict.detail}</h3>
          <ul className="mt-1 list-disc pl-4 text-sm leading-5">
            {conflict.events.map((event) => (
              <li key={event.id}>
                第 {event.chapter} 章｜{event.mode_label}｜{event.text}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function PlanList({ plans }: { plans: StoryPlan[] }) {
  if (plans.length === 0) return null;
  return (
    <section className="border-b border-row-edge">
      <h3 className="toc-head flex items-center gap-2 px-3 py-1 text-xs font-bold tracking-wide text-sub">
        计划叠加
        <span className="font-normal text-mute">来自已装模块，不是正文事实</span>
        <span className="font-normal text-mute">（{plans.length}）</span>
      </h3>
      <ul>
        {plans.map((plan) => (
          <li
            key={`${plan.module_id}:${plan.entry_id}`}
            className="border-t border-row-edge px-3 py-1.5 text-sm leading-5"
          >
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-xs text-amber">
                {plan.planned_chapter ? `计划第 ${plan.planned_chapter} 章` : "未定章号"}
              </span>
              {plan.story_time && <span className="text-xs text-sub">{plan.story_time}</span>}
              <span className="font-bold">{plan.title || plan.entry_id}</span>
              <span className="text-xs text-mute">{plan.module_name}</span>
            </div>
            {plan.decision && <p className="text-xs text-sub">取舍：{plan.decision}</p>}
            {plan.excerpt && <p className="text-sm text-sub">{plan.excerpt}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function EventRow({
  event,
  onOpenSource,
}: {
  event: StoryEvent;
  onOpenSource: (path: string) => void;
}) {
  const time = event.story_time.label || event.story_time.normalized;
  return (
    <li className="border-t border-row-edge px-3 py-1.5 first:border-t-0">
      <div className="flex items-baseline gap-2">
        <span className="flex-none rounded bg-panel-head px-1 text-xs text-sub">
          {event.mode_label}
        </span>
        <span className="min-w-0 flex-1 text-sm leading-5">{event.text}</span>
        <span className="flex-none text-xs text-mute">第 {event.chapter} 章</span>
      </div>
      {time && <p className="mt-0.5 text-xs text-mute">时间：{time}</p>}
      {event.evidence.length > 0 && (
        <div className="mt-0.5 flex flex-wrap gap-1">
          {event.evidence.map((evidence, index) => (
            <Button
              key={index}
              className="max-w-full truncate rounded bg-panel-head px-1.5 py-0.5 text-left text-xs text-sub hover:underline"
              title="打开原文"
              onClick={() => onOpenSource(evidence.path)}
            >
              {evidence.quote}
            </Button>
          ))}
        </div>
      )}
    </li>
  );
}

function describe(error: unknown): string {
  return errorMessage(error, "故事事件");
}

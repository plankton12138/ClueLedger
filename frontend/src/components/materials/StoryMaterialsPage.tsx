/**
 * 故事资料页（0.7）。
 *
 * 左侧是资料页导航，中间是按章节或类别排列的资料，右侧是同页原文依据。资料页只读：
 * 正文与设定仍由对话修改；明确启动的整理任务只发布派生资料，不获得作品写权限。
 *
 * 两个范围分开：顶部的「查看截至」只影响浏览，整理按钮弹出的章节选择只影响这一次任务。
 * 选中范围不会跟着任务期间的导航变化，切换页面也不修改已经受理的任务。
 */

import { useEffect, useState } from "react";

import {
  fetchMaterialEntry,
  type MaterialDomain,
  type MaterialEntry,
} from "../../api/storyMaterials";
import {
  MATERIAL_SECTIONS,
  type MaterialSection,
  type StoryOrder,
  type WorkspacePage,
} from "../../state/navigation";
import { useMaterialTasksStore } from "../../state/materialTasks";
import { materialsKey, useStoryMaterialsStore } from "../../state/storyMaterials";
import ChapterRangePicker from "./ChapterRangePicker";
import EvidencePane from "./EvidencePane";
import MaterialTaskBar from "./MaterialTaskBar";
import StoryEventsPanel from "./StoryEventsPanel";
import {
  COVERAGE_LABEL,
  COVERAGE_TONE,
  FRESHNESS_LABEL,
  FRESHNESS_TONE,
} from "./labels";

const PRIMARY: Record<MaterialSection, MaterialDomain> = {
  story: "story_index",
  characters: "disclosure",
  clues: "clues",
  glossary: "glossary",
};

const DOMAIN_LABEL: Record<MaterialDomain, string> = {
  story_index: "章节索引与事件",
  chapter_index: "章节概览",
  disclosure: "身份披露",
  roster: "人物状态",
  timeline: "时间线",
  clues: "线索与伏笔",
  glossary: "名词表",
};

interface PickerState {
  domain: MaterialDomain;
  title: string;
  action: string;
  entitiesRequired: boolean;
}

interface Props {
  workspaceId: string;
  section: MaterialSection;
  chapter: number | null;
  order: StoryOrder;
  entryId?: string;
  onNavigate: (page: WorkspacePage) => void;
  onOpenSource: (path: string) => void;
  onTakeToChat: (path: string) => void;
  onOpenActivity: () => void;
}

function Badges({ entry }: { entry: MaterialEntry }) {
  return (
    <span className="flex flex-none items-center gap-2 text-[10px]">
      <span className={COVERAGE_TONE[entry.coverage]}>{COVERAGE_LABEL[entry.coverage]}</span>
      {entry.freshness !== "valid" && entry.freshness !== "none" && (
        <span className={FRESHNESS_TONE[entry.freshness]}>
          {FRESHNESS_LABEL[entry.freshness]}
        </span>
      )}
    </span>
  );
}

function Row({
  entry,
  active,
  onOpen,
  extra,
}: {
  entry: MaterialEntry;
  active: boolean;
  onOpen: () => void;
  extra?: string;
}) {
  return (
    <li>
      <button
        className={`flex w-full items-start gap-3 px-3 py-2 text-left ${
          active ? "bg-sel-high" : "hover:bg-panel-head"
        }`}
        onClick={onOpen}
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-[12px] font-bold">{entry.title}</span>
            {extra && <span className="flex-none text-[10px] text-mute">{extra}</span>}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-sub">
            {entry.subtitle || "—"}
          </span>
        </span>
        <Badges entry={entry} />
      </button>
    </li>
  );
}

export default function StoryMaterialsPage({
  workspaceId,
  section,
  chapter,
  order,
  entryId,
  onNavigate,
  onOpenSource,
  onTakeToChat,
  onOpenActivity,
}: Props) {
  const primary = PRIMARY[section];
  const cache = useStoryMaterialsStore((state) => state.cache);
  const loading = useStoryMaterialsStore((state) => state.loading);
  const error = useStoryMaterialsStore((state) => state.error);
  const load = useStoryMaterialsStore((state) => state.load);
  const clearMaterialsError = useStoryMaterialsStore((state) => state.clearError);

  const tasks = useMaterialTasksStore((state) => state.tasks);
  const active = useMaterialTasksStore((state) => state.active);
  const taskRevision = useMaterialTasksStore((state) => state.revision);
  const taskError = useMaterialTasksStore((state) => state.error);
  const loadTasks = useMaterialTasksStore((state) => state.load);
  const startTask = useMaterialTasksStore((state) => state.start);
  const stopTask = useMaterialTasksStore((state) => state.stop);
  const clearTaskError = useMaterialTasksStore((state) => state.clearError);

  const [picker, setPicker] = useState<PickerState | null>(null);
  const [fetchedEntry, setFetchedEntry] = useState<MaterialEntry | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const primaryData = cache[materialsKey(primary, chapter)];
  const rosterData = cache[materialsKey("roster", chapter)];

  useEffect(() => {
    // 每次进入页面、切换时点或任务结束都重读：缓存只用于先把上一份结果显示出来。
    void load(workspaceId, primary, { asOfChapter: chapter, refresh: true });
    if (section === "characters") {
      void load(workspaceId, "roster", { asOfChapter: chapter, refresh: true });
    }
  }, [workspaceId, primary, section, chapter, load, taskRevision, refreshToken]);

  useEffect(() => {
    void loadTasks(workspaceId, primary);
    if (section === "characters") void loadTasks(workspaceId, "roster");
  }, [workspaceId, primary, section, loadTasks]);

  // 人物页一人一行：身份与状态在详情里分区显示，不把同一个人拆成两条。
  const entries = primaryData?.entries ?? [];

  const selected =
    entries.find((entry) => entry.id === entryId) ??
    (fetchedEntry && fetchedEntry.id === entryId ? fetchedEntry : null);

  useEffect(() => {
    if (!entryId || selected) return;
    let alive = true;
    const domains: MaterialDomain[] =
      section === "characters" ? ["disclosure", "roster"] : [primary];
    void (async () => {
      for (const domain of domains) {
        try {
          const result = await fetchMaterialEntry(workspaceId, domain, entryId, chapter);
          if (alive) setFetchedEntry(result.entry);
          return;
        } catch {
          // 换下一个域再试；都找不到时保持空状态，不白屏。
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [entryId, selected, workspaceId, section, primary, chapter]);

  const sectionLabel =
    MATERIAL_SECTIONS.find((item) => item.key === section)?.label ?? DOMAIN_LABEL[primary];
  const asOf = primaryData?.as_of_chapter ?? chapter ?? 0;
  const chapters = primaryData?.chapters ?? [];
  const entities = primaryData?.entities ?? [];
  const runningTask =
    tasks[active[primary] ?? ""] ?? (section === "characters" ? tasks[active.roster ?? ""] : null);

  const open = (entry: MaterialEntry) =>
    onNavigate({ kind: "materials", workspaceId, section, chapter, order, entryId: entry.id });

  const changeChapter = (value: string) => {
    const next = value === "" ? null : Number(value);
    onNavigate({ kind: "materials", workspaceId, section, chapter: next, order });
  };

  const submitTask = async (
    domain: MaterialDomain,
    chapterPaths: string[],
    entityPaths: string[],
  ) => {
    const digests = new Map(chapters.map((item) => [item.path, item.digest]));
    const observed: Record<string, string> = {};
    for (const path of chapterPaths) {
      const digest = digests.get(path);
      if (digest) observed[path] = digest;
    }
    setPicker(null);
    await startTask(
      workspaceId,
      {
        domain,
        chapter_paths: chapterPaths,
        entity_paths: entityPaths,
        as_of_chapter: asOf,
        observed_digests: observed,
      },
      DOMAIN_LABEL[domain],
    );
  };

  const reload = () => {
    setRefreshToken((value) => value + 1);
    void loadTasks(workspaceId, primary);
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col md:flex-row">
      <nav className="materials-nav flex flex-none flex-row gap-0.5 overflow-x-auto px-2 py-2 md:w-[150px] md:flex-col md:py-3">
        <h2 className="hidden px-2 pb-1 text-[11px] font-bold tracking-wide text-sub md:block">
          故事资料
        </h2>
        {MATERIAL_SECTIONS.map((item) => (
          <button
            key={item.key}
            className={`rounded px-2 py-1 text-left text-[12px] ${
              item.key === section ? "bg-sel-high font-bold" : "hover:bg-panel-head"
            }`}
            aria-current={item.key === section ? "page" : undefined}
            onClick={() =>
              onNavigate({
                kind: "materials",
                workspaceId,
                section: item.key,
                chapter,
                order: "chapter",
              })
            }
          >
            {item.label}
          </button>
        ))}
      </nav>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex flex-none flex-wrap items-center gap-2 border-b border-panel-edge px-3 py-2">
          <h1 className="mr-1 text-sm font-bold">{sectionLabel}</h1>
          <label className="flex items-center gap-1 text-[11px] text-sub">
            查看截至
            <select
              className="well-input px-1 py-0.5 text-[11px]"
              aria-label="查看截至第几章"
              value={chapter ?? ""}
              onChange={(event) => changeChapter(event.target.value)}
            >
              <option value="">
                全书{primaryData ? `（第 ${primaryData.current_chapter} 章）` : ""}
              </option>
              {chapters.map((item) => (
                <option key={item.path} value={item.chapter}>
                  第 {item.chapter} 章
                </option>
              ))}
            </select>
          </label>
          {primaryData && (
            <span className="text-[10px] text-mute">
              有结果 {primaryData.coverage.found} · 无发现 {primaryData.coverage.empty} · 未整理{" "}
              {primaryData.coverage.unrecorded}
              {primaryData.coverage.stale > 0 ? ` · 已失效 ${primaryData.coverage.stale}` : ""}
            </span>
          )}
          <div className="flex-1" />
          <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={reload}>
            重新读取资料
          </button>
          {section === "story" && (
            <button
              className="btn btn-gold px-2 py-1 text-[11px]"
              disabled={chapters.length === 0}
              onClick={() =>
                setPicker({
                  domain: "story_index",
                  title: "整理章节索引",
                  action: "整理所选章节",
                  entitiesRequired: false,
                })
              }
            >
              整理所选章节
            </button>
          )}
          {section === "characters" && (
            <>
              <button
                className="btn btn-gold px-2 py-1 text-[11px]"
                disabled={chapters.length === 0 || entities.length === 0}
                onClick={() =>
                  setPicker({
                    domain: "disclosure",
                    title: "核对身份披露",
                    action: "开始核对",
                    entitiesRequired: true,
                  })
                }
              >
                核对身份披露
              </button>
              <button
                className="btn btn-gray px-2 py-1 text-[11px]"
                disabled={chapters.length === 0 || entities.length === 0}
                onClick={() =>
                  setPicker({
                    domain: "roster",
                    title: "整理人物状态",
                    action: "开始整理",
                    entitiesRequired: true,
                  })
                }
              >
                从章节整理人物状态
              </button>
            </>
          )}
          {section === "clues" && (
            <button
              className="btn btn-gold px-2 py-1 text-[11px]"
              disabled={chapters.length === 0}
              onClick={() =>
                setPicker({
                  domain: "clues",
                  title: "整理线索",
                  action: "从章节整理线索",
                  entitiesRequired: false,
                })
              }
            >
              从章节整理线索
            </button>
          )}
          {section === "glossary" && (
            <button
              className="btn btn-gold px-2 py-1 text-[11px]"
              disabled={chapters.length === 0}
              onClick={() =>
                setPicker({
                  domain: "glossary",
                  title: "整理名词",
                  action: "从章节整理名词",
                  entitiesRequired: false,
                })
              }
            >
              从章节整理名词
            </button>
          )}
        </header>

        {(error || taskError) && (
          <div className="flex flex-none items-center gap-2 border-b border-red-edge bg-red-bg px-3 py-1.5 text-[11px] text-red">
            <span className="min-w-0 flex-1">{error ?? taskError}</span>
            <button
              className="underline"
              onClick={() => {
                clearMaterialsError();
                clearTaskError();
              }}
            >
              知道了
            </button>
          </div>
        )}

        <MaterialTaskBar
          task={runningTask ?? null}
          onStop={() => runningTask && void stopTask(workspaceId, runningTask.turn_id)}
          onOpenActivity={onOpenActivity}
        />

        {section === "story" ? (
          <StoryEventsPanel
            workspaceId={workspaceId}
            order={order}
            chapter={chapter}
            refreshKey={taskRevision + refreshToken}
            onOrderChange={(next) =>
              onNavigate({ kind: "materials", workspaceId, section, chapter, order: next })
            }
            onOpenSource={onOpenSource}
          />
        ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
          <div className="min-h-0 flex-1 overflow-auto">
            {loading[materialsKey(primary, chapter)] && !primaryData && (
              <p className="px-3 py-2 text-[11px] text-mute">正在读取资料…</p>
            )}
            {!loading[materialsKey(primary, chapter)] && primaryData && chapters.length === 0 && (
              <p className="px-3 py-2 text-[11px] text-mute">
                这个作品还没有章节。先在写作页建立正文，再回来整理资料。
              </p>
            )}

            {primaryData?.warnings.length ? (
              <ul className="space-y-1 border-b border-row-edge px-3 py-2 text-[10px] text-amber">
                {primaryData.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            ) : null}

            {primaryData?.conflicts.length ? (
              <ul className="space-y-1 border-b border-row-edge px-3 py-2 text-[10px] text-amber">
                {primaryData.conflicts.map((conflict, index) => (
                  <li key={index}>
                    {conflict.label ?? "冲突"}：{conflict.detail}
                  </li>
                ))}
              </ul>
            ) : null}

            {section === "characters" ? (
              <CharactersList
                entries={entries}
                roster={rosterData?.entries ?? []}
                selectedId={selected?.id}
                onOpen={open}
              />
            ) : section === "clues" ? (
              <CluesList entries={entries} selectedId={selected?.id} onOpen={open} />
            ) : (
              <ul className="divide-y divide-row-edge">
                {entries.map((entry) => (
                  <Row
                    key={entry.id}
                    entry={entry}
                    active={entry.id === selected?.id}
                    onOpen={() => open(entry)}
                    extra={entry.chapter !== null ? `第 ${entry.chapter} 章` : undefined}
                  />
                ))}
                {entries.length === 0 && primaryData && chapters.length > 0 && (
                  <li className="px-3 py-2 text-[11px] text-mute">这个时点下没有资料。</li>
                )}
              </ul>
            )}
          </div>

          {selected ? (
            <div className="flex max-h-[60%] w-full flex-none flex-col border-t border-panel-edge md:max-h-none md:w-[360px] md:border-l md:border-t-0">
              <EntryDetail
                entry={selected}
                roster={rosterData?.entries.find(
                  (item) => item.entity_path === selected.entity_path,
                )}
                onTakeToChat={onTakeToChat}
              />
              <EvidencePane
                entry={selected}
                onOpenSource={onOpenSource}
                onTakeToChat={onTakeToChat}
                onClose={() =>
                  onNavigate({ kind: "materials", workspaceId, section, chapter, order })
                }
              />
            </div>
          ) : null}
        </div>
        )}
      </section>

      {picker && primaryData && (
        <ChapterRangePicker
          open
          title={picker.title}
          actionLabel={picker.action}
          chapters={chapters}
          entries={primaryData.entries}
          entities={picker.entitiesRequired ? entities : []}
          entitiesRequired={picker.entitiesRequired}
          onCancel={() => setPicker(null)}
          onConfirm={(chapterPaths, entityPaths) =>
            void submitTask(picker.domain, chapterPaths, entityPaths)
          }
        />
      )}
    </div>
  );
}

function CharactersList({
  entries,
  roster,
  selectedId,
  onOpen,
}: {
  entries: MaterialEntry[];
  roster: MaterialEntry[];
  selectedId?: string;
  onOpen: (entry: MaterialEntry) => void;
}) {
  const byEntity = new Map(roster.map((entry) => [entry.entity_path, entry]));
  if (entries.length === 0) {
    return (
      <p className="px-3 py-2 text-[11px] text-mute">
        还没有角色小传。在对话里建立小传后，这里会显示身份与状态。
      </p>
    );
  }
  return (
    <ul className="divide-y divide-row-edge">
      {entries.map((entry) => {
        const state = entry.entity_path ? byEntity.get(entry.entity_path) : undefined;
        const alive =
          state && state.domain === "roster"
            ? state.data.alive === null
              ? "生死未知"
              : state.data.alive
                ? "在世"
                : "已死亡"
            : "";
        return (
          <Row
            key={entry.id}
            entry={entry}
            active={entry.id === selectedId}
            onOpen={() => onOpen(entry)}
            extra={alive}
          />
        );
      })}
    </ul>
  );
}

function CluesList({
  entries,
  selectedId,
  onOpen,
}: {
  entries: MaterialEntry[];
  selectedId?: string;
  onOpen: (entry: MaterialEntry) => void;
}) {
  const groups: { key: string; label: string; rows: MaterialEntry[] }[] = [
    { key: "open", label: "未回收", rows: [] },
    { key: "resolved", label: "已回收", rows: [] },
    { key: "dropped", label: "已废弃", rows: [] },
  ];
  for (const entry of entries) {
    if (entry.domain !== "clues") continue;
    const group = groups.find((item) => item.key === entry.data.status) ?? groups[0];
    group.rows.push(entry);
  }
  return (
    <div>
      {groups.map((group) => (
        <section key={group.key}>
          <h3 className="toc-head px-3 py-1 text-[10px] font-bold tracking-wide text-sub">
            {group.label}（{group.rows.length}）
          </h3>
          <ul className="divide-y divide-row-edge">
            {group.rows.map((entry) => (
              <Row
                key={entry.id}
                entry={entry}
                active={entry.id === selectedId}
                onOpen={() => onOpen(entry)}
                extra={
                  entry.domain === "clues" && entry.data.overdue ? "已超期" : undefined
                }
              />
            ))}
            {group.rows.length === 0 && (
              <li className="px-3 py-1 text-[10px] text-mute">没有。</li>
            )}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <p className="text-[11px] leading-5">
      <span className="text-mute">{label}：</span>
      {value}
    </p>
  );
}

function EntryDetail({
  entry,
  roster,
  onTakeToChat,
}: {
  entry: MaterialEntry;
  roster?: MaterialEntry;
  onTakeToChat: (path: string) => void;
}) {
  return (
    <div className="max-h-[55%] flex-none overflow-auto border-b border-panel-edge px-3 py-2 md:border-b-0">
      <h2 className="text-[13px] font-bold">{entry.title}</h2>
      <p className="mt-0.5 text-[10px] text-mute">
        {entry.chapter !== null ? `第 ${entry.chapter} 章 · ` : ""}
        {COVERAGE_LABEL[entry.coverage]}
        {entry.freshness !== "valid" && entry.freshness !== "none"
          ? ` · ${FRESHNESS_LABEL[entry.freshness]}`
          : ""}
      </p>

      <div className="mt-2 space-y-1">
        {entry.domain === "chapter_index" && (
          <>
            <p className="whitespace-pre-wrap text-[11px] leading-5">
              {entry.data.summary || "还没有摘要。"}
            </p>
            {(
              [
                ["事件", entry.data.events],
                ["人物", entry.data.characters],
                ["时间地点", entry.data.places],
                ["线索", entry.data.clues],
              ] as const
            ).map(([label, rows]) =>
              rows.length ? (
                <div key={label}>
                  <h3 className="mt-1 text-[10px] font-bold text-sub">{label}</h3>
                  <ul className="list-disc pl-4 text-[11px] leading-5">
                    {rows.map((row, index) => (
                      <li key={index}>{row}</li>
                    ))}
                  </ul>
                </div>
              ) : null,
            )}
          </>
        )}

        {entry.domain === "timeline" && (
          <>
            <Field label="原文时间" value={entry.data.when || "时间未知"} />
            <Field label="跨度" value={entry.data.span} />
            <Field label="地点" value={entry.data.location} />
          </>
        )}

        {entry.domain === "clues" && (
          <>
            <Field label="状态" value={entry.data.label} />
            <Field label="埋设" value={`第 ${entry.data.planted_chapter} 章`} />
            <Field
              label="计划回收"
              value={entry.data.due_chapter ? `第 ${entry.data.due_chapter} 章` : "未定"}
            />
            <Field
              label="回收"
              value={
                entry.data.resolution_chapter ? `第 ${entry.data.resolution_chapter} 章` : ""
              }
            />
            <Field label="指向" value={entry.data.supports} />
            {entry.data.overdue && (
              <p className="text-[10px] text-amber">计划回收章已过，仍未回收。</p>
            )}
          </>
        )}

        {entry.domain === "glossary" && (
          <>
            <Field label="类别" value={entry.data.label} />
            <Field label="别名" value={entry.data.aliases.join("、")} />
            <Field
              label="首次出现"
              value={entry.data.first_chapter ? `第 ${entry.data.first_chapter} 章` : ""}
            />
            <Field
              label="来源"
              value={entry.data.source === "declared" ? "人物小传声明" : "正文登记"}
            />
          </>
        )}

        {entry.domain === "disclosure" && (
          <>
            <Field label="社会身份" value={entry.data.surface} />
            <Field label="别名" value={entry.data.aliases.join("、")} />
            <Field label="角色" value={entry.data.roles.join("、")} />
            <h3 className="mt-1 text-[10px] font-bold text-sub">读者已知</h3>
            {entry.data.revealed.length === 0 ? (
              <p className="text-[11px] text-mute">还没有已披露的信息。</p>
            ) : (
              <ul className="list-disc pl-4 text-[11px] leading-5">
                {entry.data.revealed.map((fact, index) => (
                  <li key={index}>
                    第 {fact.chapter} 章：{fact.fact}
                    <span className="ml-1 text-[10px] text-mute">
                      {fact.source === "text" ? "正文核对" : "作者声明"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {entry.data.upcoming.length > 0 && (
              <>
                <h3 className="mt-1 text-[10px] font-bold text-sub">正文还没写到</h3>
                <ul className="list-disc pl-4 text-[11px] leading-5 text-mute">
                  {entry.data.upcoming.map((fact, index) => (
                    <li key={index}>
                      第 {fact.chapter} 章：{fact.fact}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {entry.data.planned.length > 0 && (
              <>
                <h3 className="mt-1 text-[10px] font-bold text-sub">作者计划</h3>
                <ul className="list-disc pl-4 text-[11px] leading-5 text-mute">
                  {entry.data.planned.map((fact, index) => (
                    <li key={index}>
                      第 {fact.chapter} 章：{fact.fact}
                    </li>
                  ))}
                </ul>
                <p className="text-[10px] text-mute">计划不是正文事实，不进入写作上下文。</p>
              </>
            )}
            {roster && roster.domain === "roster" && (
              <>
                <h3 className="mt-1 text-[10px] font-bold text-sub">人物状态</h3>
                <Field
                  label="位置"
                  value={roster.data.location || (roster.data.chapter ? "位置未记" : "状态未知")}
                />
                <Field
                  label="生死"
                  value={
                    roster.data.alive === null ? "未知" : roster.data.alive ? "在世" : "已死亡"
                  }
                />
                <Field label="立场" value={roster.data.stance} />
                <Field
                  label="最近核对"
                  value={roster.data.chapter ? `第 ${roster.data.chapter} 章` : ""}
                />
                {roster.data.lag ? (
                  <p className="text-[10px] text-amber">
                    此后 {roster.data.lag} 章没有核对记录，这些章的变动未知。
                  </p>
                ) : null}
              </>
            )}
          </>
        )}

        {entry.domain === "roster" && (
          <>
            <Field
              label="位置"
              value={entry.data.location || (entry.data.chapter ? "位置未记" : "状态未知")}
            />
            <Field
              label="生死"
              value={
                entry.data.alive === null ? "未知" : entry.data.alive ? "在世" : "已死亡"
              }
            />
            <Field label="立场" value={entry.data.stance} />
            <Field
              label="最近核对"
              value={entry.data.chapter ? `第 ${entry.data.chapter} 章` : ""}
            />
            {entry.data.lag ? (
              <p className="text-[10px] text-amber">
                此后 {entry.data.lag} 章没有核对记录，这些章的变动未知。
              </p>
            ) : null}
          </>
        )}
      </div>

      {entry.entity_path && (
        <button
          className="btn btn-gray mt-2 px-2 py-0.5 text-[10px]"
          onClick={() => onTakeToChat(entry.entity_path as string)}
        >
          把人物小传带到对话
        </button>
      )}
    </div>
  );
}

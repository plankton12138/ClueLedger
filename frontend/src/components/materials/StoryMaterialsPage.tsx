/**
 * 故事资料页（0.7）。
 *
 * 左侧是资料页导航，中间是按章节或类别排列的资料，右侧是同页原文依据。资料页只读：
 * 正文与设定仍由对话修改；明确启动的整理任务只发布派生资料，不获得作品写权限。
 *
 * 两个范围分开：顶部的「查看截至」只影响浏览，整理按钮弹出的章节选择只影响这一次任务。
 * 选中范围不会跟着任务期间的导航变化，切换页面也不修改已经受理的任务。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  fetchMaterialEntry,
  type MaterialDomain,
  type MaterialEntry,
} from "../../api/storyMaterials";
import {
  type MaterialSection,
  type StoryOrder,
  type WorkspacePage,
} from "../../state/navigation";
import { useMaterialTasksStore } from "../../state/materialTasks";
import { materialsKey, useStoryMaterialsStore } from "../../state/storyMaterials";
import ChapterRangePicker from "./ChapterRangePicker";
import EvidencePane from "./EvidencePane";
import ClueDetail from "../clues/ClueDetail";
import NewClueDialog from "../clues/NewClueDialog";
import MaterialTaskBar from "./MaterialTaskBar";
import StoryEventsPanel from "./StoryEventsPanel";
import { Row, CharactersList, CluesList, EntryDetail } from "./MaterialEntries";
import MaterialNavigation from "./MaterialNavigation";
import MaterialBrowserBar from "./MaterialBrowserBar";
import OrganizeActions from "./OrganizeActions";
import { useLayoutStore } from "../../state/layout";
import { useConversationsStore } from "../../state/conversations";
import type { ClueReferenceInput } from "../../api/workspace";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import { usePagePositions } from "../../state/pagePositions";
import type { StoryMaterials } from "../../api/storyMaterials";

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
  workspaceId: string;
  asOf: number;
  data: StoryMaterials;
}

interface Props {
  workspaceId: string;
  section: MaterialSection;
  chapter: number | null;
  order: StoryOrder;
  entryId?: string;
  onNavigate: (page: WorkspacePage) => void;
  onOpenSource: (path: string) => void;
  onLocateEvidence?: (evidenceId: string) => void;
  onTakeToChat: (path: string) => void;
  onOpenActivity: () => void;
}

export default function StoryMaterialsPage({
  workspaceId,
  section,
  chapter,
  order,
  entryId,
  onNavigate,
  onOpenSource,
  onLocateEvidence,
  onTakeToChat,
  onOpenActivity,
}: Props) {
  const primary = PRIMARY[section];
  const root = useRef<HTMLDivElement>(null), list = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1440);
  const scale = useLayoutStore(state => state.fontScale);
  const compact = width < 1100 * Math.max(1, scale / 100);
  const positionKey = `${workspaceId}:materials:${section}:${chapter ?? ""}`;
  useLayoutEffect(() => {
    if (!root.current) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width) setWidth(entries[0].contentRect.width); });
    observer.observe(root.current); return () => observer.disconnect();
  }, []);
  const dataWorkspace = useStoryMaterialsStore(state => state.workspaceId);
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
  const [newClue, setNewClue] = useState(false);
  const lookupKey = `${workspaceId}|${primary}|${chapter ?? ""}|${entryId ?? ""}`;
  const [fetchedEntry, setFetchedEntry] = useState<{ key: string; entry: MaterialEntry } | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const primaryData = dataWorkspace === workspaceId ? cache[materialsKey(primary, chapter)] : undefined;
  const rosterData = dataWorkspace === workspaceId ? cache[materialsKey("roster", chapter)] : undefined;

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

  // 资料页只登记引用：切到写作页后由作者决定要不要发送这一轮。
  async function takeClue(reference: ClueReferenceInput, title: string) {
    const store = useConversationsStore.getState();
    if (store.workspaceId !== workspaceId) await store.load(workspaceId);
    if (useConversationsStore.getState().addClueReference({ ...reference, title }, title)) {
      onNavigate({ kind: "writing", workspaceId });
    }
  }

  // 人物页一人一行：身份与状态在详情里分区显示，不把同一个人拆成两条。
  const entries = primaryData?.entries ?? [];

  const selected =
    entries.find((entry) => entry.id === entryId) ??
    (fetchedEntry?.key === lookupKey && fetchedEntry.entry.id === entryId ? fetchedEntry.entry : null);

  useEffect(() => {
    if (!entryId || selected) return;
    let alive = true;
    const domains: MaterialDomain[] =
      section === "characters" ? ["disclosure", "roster"] : [primary];
    void (async () => {
      for (const domain of domains) {
        try {
          const result = await fetchMaterialEntry(workspaceId, domain, entryId, chapter);
          if (alive) setFetchedEntry({ key: lookupKey, entry: result.entry });
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

  const asOf = primaryData?.as_of_chapter ?? chapter ?? 0;
  const chapters = primaryData?.chapters ?? [];
  const entities = primaryData?.entities ?? [];
  const runningTask =
    tasks[active[primary] ?? ""] ?? (section === "characters" ? tasks[active.roster ?? ""] : null);

  const open = (entry: MaterialEntry) =>
    onNavigate({ kind: "materials", workspaceId, section, chapter, order, entryId: entry.id });

  function openPicker(input: Omit<PickerState, "workspaceId" | "asOf" | "data">) {
    if (!primaryData) return;
    setPicker({ ...input, workspaceId, asOf, data: structuredClone(primaryData) });
  }
  async function submitTask(chapterPaths: string[], entityPaths: string[]) {
    if (!picker) return;
    const frozen = picker;
    const digests = new Map(frozen.data.chapters.map(item => [item.path, item.digest]));
    const observed = Object.fromEntries(chapterPaths.map(path => [path, digests.get(path) ?? ""]));
    setPicker(null);
    await startTask(frozen.workspaceId, { domain: frozen.domain, chapter_paths: [...chapterPaths], entity_paths: [...entityPaths],
      as_of_chapter: frozen.asOf, observed_digests: observed }, DOMAIN_LABEL[frozen.domain]);
  }
  useLayoutEffect(() => { if (list.current) list.current.scrollTop = usePagePositions.getState().offsets[positionKey] ?? 0; }, [positionKey, primaryData]);

  const reload = () => {
    setRefreshToken((value) => value + 1);
    void loadTasks(workspaceId, primary);
  };

  return (
    <div className="materials-page" data-compact={compact} ref={root}>
      <MaterialNavigation workspaceId={workspaceId} section={section} chapter={chapter} onNavigate={onNavigate} />
      <section className="materials-main module">
        <MaterialBrowserBar section={section} chapter={chapter} data={primaryData} onChange={next => onNavigate({ kind: "materials", workspaceId, section, chapter: next, order })} onReload={reload} />
        <OrganizeActions section={section} hasChapters={chapters.length > 0} hasEntities={entities.length > 0} onChoose={openPicker} />
        {loading[materialsKey(primary, chapter)] && primaryData && <StatusMessage>正在更新资料…</StatusMessage>}
        {(error || taskError) && (
          <div className="flex flex-none items-center gap-2 border-b border-red-edge bg-red-bg px-3 py-1.5 text-sm text-red">
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
            onReload={reload}
            onOrderChange={(next) =>
              onNavigate({ kind: "materials", workspaceId, section, chapter, order: next })
            }
            onOpenSource={onOpenSource}
          />
        ) : (
        <div className="materials-content">
          <div ref={list} className="materials-list" hidden={compact && Boolean(selected)} onScroll={event => { if (event.currentTarget.clientHeight && primaryData) usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop); }}>
            {section === "clues" && (
              <div className="flex justify-end border-b border-row-edge px-3 py-2">
                <Button size="compact" onClick={() => setNewClue(true)}>新建线索设计</Button>
              </div>
            )}
            {loading[materialsKey(primary, chapter)] && !primaryData && (
              <p className="px-3 py-2 text-sm text-mute">正在读取资料…</p>
            )}
            {!loading[materialsKey(primary, chapter)] && primaryData && chapters.length === 0 && (
              <p className="px-3 py-2 text-sm text-mute">
                这个作品还没有章节。先在写作页建立正文，再回来整理资料。
              </p>
            )}

            {primaryData?.warnings.length ? (
              <ul className="space-y-1 border-b border-row-edge px-3 py-2 text-xs text-amber">
                {primaryData.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            ) : null}

            {primaryData?.conflicts.length ? (
              <ul className="space-y-1 border-b border-row-edge px-3 py-2 text-xs text-amber">
                {primaryData.conflicts.map((conflict, index) => (
                  <li key={index}>
                    {conflict.label ?? "冲突"}：{conflict.detail}
                  </li>
                ))}
              </ul>
            ) : null}

            {!primaryData ? null : section === "characters" ? (
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
                  <li className="px-3 py-2 text-sm text-mute">这个时点下没有资料。</li>
                )}
              </ul>
            )}
          </div>

          {selected ? (
            <div className="materials-evidence" data-compact={compact}>
              {compact && <Button size="compact" onClick={() => onNavigate({ kind: "materials", workspaceId, section, chapter, order })}>返回资料列表</Button>}
              {selected.domain === "clues" ? <ClueDetail workspaceId={workspaceId} clueId={selected.id} asOf={chapter}
                refreshKey={`${taskRevision}:${refreshToken}:${selected.freshness}:${selected.updated_at}`}
                onLocate={onLocateEvidence}
                onReference={(reference: ClueReferenceInput, title: string) => void takeClue(reference, title)}
                onClose={compact ? undefined : () => onNavigate({ kind: "materials", workspaceId, section, chapter, order })} /> : <><EntryDetail
                entry={selected}
                roster={rosterData?.entries.find(
                  (item) => item.entity_path === selected.entity_path,
                )}
                onTakeToChat={onTakeToChat}
              />
              <EvidencePane
                entry={selected}
                showClose={!compact}
                onOpenSource={onOpenSource}
                onLocateEvidence={onLocateEvidence}
                onTakeToChat={onTakeToChat}
                onClose={() =>
                  onNavigate({ kind: "materials", workspaceId, section, chapter, order })
                }
              />
              </>}
            </div>
          ) : null}
        </div>
        )}
      </section>

      {picker && (
        <ChapterRangePicker
          open
          title={picker.title}
          actionLabel={picker.action}
          chapters={picker.data.chapters}
          entries={picker.data.entries}
          entities={picker.entitiesRequired ? picker.data.entities : undefined}
          entitiesRequired={picker.entitiesRequired}
          onCancel={() => setPicker(null)}
          onConfirm={(chapterPaths, entityPaths) =>
            void submitTask(chapterPaths, entityPaths)
          }
        />
      )}
      {newClue && (
        <NewClueDialog
          workspaceId={workspaceId}
          asOf={asOf}
          onClose={() => setNewClue(false)}
          onCreated={clueId => {
            setNewClue(false);
            setRefreshToken(value => value + 1);
            onNavigate({ kind: "materials", workspaceId, section, chapter, order, entryId: clueId });
          }}
        />
      )}
    </div>
  );
}

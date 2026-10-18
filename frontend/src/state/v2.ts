import { create } from "zustand";
import {
  fetchManuscript,
  fetchStorySnapshot,
  fetchV2Chapter,
  listAgentJobs,
  listChanges,
  saveV2Chapter,
  type AgentJob,
  type ChangeSetWire,
  type ChapterHeadWire,
  type StoryArtifact,
} from "../api/v2";
export interface V2ProjectRef {
  projectId: string;
  name: string;
  source: "created";
}
const REGISTRY_KEY = "hastings.v2.projects";
const CURRENT_KEY = "hastings.v2.current";
function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* 浏览器禁用存储时仍可在本次会话工作。 */
  }
}
export function loadRegistry(): V2ProjectRef[] {
  try {
    const rows: unknown = JSON.parse(read(REGISTRY_KEY) ?? "[]");
    return Array.isArray(rows)
      ? rows.filter(
          (p): p is V2ProjectRef =>
            p &&
            typeof p.projectId === "string" &&
            typeof p.name === "string" &&
            p.source === "created",
        )
      : [];
  } catch {
    return [];
  }
}
export function registerProject(ref: V2ProjectRef): V2ProjectRef[] {
  const rows = [
    ...useV2Store
      .getState()
      .projects.filter((p) => p.projectId !== ref.projectId),
    ref,
  ];
  write(REGISTRY_KEY, JSON.stringify(rows));
  useV2Store.setState({ projects: rows });
  return rows;
}
export interface ChapterEditState {
  chapterId: string | null;
  baseline: string | null;
  draft: string | null;
  chapterBase: string;
  title: string;
  saving: boolean;
  loading: boolean;
  error: string | null;
  lastSavedAt: string | null;
  stale: boolean;
}
const idleChapter: ChapterEditState = {
  chapterId: null,
  baseline: null,
  draft: null,
  chapterBase: "",
  title: "",
  saving: false,
  loading: false,
  error: null,
  lastSavedAt: null,
  stale: false,
};
export const isDirty = (c: ChapterEditState) =>
  c.draft !== null && c.draft !== c.baseline;
interface V2Store {
  projects: V2ProjectRef[];
  currentId: string | null;
  head: string | null;
  chapters: ChapterHeadWire[];
  chapter: ChapterEditState;
  storyArtifacts: StoryArtifact[];
  storyCommit: string | null;
  changes: ChangeSetWire[];
  jobs: AgentJob[];
  error: string | null;
  selectedChangeId: string | null;
  focusArtifactId: string | null;
  setProjects(projects: V2ProjectRef[]): void;
  selectProject(projectId: string | null, discard?: boolean): boolean;
  setError(message: string | null): void;
  refreshManuscript(): Promise<void>;
  refreshStory(): Promise<void>;
  refreshChanges(): Promise<void>;
  refreshJobs(): Promise<void>;
  selectChapter(chapterId: string, discard?: boolean): Promise<void>;
  setDraft(text: string): void;
  saveChapter(): Promise<void>;
}
export const useV2Store = create<V2Store>()((set, get) => {
  const projects = loadRegistry();
  let generation = 0;
  let chapterRequest = 0;
  const requests: Record<string, number> = {};
  async function refresh<T>(
    name: string,
    fetcher: (id: string) => Promise<T>,
    apply: (data: T) => void,
  ) {
    const pid = get().currentId;
    if (!pid) return;
    const epoch = generation;
    const token = (requests[name] = (requests[name] ?? 0) + 1);
    try {
      const value = await fetcher(pid);
      if (epoch === generation && token === requests[name]) apply(value);
    } catch (e) {
      if (epoch === generation && token === requests[name])
        set({ error: message(e) });
    }
  }
  return {
    projects,
    currentId: projects.find((p) => p.projectId === read(CURRENT_KEY))?.projectId
      ?? projects[0]?.projectId ?? null,
    head: null,
    chapters: [],
    chapter: { ...idleChapter },
    storyArtifacts: [],
    storyCommit: null,
    changes: [],
    jobs: [],
    error: null,
    selectedChangeId: null,
    focusArtifactId: null,
    setProjects: (projects) => {
      write(REGISTRY_KEY, JSON.stringify(projects));
      set({ projects });
    },
    selectProject: (currentId, discard = false) => {
      if (get().currentId === currentId) return true;
      if (get().chapter.saving || (!discard && isDirty(get().chapter)))
        return false;
      generation++;
      chapterRequest++;
      write(CURRENT_KEY, currentId);
      set({
        currentId,
        head: null,
        chapters: [],
        chapter: { ...idleChapter },
        storyArtifacts: [],
        storyCommit: null,
        changes: [],
        jobs: [],
        error: null,
        selectedChangeId: null,
        focusArtifactId: null,
      });
      return true;
    },
    setError: (error) => set({ error }),
    refreshManuscript: () =>
      refresh("manuscript", fetchManuscript, (data) => {
        set({ chapters: data.chapters, head: data.commit_id });
        const current = get().chapter;
        const updated = data.chapters.find(
          (c) => c.chapter_id === current.chapterId,
        );
        if (current.chapterId === null && data.chapters.length)
          void get().selectChapter(data.chapters[0]!.chapter_id);
        else if (
          updated &&
          current.chapterBase &&
          updated.chapter_base !== current.chapterBase &&
          !current.saving
        ) {
          if (isDirty(current)) set({ chapter: { ...current, stale: true } });
          else void get().selectChapter(updated.chapter_id, true);
        }
      }),
    refreshStory: () =>
      refresh("story", fetchStorySnapshot, (data) =>
        set({ storyArtifacts: data.artifacts, storyCommit: data.commit_id }),
      ),
    refreshChanges: () =>
      refresh("changes", listChanges, (data) => set({ changes: data.changes })),
    refreshJobs: () => refresh("jobs", listAgentJobs, (jobs) => set({ jobs })),
    selectChapter: async (chapterId, discard = false) => {
      const pid = get().currentId;
      if (!pid || get().chapter.saving || (!discard && isDirty(get().chapter)))
        return;
      const epoch = generation,
        token = ++chapterRequest;
      set({ chapter: { ...idleChapter, chapterId, loading: true } });
      try {
        const ch = await fetchV2Chapter(pid, chapterId);
        if (epoch !== generation || token !== chapterRequest) return;
        set({
          chapter: {
            ...idleChapter,
            chapterId,
            baseline: ch.text,
            draft: ch.text,
            chapterBase: ch.chapter_base,
            title: ch.title,
          },
        });
      } catch (e) {
        if (epoch === generation && token === chapterRequest)
          set((s) => ({
            chapter: { ...s.chapter, loading: false, error: message(e) },
          }));
      }
    },
    setDraft: (draft) => set((s) => ({ chapter: { ...s.chapter, draft } })),
    saveChapter: async () => {
      const { currentId, chapter } = get();
      if (
        !currentId ||
        !chapter.chapterId ||
        chapter.draft === null ||
        chapter.saving ||
        chapter.loading
      )
        return;
      const epoch = generation,
        cid = chapter.chapterId;
      set({ chapter: { ...chapter, saving: true, error: null } });
      try {
        const result = await saveV2Chapter(
          currentId,
          cid,
          chapter.draft,
          chapter.chapterBase,
        );
        if (epoch !== generation || get().chapter.chapterId !== cid) return;
        set((s) => ({
          head: result.new_head ?? s.head,
          chapter: {
            ...s.chapter,
            baseline: chapter.draft,
            chapterBase: result.chapter_base ?? chapter.chapterBase,
            saving: false,
            stale: false,
            lastSavedAt: new Date().toLocaleTimeString("zh-CN", {
              hour12: false,
            }),
          },
        }));
        void get().refreshManuscript();
        void get().refreshStory();
        void get().refreshChanges();
      } catch (e) {
        if (epoch === generation && get().chapter.chapterId === cid)
          set((s) => ({
            chapter: { ...s.chapter, saving: false, error: message(e) },
          }));
      }
    },
  };
});
export function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
export function currentProjectName(state: {
  projects: V2ProjectRef[];
  currentId: string | null;
}): string {
  return (
    state.projects.find((p) => p.projectId === state.currentId)?.name ??
    "未选择项目"
  );
}
export function chapterOrder(
  chapters: ChapterHeadWire[],
  chapterId: string | null,
): number {
  return chapters.findIndex((c) => c.chapter_id === chapterId) + 1;
}

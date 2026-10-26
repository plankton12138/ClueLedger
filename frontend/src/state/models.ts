import { errorMessage } from "../api/errors";
/**
 * 模型设置状态（0.4 V04-9C）。
 *
 * 只保存脱敏事实：连接列表、当前选择、配置版本与界面状态。API Key 不进这里——
 * 它在表单组件内短暂持有，保存成功后即丢弃；界面也不做任何浏览器持久化，
 * 否则「换台机器结果一样」就不成立了。
 *
 * 写操作返回的都是完整的「列表 + 选择 + 版本」，一次应用即可：不需要在两次请求
 * 之间显示「新连接配旧选择」。版本冲突（409）把 `conflict` 置位，界面保留表单
 * 并提示重新读取，而不是悄悄覆盖别人的修改。
 */

import { create } from "zustand";

import {
  createConnection,
  deleteConnection,
  listAuthProviders,
  listProviders,
  ModelApiError,
  readConfig,
  selectModel,
  updateConnection,
  type AuthProvider,
  type ConnectionDraft,
  type ModelConfig,
  type ModelConnection,
  type ModelSelection,
  type ProviderPreset,
  type ThinkingLevel,
} from "../api/models";

const EMPTY_SELECTION: ModelSelection = {
  connection_id: null,
  model: "",
  source: "none",
  available: false,
  reason: "还没有可用的模型连接",
  thinking_level: null,
  thinking_levels: [],
};

interface ModelsStore {
  presets: ProviderPreset[];
  /** 可以登录的订阅来源；与 API Key 预设分开，界面按供应商名显示。 */
  authProviders: AuthProvider[];
  connections: ModelConnection[];
  selection: ModelSelection;
  revision: string;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  conflict: boolean;
  settingsOpen: boolean;

  init(): Promise<void>;
  refresh(): Promise<void>;
  openSettings(): void;
  closeSettings(): void;
  create(draft: ConnectionDraft): Promise<boolean>;
  update(name: string, draft: ConnectionDraft): Promise<boolean>;
  remove(name: string): Promise<boolean>;
  choose(
    connectionId: string | null,
    model: string,
    thinkingLevel?: ThinkingLevel | null,
  ): Promise<boolean>;
  dismissError(): void;
}

function describe(error: unknown): string {
  return errorMessage(error, "模型连接");
}

function isConflict(error: unknown): boolean {
  return error instanceof ModelApiError && error.code === "MODEL_CONFIG_CONFLICT";
}

export const useModelsStore = create<ModelsStore>()((set, get) => ({
  presets: [],
  authProviders: [],
  connections: [],
  selection: EMPTY_SELECTION,
  revision: "",
  loaded: false,
  loading: false,
  error: null,
  conflict: false,
  settingsOpen: false,

  async init() {
    if (get().loaded) return;
    await get().refresh();
    try {
      set({ presets: await listProviders() });
    } catch (error) {
      set({ error: describe(error) });
    }
    try {
      set({ authProviders: await listAuthProviders() });
    } catch {
      // 没有可登录的订阅来源时后端不挂这组接口：不是错误，界面按「只能填 Key」显示。
      set({ authProviders: [] });
    }
  },

  async refresh() {
    set({ loading: true });
    try {
      const config = await readConfig();
      apply(set, config);
      set({ loaded: true });
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      set({ loading: false });
    }
  },

  openSettings() {
    set({ settingsOpen: true, conflict: false });
  },

  closeSettings() {
    set({ settingsOpen: false, conflict: false });
  },

  async create(draft) {
    return write(set, () => createConnection(draft, get().revision));
  },

  async update(name, draft) {
    return write(set, () => updateConnection(name, draft, get().revision));
  },

  async remove(name) {
    return write(set, () => deleteConnection(name, get().revision));
  },

  async choose(connectionId, model, thinkingLevel) {
    return write(set, () => selectModel(connectionId, model, get().revision, thinkingLevel));
  },

  dismissError() {
    set({ error: null, conflict: false });
  },
}));

type SetState = (partial: Partial<ModelsStore>) => void;

function apply(set: SetState, config: ModelConfig): void {
  set({
    revision: config.revision,
    connections: config.connections,
    selection: config.selection,
    conflict: false,
    error: null,
  });
}

async function write(set: SetState, action: () => Promise<ModelConfig>): Promise<boolean> {
  set({ loading: true });
  try {
    apply(set, await action());
    return true;
  } catch (error) {
    set({ error: describe(error), conflict: isConflict(error) });
    return false;
  } finally {
    set({ loading: false });
  }
}

/** 当前选择的展示文本；未选择时给一句能指导下一步的话。 */
export function selectionLabel(
  selection: ModelSelection,
  connections: ModelConnection[],
): string {
  if (selection.source === "none" || !selection.connection_id) return "未连接模型";
  const connection = connections.find((item) => item.name === selection.connection_id);
  const title = connection?.display_name ?? selection.connection_id;
  return selection.model ? `${title} / ${selection.model}` : title;
}

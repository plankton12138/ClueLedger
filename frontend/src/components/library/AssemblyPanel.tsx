import { useEffect, useState } from "react";
import { fetchRevisions, type AssemblyBinding, type RevisionSummary, type ModuleKind } from "../../api/moduleLibrary";
import { bindingLabel, bindingProblems, useModuleLibraryStore } from "../../state/moduleLibrary";
import { useWorkspaceStore } from "../../state/workspace";
import { navigate } from "../../state/navigation";
import { Button } from "../ui/Button";
import { KIND_LABEL } from "./labels";

export default function AssemblyPanel({ onSelect }: { onSelect: (moduleId: string, revisionId?: string | null) => void }) {
  const store = useModuleLibraryStore();
  const assembly = store.assembly;
  const selectModule = onSelect;
  const [deriving, setDeriving] = useState(false);
  const [deriveName, setDeriveName] = useState("");
  const [replacementOptions, setReplacementOptions] = useState<Record<string, RevisionSummary[]>>(
    {},
  );
  const [replacementCursors, setReplacementCursors] = useState<Record<string, string | null>>({});
  const [versionsLoading, setVersionsLoading] = useState<Record<string, boolean>>({});
  const [replacements, setReplacements] = useState<Record<string, string>>({});

  useEffect(() => { setDeriving(false); setReplacements({}); setReplacementOptions({}); setReplacementCursors({}); }, [store.workspaceId]);
  async function loadVersions(moduleId: string, cursor?: string): Promise<void> {
    const workspaceId = store.workspaceId;
    setVersionsLoading(current => ({ ...current, [moduleId]: true }));
    try {
      const page = await fetchRevisions(moduleId, { limit: 50, cursor });
      if (useModuleLibraryStore.getState().workspaceId !== workspaceId) return;
      setReplacementOptions(current => ({ ...current, [moduleId]: [...(cursor ? current[moduleId] ?? [] : []), ...page.items].filter((item, index, rows) => rows.findIndex(other => other.revision_id === item.revision_id) === index) }));
      setReplacementCursors(current => ({ ...current, [moduleId]: page.next_cursor }));
    } finally { setVersionsLoading(current => ({ ...current, [moduleId]: false })); }
  }
  async function startDerive(): Promise<void> {
    setDeriving(true); setReplacements({});
    await Promise.allSettled((assembly?.bindings ?? []).map(binding => loadVersions(binding.module.module_id)));
  }

  function revisionChoices(binding: AssemblyBinding): RevisionSummary[] {
    const list = replacementOptions[binding.module.module_id] ?? [];
    if (list.some((item) => item.revision_id === binding.module.revision_id)) return list;
    return [
      {
        revision_id: binding.module.revision_id,
        parent: null,
        created_at: "",
        entry_ids: [],
      },
      ...list,
    ];
  }

  function chosenReplacements(): Record<string, string> {
    const bindings = assembly?.bindings ?? [];
    return Object.fromEntries(
      Object.entries(replacements).filter(([moduleId, revisionId]) => {
        const binding = bindings.find((item) => item.module.module_id === moduleId);
        return Boolean(binding && binding.module.revision_id !== revisionId);
      }),
    );
  }


  if (!assembly || assembly.bindings.length === 0) return <p className="ui-field-hint">作品还没有装入模块。</p>;
  return <div className="assembly-panel">

              <section className="flex-none border-b border-[#e2dbc6] px-3 py-2">
                <h3 className="text-sm font-bold text-[#6b6555]">
                  本作品已装（{assembly.bindings.length}）
                </h3>
                <ul className="mt-1 space-y-1">
                  {assembly.bindings.map((binding, index) => {
                    const problems = bindingProblems(binding);
                    return (
                      <li key={binding.id} className="flex items-center gap-1 text-sm">
                        <Button
                          className="min-w-0 flex-1 truncate text-left hover:underline"
                          onClick={() =>
                            selectModule(
                              binding.module.module_id,
                              binding.module.revision_id,
                            )
                          }
                        >
                          {bindingLabel(binding)}
                          <span className="ml-1 text-[12px] text-ui-muted">
                            {binding.kind ? KIND_LABEL[binding.kind as ModuleKind] : ""}
                          </span>
                        </Button>
                        {problems && <span className="text-[12px] text-amber">{problems}</span>}
                        <span className="flex flex-none items-center">
                          <Button
                            aria-label={`上移 ${bindingLabel(binding)}`}
                            className="px-0.5 text-[12px] text-ui-muted disabled:opacity-30"
                            disabled={store.busy || index === 0}
                            onClick={() => void store.reorder(binding.id, -1)}
                          >
                            ↑
                          </Button>
                          <Button
                            aria-label={`下移 ${bindingLabel(binding)}`}
                            className="px-0.5 text-[12px] text-ui-muted disabled:opacity-30"
                            disabled={store.busy || index === assembly!.bindings.length - 1}
                            onClick={() => void store.reorder(binding.id, 1)}
                          >
                            ↓
                          </Button>
                        </span>
                        <Button
                          className="text-[12px] text-ui-muted underline"
                          disabled={store.busy} onClick={() => void store.unbind(binding.id)}
                        >
                          卸下
                        </Button>
                      </li>
                    );
                  })}
                </ul>
                <div className="mt-2 flex flex-none items-center gap-1">
                  {deriving ? (
                    <div className="flex w-full flex-col gap-1">
                      <div className="flex items-center gap-1">
                        <input
                          aria-label="新作品名称"
                          className="well-input min-w-0 flex-1 px-2 py-1 text-sm"
                          value={deriveName}
                          placeholder="新作品目录名"
                          onChange={(event) => setDeriveName(event.target.value)}
                        />
                        <Button
                          className="btn btn-gold px-2 py-1 text-sm"
                          disabled={store.busy || !deriveName.trim()}
                          onClick={() => {
                            void store.derive(deriveName, chosenReplacements()).then(
                              async (created) => {
                                if (!created) return;
                                setDeriving(false);
                                setDeriveName("");
                                setReplacements({});
                                // 登记列表会多一条：先刷新，否则顶栏仍把新作品标成「目录已不在」。
                                await useWorkspaceStore.getState().refreshBindings();
                                await useWorkspaceStore.getState().selectWorkspace(created.id);
                                navigate({ kind: "writing", workspaceId: created.id });
                                store.closeDrawer();
                              },
                            );
                          }}
                        >
                          创建
                        </Button>
                        <Button
                          className="text-[12px] text-ui-muted underline"
                          onClick={() => {
                            setDeriving(false);
                            setReplacements({});
                          }}
                        >
                          取消
                        </Button>
                      </div>
                      <p className="text-[12px] text-ui-muted">
                        默认沿用当前版本；需要换版本时在下面选择。新作品只有装配，正文与观察从空白开始。
                      </p>
                      {assembly.bindings.map((binding) => (
                        <label key={binding.id} className="flex items-center gap-2 text-[12px]">
                          <span className="w-24 flex-none truncate">{bindingLabel(binding)}</span>
                          <select
                            aria-label={`${bindingLabel(binding)} 派生版本`}
                            className="well-input min-w-0 flex-1 px-1 py-0.5 text-[12px]"
                            value={
                              replacements[binding.module.module_id] ??
                              binding.module.revision_id
                            }
                            onChange={(event) =>
                              setReplacements((current) => ({
                                ...current,
                                [binding.module.module_id]: event.target.value,
                              }))
                            }
                          >
                            {revisionChoices(binding).map((revision) => (
                              <option key={revision.revision_id} value={revision.revision_id}>
                                {revision.revision_id === binding.module.revision_id
                                  ? "当前使用"
                                  : revision.created_at
                                    ? revision.created_at.slice(0, 10)
                                    : revision.revision_id}
                              </option>
                            ))}
                          </select>
                          {replacementCursors[binding.module.module_id] && <Button size="compact" variant="quiet" busy={versionsLoading[binding.module.module_id]} onClick={() => void loadVersions(binding.module.module_id, replacementCursors[binding.module.module_id]!).catch(() => useWorkspaceStore.getState().reportError("[MODULE_REVISIONS_FAILED] 派生作品：版本列表未能读取。建议：重试更多版本，或沿用当前版本。"))}>更多版本</Button>}
                        </label>
                      ))}
                    </div>
                  ) : (
                    <Button
                      variant="quiet"
                      onClick={() => void startDerive()}
                    >
                      用当前组合新建作品
                    </Button>
                  )}
                </div>
              </section>

  </div>;
}

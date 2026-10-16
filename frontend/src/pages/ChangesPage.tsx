import { useEffect, useState } from "react";
import {
  changeAction,
  fetchChangeDetail,
  openChangeSet,
  type ChangeDetailWire,
  type ImpactItem,
  type PatchHunk,
  type RevisionStep,
} from "../api/v2";
import { useV2Store, message, isDirty } from "../state/v2";
import {
  Panel,
  Empty,
  Notice,
  ErrorText,
  Pill,
  titleOf,
  display,
  changeLabels,
  fieldLabels,
  type View,
} from "../components/Workbench";

export default function ChangesPage({
  review = false,
  onNavigate,
}: {
  review?: boolean;
  onNavigate(view: View): void;
}) {
  const pid = useV2Store((s) => s.currentId),
    changes = useV2Store((s) => s.changes),
    selectedId = useV2Store((s) => s.selectedChangeId);
  const artifacts = useV2Store((s) => s.storyArtifacts),
    chapters = useV2Store((s) => s.chapters),
    focus = useV2Store((s) => s.focusArtifactId);
  const [detail, setDetail] = useState<ChangeDetailWire | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [intent, setIntent] = useState(""),
    [seed, setSeed] = useState(focus ?? ""),
    [field, setField] = useState(""),
    [value, setValue] = useState("");
  const [scope, setScope] = useState<string[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [riskReasons, setRisks] = useState<Record<string, string>>({});
  const [manualTarget, setManualTarget] = useState(""),
    [manualField, setManualField] = useState(""),
    [manualValue, setManualValue] = useState("");
  const [manualPatches, setManualPatches] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [proof, setProof] = useState("");
  const [newOpen, setNewOpen] = useState(
    changes.length === 0 || focus !== null,
  );
  const [agentPending, setAgentPending] = useState(false);
  const activeId =
    selectedId ??
    changes.find((c) => !review || c.status === "review")?.change_set_id ??
    changes[0]?.change_set_id;
  useEffect(() => {
    setDetail(null);
    setSelected([]);
    setRisks({});
    setManualPatches({});
    setProof("");
    if (!pid || !activeId) return;
    let alive = true;
    void fetchChangeDetail(pid, activeId)
      .then((d) => {
        if (alive) {
          setDetail(d);
          setManualPatches(
            Object.fromEntries(
              (d.change_set.seed_changes ?? [])
                .filter((seed) => seed.proposed_content)
                .map((seed) => [seed.artifact_id, seed.proposed_content!]),
            ),
          );
        }
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [pid, activeId, review]);
  const currentSeed = artifacts.find((a) => a.artifact_id === seed);
  const scalarFields = currentSeed
    ? Object.entries(currentSeed.content)
        .filter(
          ([k, v]) =>
            typeof v === "string" &&
            !["schema_version", "artifact_id"].includes(k),
        )
        .map(([k]) => k)
    : [];
  const names = Object.fromEntries([
    ...artifacts.map((a) => [a.artifact_id, titleOf(a)]),
    ...chapters.map((c) => [c.chapter_id, c.title]),
    ...chapters.flatMap((c) =>
      c.units.map((u, i) => [u.unit_id, `${c.title} · 第${i + 1}段`]),
    ),
  ]);
  const object = (kind: string) =>
    detail?.objects.find((o) => o.kind === kind)?.content;
  const items = (object("changes.impact_report")?.items ?? []) as ImpactItem[];
  const plan = object("changes.revision_plan"),
    steps = (plan?.steps ?? []) as RevisionStep[];
  const hunks = (object("changes.patch_set")?.hunks ?? []) as PatchHunk[];
  const status = detail?.change_set.status;
  useEffect(() => {
    if (!agentPending || !pid || !activeId) return;
    let alive = true;
    const timer = window.setInterval(() => {
      void fetchChangeDetail(pid, activeId)
        .then((next) => {
          if (!alive) return;
          setDetail(next);
          if (
            ["review", "failed", "committed"].includes(next.change_set.status)
          )
            setAgentPending(false);
        })
        .catch((e) => {
          if (alive) setError(message(e));
        });
    }, 1800);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [agentPending, pid, activeId]);
  async function load() {
    if (!pid || !activeId) return;
    setDetail(await fetchChangeDetail(pid, activeId));
    await useV2Store.getState().refreshChanges();
  }
  async function perform(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function action(
    name: Parameters<typeof changeAction>[2],
    extra: Record<string, unknown> = {},
  ) {
    if (!pid || !detail) return;
    const result = await changeAction(
      pid,
      detail.change_set.change_set_id,
      name,
      detail.head_commit_id,
      extra,
    );
    if (name === "execute-agent") {
      setAgentPending(true);
      setProof(
        "修订任务已提交，完成后将更新审阅入口。可在创作台查看任务过程或重试原因。",
      );
    }
    await load();
    await Promise.all([
      useV2Store.getState().refreshManuscript(),
      useV2Store.getState().refreshStory(),
    ]);
    if (
      result &&
      typeof result === "object" &&
      "status" in result &&
      result.status === "failed"
    ) {
      throw new Error(
        String(
          (result as { error_message?: string }).error_message ||
            "修订未完成，请检查任务原因后重试。",
        ),
      );
    }
  }
  const allowedTargets = [
    ...new Set(steps.flatMap((s) => s.target_artifact_ids)),
  ];
  const manualArtifact = artifacts.find((a) => a.artifact_id === manualTarget);
  const manualFields = manualArtifact
    ? Object.entries(manualArtifact.content)
        .filter(([, v]) => typeof v === "string")
        .map(([k]) => k)
    : [];
  return (
    <main className="page-grid" aria-label={review ? "修订审阅" : "变更影响"}>
      <aside className="space-y-4">
        <Panel
          title={review ? "修订与审阅队列" : "变更集"}
          meta={`${changes.length} 项`}
        >
          {changes.map((c) => (
            <button
              key={c.change_set_id}
              disabled={busy}
              className={`asset-row block w-full p-4 text-left ${activeId === c.change_set_id ? "sel" : ""}`}
              onClick={() =>
                useV2Store.setState({ selectedChangeId: c.change_set_id })
              }
            >
              <Pill tone={c.status === "committed" ? "green" : "amber"}>
                {changeLabels[c.status] ?? c.status}
              </Pill>
              <p className="mt-2 text-xs leading-6">{c.intent}</p>
            </button>
          ))}
          {!changes.length && (
            <Empty>还没有变更集。选择故事对象，说明你希望改变什么。</Empty>
          )}
        </Panel>
        <Notice>
          <strong>先批准范围，再执行修订</strong>
          <br />
          候选在分支上试改；只有你选中的差异会进入正式稿。
        </Notice>
      </aside>
      <section className="min-w-0 space-y-4">
        <ErrorText>{error}</ErrorText>
        {proof && (
          <p role="status" className="notice break-all p-4 text-sm">
            {proof}
          </p>
        )}
        {!review && (
          <Panel title="发起事实变化" meta="保留原内容与修改原因">
            <button
              className="w-full p-3 text-left text-xs text-blue"
              onClick={() => setNewOpen(!newOpen)}
              aria-expanded={newOpen}
            >
              {newOpen ? "收起新变更表单" : "＋ 发起新变更"}
            </button>
            {newOpen && (
              <form
                className="grid gap-3 p-4 sm:grid-cols-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!pid) return;
                  void perform(async () => {
                    const base = useV2Store.getState().head;
                    if (!base)
                      throw new Error(
                        "项目尚无可修改版本，请先接受规划或保存章节。",
                      );
                    const patches =
                      currentSeed && field
                        ? { [seed]: { ...currentSeed.content, [field]: value } }
                        : {};
                    const opened = await openChangeSet(
                      pid,
                      intent,
                      base,
                      [seed],
                      { roots: scope },
                      patches,
                    );
                    useV2Store.setState({
                      selectedChangeId: opened.change_set_id,
                    });
                    await useV2Store.getState().refreshChanges();
                    setIntent("");
                    setNewOpen(false);
                  });
                }}
              >
                <label className="field sm:col-span-2">
                  为什么修改
                  <textarea
                    aria-label="修改原因"
                    className="well-input min-h-20"
                    value={intent}
                    onChange={(e) => setIntent(e.target.value)}
                    placeholder="例如：让协助者成为幕后操纵者，检查相关线索与章节。"
                    required
                  />
                </label>
                <label className="field">
                  变化对象
                  <select
                    aria-label="变化对象"
                    className="well-input"
                    value={seed}
                    onChange={(e) => {
                      setSeed(e.target.value);
                      setField("");
                      setValue("");
                    }}
                    required
                  >
                    <option value="">选择已接受的故事对象</option>
                    {artifacts.map((a) => (
                      <option value={a.artifact_id} key={a.artifact_id}>
                        {titleOf(a)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  要修改的内容
                  <select
                    aria-label="修改字段"
                    className="well-input"
                    value={field}
                    onChange={(e) => {
                      setField(e.target.value);
                      setValue(
                        String(currentSeed?.content[e.target.value] ?? ""),
                      );
                    }}
                  >
                    <option value="">仅分析影响</option>
                    {scalarFields.map((k) => (
                      <option key={k} value={k}>
                        {fieldLabels[k] ?? k}
                      </option>
                    ))}
                  </select>
                </label>
                {field && (
                  <label className="field sm:col-span-2">
                    期望的新内容
                    <textarea
                      aria-label="期望的新内容"
                      className="well-input min-h-20"
                      value={value}
                      onChange={(e) => setValue(e.target.value)}
                      required
                    />
                  </label>
                )}
                <fieldset className="sm:col-span-2">
                  <legend className="mb-2 text-xs text-sub">
                    限定章节范围（不选则检查全书）
                  </legend>
                  <div className="flex flex-wrap gap-3">
                    {chapters.map((c) => (
                      <label
                        className="flex items-center gap-1 text-xs"
                        key={c.chapter_id}
                      >
                        <input
                          type="checkbox"
                          checked={scope.includes(c.chapter_id)}
                          onChange={(e) =>
                            setScope(
                              e.target.checked
                                ? [...scope, c.chapter_id]
                                : scope.filter((id) => id !== c.chapter_id),
                            )
                          }
                        />
                        {c.title}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <button
                  disabled={busy || !pid || !seed || !intent.trim()}
                  className="btn justify-self-start px-4 py-2 text-xs"
                >
                  建立变更集
                </button>
              </form>
            )}
          </Panel>
        )}
        {detail ? (
          <Panel
            title={review ? "局部补丁 · 作者审阅" : "变更详情与影响路径"}
            meta={changeLabels[status ?? ""]}
          >
            <div className="space-y-4 p-5">
              <h1 className="font-serif text-xl font-bold text-fgStrong">
                {detail.change_set.intent}
              </h1>
              <p className="break-all text-[11px] text-sub">
                分析依据：{detail.change_set.base_commit_id}
              </p>
              <div className="flex flex-wrap gap-2">
                {status === "opened" && (
                  <button
                    className="btn px-3 py-2 text-xs"
                    disabled={busy}
                    onClick={() => void perform(() => action("analyze"))}
                  >
                    分析影响
                  </button>
                )}
                {status === "analyzed" && !plan && (
                  <button
                    className="btn px-3 py-2 text-xs"
                    disabled={busy}
                    onClick={() => void perform(() => action("plan"))}
                  >
                    制定修订计划
                  </button>
                )}
                {status === "analyzed" && plan && (
                  <button
                    className="btn btn-gold px-3 py-2 text-xs"
                    disabled={busy}
                    onClick={() => void perform(() => action("approve"))}
                  >
                    批准所示范围与计划
                  </button>
                )}
                {["approved", "executing"].includes(status ?? "") && (
                  <>
                    <button
                      className="btn px-3 py-2 text-xs"
                      disabled={busy}
                      onClick={() =>
                        void perform(() => action("execute-agent"))
                      }
                    >
                      由 Agent 执行已批准修订
                    </button>
                    <button
                      className="btn btn-gray px-3 py-2 text-xs"
                      disabled={busy}
                      onClick={() =>
                        void perform(() =>
                          action("execute", { patches: manualPatches }),
                        )
                      }
                    >
                      在分支执行修订
                    </button>
                  </>
                )}
                {status === "review" && (
                  <>
                    <button
                      className="btn px-3 py-2 text-xs"
                      disabled={busy}
                      onClick={() => void perform(() => action("review"))}
                    >
                      生成审阅差异
                    </button>
                    {!review && (
                      <button
                        className="btn btn-gray px-3 py-2 text-xs"
                        onClick={() => onNavigate("review")}
                      >
                        进入修订审阅 →
                      </button>
                    )}
                  </>
                )}
                <button
                  className="btn btn-gray px-3 py-2 text-xs"
                  disabled={busy}
                  onClick={() => void perform(load)}
                >
                  刷新详情
                </button>
              </div>
              {!review && (
                <>
                  <div className="grid grid-cols-3 gap-3">
                    {[
                      {
                        label: "必须修复",
                        n: items.filter((i) => i.severity === "must_fix")
                          .length,
                      },
                      {
                        label: "建议修复",
                        n: items.filter((i) => i.severity === "should_fix")
                          .length,
                      },
                      {
                        label: "待复核",
                        n: items.filter((i) => i.severity === "review").length,
                      },
                    ].map((m) => (
                      <div key={m.label} className="metric p-3">
                        <b className="text-2xl">{m.n}</b>
                        <p className="text-[11px] text-sub">{m.label}</p>
                      </div>
                    ))}
                  </div>
                  {items.map((item, i) => (
                    <article key={i} className="flow-node space-y-3 p-4">
                      <div className="flex flex-wrap justify-between gap-2">
                        <b className="text-sm">
                          {names[item.target_artifact_id] ??
                            item.target_artifact_id}
                        </b>
                        <Pill
                          tone={item.severity === "must_fix" ? "red" : "amber"}
                        >
                          {(
                            {
                              must_fix: "必须修复",
                              should_fix: "建议修复",
                              review: "待复核",
                              unaffected: "无须修改",
                            } as Record<string, string>
                          )[item.severity] ?? item.severity}
                        </Pill>
                      </div>
                      <p className="text-xs leading-6">{item.reason}</p>
                      <div className="link-path ml-2 space-y-3 pl-5 text-xs text-sub">
                        {item.path.map((p, n) => (
                          <div key={n}>
                            {names[p.source] ?? p.source}
                            <span className="mx-2 text-blue">
                              → {p.relation_type} →
                            </span>
                            {names[p.target] ?? p.target}
                          </div>
                        ))}
                      </div>
                    </article>
                  ))}
                  {items.length === 0 && status !== "opened" && (
                    <Empty>当前报告没有受影响对象。空结果也是有效结论。</Empty>
                  )}
                </>
              )}
              {review && (
                <>
                  <p className="text-xs text-sub">
                    逐项选择要接受的差异。未选中的修改保留在分支，不进入正式稿。
                  </p>
                  {hunks.map((h) => (
                    <article
                      key={h.hunk_id}
                      className="flow-node overflow-hidden"
                    >
                      <label className="flex items-center gap-2 border-b border-line bg-blue-bg p-3 text-xs">
                        <input
                          aria-label={`接受 ${names[h.artifact_id] ?? h.artifact_id} ${fieldLabels[h.field] ?? h.field}`}
                          type="checkbox"
                          checked={selected.includes(h.hunk_id)}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, h.hunk_id]
                                : selected.filter((id) => id !== h.hunk_id),
                            )
                          }
                          disabled={busy || status !== "review"}
                        />
                        <b>{names[h.artifact_id] ?? h.artifact_id}</b>
                        <span className="text-sub">
                          {fieldLabels[h.field] ?? h.field}
                        </span>
                      </label>
                      <div className="grid sm:grid-cols-2">
                        <div className="p-4">
                          <p className="mb-2 text-[10px] text-sub">修改前</p>
                          <p className="diff-del whitespace-pre-wrap break-words text-xs leading-7">
                            {display(h.old, names)}
                          </p>
                        </div>
                        <div className="p-4">
                          <p className="mb-2 text-[10px] text-sub">候选修改</p>
                          <p className="diff-ins whitespace-pre-wrap break-words text-xs leading-7">
                            {display(h.new, names)}
                          </p>
                        </div>
                      </div>
                    </article>
                  ))}
                  {!hunks.length && (
                    <Empty>
                      尚无已冻结的差异。请先完成批准与分支执行，再生成审阅差异。
                    </Empty>
                  )}
                  {items
                    .filter(
                      (i) =>
                        i.severity === "must_fix" && i.status !== "resolved",
                    )
                    .map((i) => (
                      <label className="field" key={i.target_artifact_id}>
                        若保留此风险，请说明原因：
                        {names[i.target_artifact_id] ?? i.target_artifact_id}
                        <input
                          className="well-input"
                          value={riskReasons[i.target_artifact_id] ?? ""}
                          onChange={(e) =>
                            setRisks({
                              ...riskReasons,
                              [i.target_artifact_id]: e.target.value,
                            })
                          }
                        />
                      </label>
                    ))}
                  {status === "review" && (
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className="text-xs text-sub">
                        已选择 {selected.length} / {hunks.length} 项
                      </span>
                      <button
                        className="btn btn-gold px-4 py-2 text-xs"
                        disabled={
                          busy ||
                          !object("changes.patch_set") ||
                          isDirty(useV2Store.getState().chapter)
                        }
                        onClick={() =>
                          void perform(async () => {
                            await action("merge", {
                              selected,
                              risks: Object.fromEntries(
                                Object.entries(riskReasons).filter(([, v]) =>
                                  v.trim(),
                                ),
                              ),
                            });
                            setProof(
                              "所选差异已提交。未接受的修改保留为残余影响，可重新分析。",
                            );
                            setSelected([]);
                          })
                        }
                      >
                        提交所选差异
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </Panel>
        ) : (
          <Panel title={review ? "修订审阅" : "变更详情"}>
            <Empty>选择一个变更集，查看修改依据和下一步。</Empty>
          </Panel>
        )}
      </section>
      <aside className="space-y-4">
        <Panel title="范围与批准依据">
          {detail ? (
            <div className="space-y-4 p-4 text-xs leading-6">
              <p>原因：{detail.change_set.intent}</p>
              <p>
                预计修订 {steps.length} 组，影响 {items.length} 项。
              </p>
              {steps.map((step) => (
                <div className="flow-node p-3" key={step.step_id}>
                  <b>{step.title}</b>
                  <p className="mt-1 text-sub">
                    {step.target_artifact_ids
                      .map((id) => names[id] ?? id)
                      .join("、")}
                  </p>
                </div>
              ))}
              {plan && <p>接受风险：{display(plan.risk_acceptances ?? [])}</p>}
            </div>
          ) : (
            <Empty>分析与计划生成后，在这里核对范围。</Empty>
          )}
        </Panel>
        {!review && ["approved", "executing"].includes(status ?? "") && (
          <Panel title="补充人工修订">
            <div className="space-y-3 p-4">
              <label className="field">
                计划中的故事对象
                <select
                  className="well-input"
                  value={manualTarget}
                  onChange={(e) => {
                    setManualTarget(e.target.value);
                    setManualField("");
                  }}
                >
                  <option value="">选择对象</option>
                  {allowedTargets
                    .filter((id) => artifacts.some((a) => a.artifact_id === id))
                    .map((id) => (
                      <option key={id} value={id}>
                        {names[id] ?? id}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field">
                内容字段
                <select
                  className="well-input"
                  value={manualField}
                  onChange={(e) => {
                    setManualField(e.target.value);
                    setManualValue(
                      String(manualArtifact?.content[e.target.value] ?? ""),
                    );
                  }}
                >
                  <option value="">选择字段</option>
                  {manualFields.map((k) => (
                    <option key={k} value={k}>
                      {fieldLabels[k] ?? k}
                    </option>
                  ))}
                </select>
              </label>
              {manualField && (
                <textarea
                  aria-label="人工修订内容"
                  className="well-input min-h-24 w-full p-2 text-xs"
                  value={manualValue}
                  onChange={(e) => setManualValue(e.target.value)}
                />
              )}
              <button
                className="btn btn-gray px-3 py-2 text-xs"
                disabled={!manualArtifact || !manualField}
                onClick={() => {
                  if (manualArtifact)
                    setManualPatches({
                      ...manualPatches,
                      [manualTarget]: {
                        ...(manualPatches[manualTarget] ??
                          manualArtifact.content),
                        [manualField]: manualValue,
                      },
                    });
                }}
              >
                加入本次分支修订
              </button>
              <p className="text-xs text-sub">
                已补充 {Object.keys(manualPatches).length}{" "}
                个对象。执行时仅使用批准范围内的内容。
              </p>
            </div>
          </Panel>
        )}
        <Notice>
          版本变化时，批准和合并可能被拒绝。错误会保留你的选择；刷新并核对新依据后再继续。
        </Notice>
      </aside>
    </main>
  );
}

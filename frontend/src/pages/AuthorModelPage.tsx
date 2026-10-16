import { useEffect, useState } from "react";
import {
  declarePreference,
  revokePreference,
  fetchObservations,
  fetchPreferenceEvidence,
  type AuthorObservationWire,
} from "../api/v2";
import { useV2Store, message } from "../state/v2";
import { Panel, Empty, Notice, ErrorText, Pill } from "../components/Workbench";
const categories: Record<string, string> = {
  language_style: "语言风格",
  fact_revision: "事实修订",
  structure_revision: "结构修订",
  format: "格式与纠错",
  collaboration_feedback: "协作反馈",
};
const scopes: Record<string, string> = {
  global: "全局",
  project: "本作品",
  arc: "故事弧",
  chapter: "章节",
};
export default function AuthorModelPage() {
  const pid = useV2Store((s) => s.currentId),
    preferences = useV2Store((s) => s.preferences);
  const [filter, setFilter] = useState("all"),
    [selected, setSelected] = useState<string | null>(null),
    [observations, setObservations] = useState<AuthorObservationWire[]>([]);
  const [evidence, setEvidence] = useState<AuthorObservationWire[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const [statement, setStatement] = useState(""),
    [category, setCategory] = useState("language_style"),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  async function decide(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      await Promise.all([
        useV2Store.getState().refreshPreferences(),
        useV2Store.getState().refreshManuscript(),
      ]);
      setStatement("");
      setReason("");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!pid) return;
    let alive = true;
    void fetchObservations(pid)
      .then((r) => {
        if (alive) setObservations(r.observations);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [pid, preferences]);
  useEffect(() => {
    setEvidence([]);
    if (!pid || !selected) return;
    let alive = true;
    setLoading(true);
    void fetchPreferenceEvidence(pid, selected)
      .then((r) => {
        if (alive) setEvidence(r.observations);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [pid, selected]);
  const visible = preferences.filter(
    (p) => filter === "all" || p.status === filter,
  );
  const current = preferences.find((p) => p.preference_id === selected);
  return (
    <main className="page-grid" aria-label="作者模型">
      <aside className="space-y-4">
        <Panel title="作者模型" meta="证据可追溯">
          {[
            { id: "all", title: "全部写作偏好" },
            { id: "active", title: "已生效" },
            { id: "candidate", title: "候选偏好" },
            { id: "revoked", title: "已撤销" },
          ].map((f) => (
            <button
              key={f.id}
              className={`asset-row w-full justify-between p-4 text-xs ${filter === f.id ? "sel" : ""}`}
              onClick={() => setFilter(f.id)}
            >
              <b>{f.title}</b>
              <span className="count-pill px-2">
                {
                  preferences.filter((p) => f.id === "all" || p.status === f.id)
                    .length
                }
              </span>
            </button>
          ))}
        </Panel>
        <Notice>
          <strong>从真实修改中学习</strong>
          <br />
          一次编辑是事实，分类是解释，偏好是待验证的归纳。事实与数字修改不会直接成为文风规则。
        </Notice>
      </aside>
      <section className="min-w-0 space-y-4">
        <ErrorText>{error}</ErrorText>
        <div className="hero p-7">
          <p className="text-[10px] tracking-widest text-white/70">
            AUTHOR / EVIDENCE
          </p>
          <h1 className="mt-3 font-serif text-2xl font-bold">
            以真实修改形成的作者模型
          </h1>
          <p className="mt-3 text-xs text-white/80">
            {observations.length} 项编辑观察 ·{" "}
            {preferences.filter((p) => p.status === "active").length} 条生效偏好
            · 每条结论保留来源
          </p>
        </div>
        <Panel title="写作偏好" meta={`${visible.length} 条`}>
          {visible.map((p) => (
            <button
              className={`asset-row block w-full p-5 text-left ${selected === p.preference_id ? "sel" : ""}`}
              key={p.preference_id}
              onClick={() => setSelected(p.preference_id)}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <b className="text-sm">{p.statement}</b>
                <Pill
                  tone={
                    p.status === "active"
                      ? "green"
                      : p.status === "revoked"
                        ? "red"
                        : "amber"
                  }
                >
                  {p.status === "active"
                    ? "已生效"
                    : p.status === "revoked"
                      ? "已撤销"
                      : "候选"}
                </Pill>
              </div>
              <p className="mt-2 text-xs text-sub">
                {categories[p.category] ?? p.category} ·{" "}
                {scopes[p.scope_kind] ?? p.scope_kind} ·{" "}
                {p.origin === "explicit" ? "作者声明" : "观察归纳"} ·{" "}
                {p.evidence.length} 项来源
              </p>
              {p.reason && <p className="mt-2 text-xs leading-6">{p.reason}</p>}
            </button>
          ))}
          {!visible.length && (
            <Empty>
              当前分类没有偏好。先在创作台真实编辑并保存正文，系统会在后台建立观察与候选。
            </Empty>
          )}
        </Panel>
        <Panel title="作者明确声明">
          <form
            className="space-y-3 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (pid)
                void decide(() =>
                  declarePreference(
                    pid,
                    useV2Store.getState().head,
                    statement.trim(),
                    category,
                    pid,
                  ),
                );
            }}
          >
            <label className="field">
              偏好类别
              <select
                className="well-input"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {Object.entries(categories).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              写作偏好
              <textarea
                aria-label="写作偏好声明"
                className="well-input min-h-20"
                value={statement}
                onChange={(e) => setStatement(e.target.value)}
                placeholder="例如：用动作表达情绪，少用解释性独白。"
                required
              />
            </label>
            <button
              className="btn px-4 py-2 text-xs"
              disabled={busy || !pid || !statement.trim()}
            >
              声明并生效
            </button>
            <p className="text-xs text-sub">
              同一类别与作用域的新声明会取代旧声明，历史保留。
            </p>
          </form>
        </Panel>
        <Panel title="最近编辑观察" meta={`${observations.length} 项`}>
          {observations
            .slice(-8)
            .reverse()
            .map((o) => (
              <div
                className="space-y-2 border-b border-line p-4 text-xs"
                key={o.observation_id}
              >
                <div className="flex justify-between gap-3">
                  <b>
                    {categories[o.classification.category ?? ""] ?? "尚待判断"}
                  </b>
                  <span className="text-sub">
                    置信度 {Math.round(o.classification.confidence * 100)}%
                  </span>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <p className="diff-del whitespace-pre-wrap p-2 leading-6">
                    {o.edit.before || "（原为空）"}
                  </p>
                  <p className="diff-ins whitespace-pre-wrap p-2 leading-6">
                    {o.edit.after || "（已删除）"}
                  </p>
                </div>
                <p className="break-all text-sub">来源版本：{o.commit_id}</p>
              </div>
            ))}
          {!observations.length && <Empty>尚无真实编辑观察。</Empty>}
        </Panel>
      </section>
      <aside className="space-y-4">
        <Panel title="偏好依据">
          {current ? (
            <div className="space-y-3 p-4 text-xs leading-6">
              <h2 className="font-bold">{current.statement}</h2>
              <p>
                作用范围：{scopes[current.scope_kind] ?? current.scope_kind}{" "}
                {current.scope_target}
              </p>
              {loading && <p>读取证据中…</p>}
              {evidence.map((o) => (
                <div className="flow-node space-y-2 p-3" key={o.observation_id}>
                  <b>{categories[o.classification.category ?? ""] ?? "观察"}</b>
                  <blockquote className="diff-ins whitespace-pre-wrap p-2">
                    {o.edit.after}
                  </blockquote>
                  <p>置信度 {Math.round(o.classification.confidence * 100)}%</p>
                  <p className="break-all text-sub">{o.commit_id}</p>
                </div>
              ))}
              {current.status !== "revoked" && (
                <form
                  className="space-y-3 border-t border-line pt-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (pid && selected)
                      void decide(() =>
                        revokePreference(
                          pid,
                          selected,
                          useV2Store.getState().head,
                          reason.trim(),
                        ),
                      );
                  }}
                >
                  <label className="field">
                    撤销原因
                    <input
                      aria-label="撤销偏好原因"
                      className="well-input"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="说明这条偏好为何不再适用"
                      required
                    />
                  </label>
                  <button
                    className="btn btn-gold w-full py-2"
                    disabled={busy || !reason.trim()}
                  >
                    撤销这条偏好
                  </button>
                </form>
              )}
              {!loading && !evidence.length && (
                <p className="text-sub">
                  此项没有可展示的编辑观察；来源可能为作者显式声明。
                </p>
              )}
            </div>
          ) : (
            <Empty>点击一条偏好，查看修改证据与适用范围。</Empty>
          )}
        </Panel>
        <Notice>
          只有生效偏好参与生成上下文。候选与撤销状态均原样展示，以服务端已提交结果为准。
        </Notice>
      </aside>
    </main>
  );
}

import { useState } from "react";
import type { AuditResult, AuditFinding } from "../api/v2";
import { useV2Store } from "../state/v2";
import {
  Panel,
  Empty,
  Notice,
  Pill,
  display,
  type View,
} from "../components/Workbench";
const reports = new Map<string, AuditResult>();
export default function AuditPage({
  onNavigate,
}: {
  onNavigate(view: View): void;
}) {
  const pid = useV2Store((s) => s.currentId);
  const [report] = useState<AuditResult | null>(
      pid ? (reports.get(pid) ?? null) : null,
    );
  const [filter, setFilter] = useState("all"),
    [selected, setSelected] = useState<AuditFinding | null>(null);
  const findings = report?.findings ?? [];
  const filtered = findings.filter(
    (f) => filter === "all" || f.status === filter,
  );
  const current = selected ?? filtered[0];
  return (
    <main className="page-grid" aria-label="悬疑审计">
      <aside className="space-y-4">
        <Panel title="审计视图">
          <div className="p-3">
            {[
              { key: "all", title: "全部发现" },
              { key: "open", title: "尚待处理" },
              { key: "fixed", title: "已有修复提交" },
              { key: "accepted_risk", title: "已接受风险" },
            ].map((f) => (
              <button
                className={`asset-row w-full justify-between p-3 text-xs ${filter === f.key ? "sel" : ""}`}
                key={f.key}
                onClick={() => {
                  setFilter(f.key);
                  setSelected(null);
                }}
              >
                <b>{f.title}</b>
                <span>
                  {
                    findings.filter(
                      (x) => f.key === "all" || x.status === f.key,
                    ).length
                  }
                </span>
              </button>
            ))}
          </div>
        </Panel>
        <Notice>
          <strong>本版不提供启动</strong>
          <br />
          独立一致性审计属后续版本能力（V03-8.6 起旧入口统一拒绝）。写作台的「正文证据」与「披露边界」照常工作；历史报告仍可从对应版本回看。
        </Notice>
        <Notice>
          <strong>审计报告绑定故事版本</strong>
          <br />
          修改故事后请重新检查。发现被标记为修复，必须有实际内容提交作为依据。
        </Notice>
      </aside>
      <section className="min-w-0 space-y-4">
        <div className="hero p-6">
          <p className="text-[10px] tracking-widest text-white/70">
            STORY / CONSISTENCY
          </p>
          <h1 className="mt-3 font-serif text-2xl font-bold">
            悬疑与三层事实检查
          </h1>
          <p className="mt-3 text-xs text-white/80">
            真实发生、角色所知与读者所见，应该有可解释的联系。
          </p>
          <div className="mt-5 flex gap-5 text-sm">
            <span>
              {report
                ? findings.filter((f) => f.status === "open").length
                : "—"}{" "}
              项待处理
            </span>
            <span>
              {report
                ? findings.filter((f) => f.status === "fixed").length
                : "—"}{" "}
              项已修复
            </span>
          </div>
        </div>
        <Panel
          title="一致性发现"
          meta={report ? `共 ${findings.length} 项` : "本版不提供启动"}
        >
          {filtered.map((f, i) => (
            <button
              key={String(f.finding_id ?? i)}
              className={`asset-row block w-full p-5 text-left ${current === f ? "sel" : ""}`}
              onClick={() => setSelected(f)}
            >
              <div className="flex items-start justify-between gap-3">
                <b className="text-sm">
                  {String(f.why ?? f.summary ?? f.code ?? "一致性发现")}
                </b>
                <Pill
                  tone={
                    f.status === "fixed"
                      ? "green"
                      : f.severity === "error" || f.severity === "must_fix"
                        ? "red"
                        : "amber"
                  }
                >
                  {f.status === "fixed"
                    ? "已修复"
                    : f.severity === "error"
                      ? "错误"
                      : "待处理"}
                </Pill>
              </div>
              <p className="mt-2 text-xs leading-6 text-sub">
                {String(f.fix ?? f.detail ?? "")}
              </p>
            </button>
          ))}
          {filtered.length === 0 && (
            <Empty>
              {report
                ? "本次报告在当前分类中没有发现。零发现表示本轮规则未发现问题，不代表模型已证明整部作品无误。"
                : "本版不提供独立一致性审计的启动；旧报告可在对应历史版本中回看。写作台的「正文证据」与「披露边界」照常工作。"}
            </Empty>
          )}
        </Panel>
      </section>
      <aside className="space-y-4">
        <Panel title="证据与处理方向">
          {current ? (
            <div className="space-y-4 p-4 text-xs leading-6">
              <Pill>{String(current.code ?? "Finding")}</Pill>
              <p>{String(current.why ?? current.summary ?? "")}</p>
              <div className="link-path ml-2 space-y-4 pl-5">
                <div>
                  <b>定位</b>
                  <p className="break-all text-sub">
                    {String(current.where ?? "—")}
                  </p>
                </div>
                <div>
                  <b>依据</b>
                  <p className="break-words text-sub">
                    {display(current.evidence)}
                  </p>
                </div>
                <div>
                  <b>修复方向</b>
                  <p className="text-sub">{String(current.fix ?? "—")}</p>
                </div>
              </div>
              <p className="break-all text-sub">
                证据版本：
                {String(
                  current.evidence_commit_id ??
                    report?.checked_commit_id ??
                    "—",
                )}
              </p>
              {Boolean(current.fixed_by_commit_id) && (
                <p className="break-all text-green">
                  修复提交：{String(current.fixed_by_commit_id)}
                </p>
              )}
              <button
                className="btn w-full py-2"
                onClick={() => onNavigate("changes")}
              >
                查看变更与修订 →
              </button>
            </div>
          ) : (
            <Empty>选择一项发现，检查事实与证据。</Empty>
          )}
        </Panel>
        <Notice>
          确定性规则检查与创作台中的 Agent
          审计互补。模型意见同样需要可定位的证据。
        </Notice>
      </aside>
    </main>
  );
}

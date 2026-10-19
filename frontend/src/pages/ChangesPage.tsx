/**
 * 变更影响（旧页，V03-8.6 起只读回看）。
 *
 * 逐章审批流（分析/计划/批准/执行/审阅/合并）已退出 0.3：HTTP 入口统一 410，
 * 页面只保留历史回看——变更集列表与详情（意图/状态/依据版本/涉及对象）。
 * 调整正文请用写作台的对话整体调整、手工修改与撤销。
 */

import { useEffect, useState } from "react";
import { fetchChangeDetail, type ChangeDetailWire } from "../api/v2";
import { useV2Store, message } from "../state/v2";
import {
  Panel,
  Empty,
  Notice,
  ErrorText,
  Pill,
  changeLabels,
  type View,
} from "../components/Workbench";

export default function ChangesPage({
  review = false,
  onNavigate,
}: {
  review?: boolean;
  onNavigate(view: View): void;
}) {
  const pid = useV2Store((s) => s.currentId);
  const changes = useV2Store((s) => s.changes);
  const selectedId = useV2Store((s) => s.selectedChangeId);
  const [detail, setDetail] = useState<ChangeDetailWire | null>(null);
  const [error, setError] = useState("");
  const activeId =
    selectedId ??
    changes.find((c) => !review || c.status === "review")?.change_set_id ??
    changes[0]?.change_set_id;

  useEffect(() => {
    setDetail(null);
    setError("");
    if (!pid || !activeId) return;
    let alive = true;
    void fetchChangeDetail(pid, activeId)
      .then((d) => {
        if (alive) setDetail(d);
      })
      .catch((e) => {
        if (alive) setError(message(e));
      });
    return () => {
      alive = false;
    };
  }, [pid, activeId]);

  const status = detail?.change_set.status;

  return (
    <main className="page-grid" aria-label={review ? "修订审阅" : "变更影响"}>
      <aside className="space-y-4">
        <Panel title="变更集" meta={`${changes.length} 个`}>
          {changes.length === 0 ? (
            <Empty>还没有变更集记录。</Empty>
          ) : (
            changes.map((c) => (
              <button
                key={c.change_set_id}
                className={`asset-row block w-full p-3 text-left text-xs ${
                  c.change_set_id === activeId ? "sel" : ""
                }`}
                onClick={() =>
                  useV2Store.setState({ selectedChangeId: c.change_set_id })
                }
              >
                <span className="block truncate font-bold">{c.intent}</span>
                <span className="text-[11px] text-sub">
                  {changeLabels[c.status] ?? c.status}
                </span>
              </button>
            ))
          )}
        </Panel>
        <Notice>
          <strong>逐章审批流已退出 0.3</strong>
          <br />
          这里只保留历史回看；调整正文请用写作台的对话整体调整、手工修改或撤销。
        </Notice>
        <button
          className="btn w-full py-2 text-xs"
          onClick={() => onNavigate("desk")}
        >
          回到写作台
        </button>
      </aside>
      <section className="min-w-0 space-y-4">
        <ErrorText>{error}</ErrorText>
        {detail ? (
          <Panel
            title={review ? "修订审阅（只读）" : "变更详情（只读）"}
            meta={changeLabels[status ?? ""]}
          >
            <div className="space-y-4 p-5">
              <h1 className="font-serif text-xl font-bold text-fgStrong">
                {detail.change_set.intent}
              </h1>
              <p className="flex flex-wrap items-center gap-2 text-[11px] text-sub">
                <Pill tone="blue">{changeLabels[status ?? ""] ?? status}</Pill>
                <span className="break-all">
                  依据版本：{detail.change_set.base_commit_id}
                </span>
              </p>
              <div>
                <p className="mb-2 text-xs font-bold text-fgStrong">
                  涉及对象（{detail.objects.length}）
                </p>
                {detail.objects.length === 0 ? (
                  <Empty>这个变更集没有派生对象。</Empty>
                ) : (
                  <ul className="space-y-1">
                    {detail.objects.map((o, index) => (
                      <li
                        key={`${o.kind}-${index}`}
                        className="asset-row flex items-center justify-between gap-3 p-2 text-xs"
                      >
                        <span className="min-w-0 flex-1 truncate">
                          {String(
                            o.content.title ??
                              o.content.change_set_id ??
                              o.content.step_id ??
                              o.kind,
                          )}
                        </span>
                        <span className="shrink-0 text-[10px] text-sub">
                          {o.kind}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Panel>
        ) : (
          <Panel title={review ? "修订审阅" : "变更详情"}>
            <Empty>选择一个变更集，查看修改依据与涉及对象。</Empty>
          </Panel>
        )}
      </section>
      <aside className="space-y-4">
        <Panel title="范围与依据">
          {detail ? (
            <div className="space-y-3 p-4 text-xs leading-6">
              <p>原因：{detail.change_set.intent}</p>
              <p className="break-all">依据版本：{detail.change_set.base_commit_id}</p>
              <p>
                种子对象：
                {(detail.change_set.seed_changes ?? []).length} 个
              </p>
            </div>
          ) : (
            <Empty>选择变更集后在这里核对依据。</Empty>
          )}
        </Panel>
        <Notice>
          旧审批流不再生成新的分析、计划或合并结果；已有记录只读保留，版本差异请看写作台的「版本历史」与「本次影响」。
        </Notice>
      </aside>
    </main>
  );
}

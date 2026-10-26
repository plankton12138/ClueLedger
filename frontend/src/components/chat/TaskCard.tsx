/**
 * 本轮任务卡（0.8 F4）。
 *
 * 把「应用实际允许了什么、还缺哪些必读材料、最后做成了什么」收在一处。所有字段都由
 * 会话事件投影而来：授权与禁止项取自 `task_scope_set`，未读取自材料读取记录，执行事实
 * 取自 `run_finished` 与检查结果。它不替模型自述，也不产生权限。
 */

import type {
  Recording,
  ReviewStatus,
  RunStatus,
  TaskGrant,
  TaskModuleContentGrant,
  TaskModuleGrant,
} from "../../api/workspace";
import { hasTaskCard, taskCardFacts, type ChatTurn } from "../../state/chatMessages";

const ACTION_LABEL: Record<TaskGrant["action"], string> = {
  create: "新建",
  update: "修改",
  delete: "删除",
  move: "移动",
};

const STATUS_LABEL: Record<RunStatus, string> = {
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  stopped: "已停止",
  interrupted: "中断",
  needs_input: "等待你回答",
};

const RECORDING_LABEL: Record<Recording, string> = {
  committed: "已记录",
  no_change: "没有改动",
  unrecorded: "未记录到历史",
};

const REVIEW_LABEL: Record<ReviewStatus, string> = {
  findings: "检查有发现",
  no_findings: "检查未发现问题",
  incomplete: "检查未完成",
};

function grantLabel(grant: TaskGrant): string {
  if (grant.action === "move" && grant.target) {
    return `${ACTION_LABEL[grant.action]} ${grant.path} → ${grant.target}`;
  }
  return `${ACTION_LABEL[grant.action]} ${grant.path}`;
}

function moduleGrantLabel(grant: TaskModuleGrant): string {
  if (grant.action === "attach") {
    return `装入 ${grant.module_id ?? ""}@${grant.revision_id ?? ""}`;
  }
  return `卸下 ${grant.binding_id ?? ""}`;
}

function contentGrantLabel(grant: TaskModuleContentGrant): string {
  if (grant.action === "create") return `新建模块 ${grant.slot ?? ""}`;
  return `修改 ${grant.module_id ?? ""}@${grant.base_revision ?? ""}`;
}

function titleOf(turn: ChatTurn): string {
  const scope = turn.scope;
  if (scope === null) return "任务卡";
  if (scope.mode === "read_only") return "任务卡 · 只读";
  const count =
    scope.grants.length +
    (scope.module_grants?.length ?? 0) +
    (scope.module_content_grants?.length ?? 0);
  return `任务卡 v${scope.version} · ${count} 项`;
}

export default function TaskCard({ turn }: { turn: ChatTurn }) {
  const facts = taskCardFacts(turn);
  if (!hasTaskCard(facts)) return null;
  const execution =
    facts.status === null
      ? ""
      : `${STATUS_LABEL[facts.status]}｜${RECORDING_LABEL[facts.recording ?? "no_change"]}`;
  return (
    <details className="mt-1 text-[11px] text-sub" data-testid="task-card">
      <summary className="cursor-pointer text-[10px] text-mute">
        {titleOf(turn)}
        {facts.requiredUnread.length > 0 && `｜必读未读 ${facts.requiredUnread.length} 份`}
        {execution && `｜${execution}`}
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5">
        {facts.summary && <li>任务：{facts.summary}</li>}
        {facts.authorization && (
          <li className="min-w-0 truncate" title={facts.authorization}>
            授权原话：{facts.authorization}
            {facts.authorizationSeq !== null && (
              <span className="text-mute">（第 {facts.authorizationSeq} 条消息）</span>
            )}
          </li>
        )}
        {facts.carriedFromSeq !== null && (
          <li className="text-mute">承接第 {facts.carriedFromSeq} 条作者消息</li>
        )}
        {facts.grants.map((grant) => (
          <li key={`${grant.action}:${grant.path}:${grant.target ?? ""}`}>
            允许{grantLabel(grant)}
          </li>
        ))}
        {facts.moduleGrants.map((grant) => (
          <li key={`${grant.action}:${grant.module_id ?? ""}:${grant.binding_id ?? ""}`}>
            允许{moduleGrantLabel(grant)}
          </li>
        ))}
        {facts.moduleContentGrants.map((grant) => (
          <li key={`${grant.action}:${grant.slot ?? ""}:${grant.binding_id ?? ""}`}>
            允许{contentGrantLabel(grant)}
          </li>
        ))}
        {facts.libraryPublish && <li>允许发布到模块库</li>}
        {facts.deniedPaths.length > 0 && (
          <li className="text-amber">不动：{facts.deniedPaths.join("、")}</li>
        )}
        {facts.extendable && <li className="text-mute">可按已读证据追加相关文件</li>}
        {facts.requiredComplete && <li>必读材料已提供全文。</li>}
        {facts.requiredUnread.map((item) => (
          <li key={item.path} className="text-amber">
            必读未读：<span className="truncate">{item.path}</span>（还剩 {item.remaining} 字符）
          </li>
        ))}
        {execution && (
          <li>
            {execution}
            {facts.changedPaths.length > 0 && `｜改动 ${facts.changedPaths.length} 个文件`}
          </li>
        )}
        {facts.changedPaths.map((path) => (
          <li key={path} className="pl-3 text-mute">
            · {path}
          </li>
        ))}
        {facts.reviewStatus && <li>{REVIEW_LABEL[facts.reviewStatus]}</li>}
      </ul>
    </details>
  );
}

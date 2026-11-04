/**
 * 全书结构只读页（v1.1 Q1 M2）。
 *
 * STRUCTURE.md 是模型自维护的全书工作记忆（铁律、路线、骨架、模块索引）；
 * 作者只读，不提供编辑框。想调整就在对话里说——模型改完入档，Git 历史可回退。
 * 起步前结构还不存在，这里如实说明；只读页同时可见体量与预算占用。
 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ListTree, MessageSquare } from "lucide-react";

import { errorMessage } from "../../api/errors";
import { readStructure, type StructureView } from "../../api/structure";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

export default function StructureView({
  workspaceId,
  onOpenChat,
}: {
  workspaceId: string;
  onOpenChat: () => void;
}) {
  const [structure, setStructure] = useState<StructureView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    readStructure(workspaceId)
      .then(data => {
        if (alive) setStructure(data);
      })
      .catch(reason => {
        if (alive) setError(errorMessage(reason, "全书结构"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [workspaceId]);

  if (loading) return <div className="ui-empty" role="status"><p>正在读取全书结构…</p></div>;
  if (error) {
    return <div className="writing-load-error"><StatusMessage tone="error" action={
      <Button size="compact" onClick={() => {
        setLoading(true);
        setError(null);
        readStructure(workspaceId)
          .then(data => setStructure(data))
          .catch(reason => setError(errorMessage(reason, "全书结构")))
          .finally(() => setLoading(false));
      }}>重试</Button>
    }>{error}</StatusMessage></div>;
  }
  if (!structure?.exists) {
    return <article className="plan-view" aria-label="全书结构">
      <header className="plan-view-header">
        <h1><ListTree size={20} strokeWidth={1.8} aria-hidden="true" />全书结构</h1>
        <p>全书结构是模型每轮写正文前读取的全局依据，也是开始写正文的前提。结构还没有建立；在对话里说出你的想法，模型会整理并写入第一版结构。</p>
      </header>
      <div className="plan-field">
        <Button size="compact" variant="primary" onClick={onOpenChat}>
          <MessageSquare size={15} strokeWidth={2} aria-hidden="true" />回到对话
        </Button>
      </div>
    </article>;
  }
  const within = structure.within_budget;
  return <article className="plan-view" aria-label="全书结构">
    <header className="plan-view-header">
      <h1><ListTree size={20} strokeWidth={1.8} aria-hidden="true" />全书结构</h1>
      <p>模型每轮写正文前都会读取这份结构，作为方向、铁律和章节的依据。这里只读；想调整就在对话里说，模型改完入档，Git 历史可回退。</p>
    </header>
    {!within && <StatusMessage tone="warning">
      体量 {structure.size} 字符已超预算上限 {structure.budget_limit}；下次入档前模型会先压缩结构。
    </StatusMessage>}
    {within && <p className="text-sm text-mute">体量 {structure.size} / 预算上限 {structure.budget_limit} 字符</p>}
    <div className="plan-field structure-doc prose-paper" aria-label="结构全文">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {structure.text}
      </ReactMarkdown>
    </div>
    <div className="plan-field">
      <Button size="compact" onClick={onOpenChat}>
        <MessageSquare size={15} strokeWidth={2} aria-hidden="true" />在对话里继续调整
      </Button>
    </div>
  </article>;
}

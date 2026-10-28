/** 保存一个推理目标：目标、意图、关联线索、必要前提与其他解释。 */
import { useEffect, useState } from "react";
import {
  clueRequestKey,
  fetchClues,
  saveClueInference,
  type ClueInference,
  type ClueIntent,
  type InferenceStatement,
} from "../../api/clues";
import { errorMessage } from "../../api/errors";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

const INTENTS: { value: ClueIntent; label: string }[] = [
  { value: "deduce", label: "推出结论" },
  { value: "suspect", label: "只引起怀疑" },
  { value: "keep_open", label: "保留多种解释" },
  { value: "undecided", label: "尚未决定" },
];

interface Props {
  workspaceId: string;
  clueId: string;
  asOf: number | null;
  ledgerRevision: string;
  designs: { link_id: string; label: string; quote: string }[];
  existing?: ClueInference;
  onSaved: (view: ClueInference) => void;
  onClose: () => void;
}

function lines(text: string): string[] {
  return text.split("\n").map(item => item.trim()).filter(Boolean);
}

function statements(text: string, prior: InferenceStatement[] | undefined): InferenceStatement[] {
  return lines(text).map((value, index) => ({
    text: value,
    clue_ids: prior?.[index]?.clue_ids ?? [],
    design_link_ids: prior?.[index]?.design_link_ids ?? [],
  }));
}

export default function ClueInferenceEditor({
  workspaceId,
  clueId,
  asOf,
  ledgerRevision,
  designs,
  existing,
  onSaved,
  onClose,
}: Props) {
  const [goal, setGoal] = useState(existing?.goal ?? "");
  const [intent, setIntent] = useState<ClueIntent>(existing?.intent ?? "deduce");
  const [selected, setSelected] = useState<string[]>(existing?.clue_ids ?? [clueId]);
  const [selectedLinks, setSelectedLinks] = useState<string[]>(existing?.design_link_ids ?? []);
  const [premises, setPremises] = useState(existing?.premises.map(item => item.text).join("\n") ?? "");
  const [alternatives, setAlternatives] = useState(
    existing?.alternatives.map(item => item.text).join("\n") ?? "",
  );
  const [options, setOptions] = useState<{ clue_id: string; title: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void fetchClues(workspaceId, null, asOf, "", 50)
      .then(page => {
        if (!alive) return;
        const items = page.items.map(item => ({ clue_id: item.clue_id, title: item.title }));
        setOptions(items.some(item => item.clue_id === clueId) ? items : [{ clue_id: clueId, title: "当前线索" }, ...items]);
      })
      .catch(cause => {
        if (alive) setError(errorMessage(cause, "读取线索列表"));
      });
    return () => {
      alive = false;
    };
  }, [workspaceId, asOf, clueId]);

  async function submit() {
    if (busy) return;
    if (!goal.trim()) {
      setError("[CLUE_SCOPE_INVALID] 推理目标：还没有写结论。建议：写一句这次要推出的判断。");
      return;
    }
    if (selected.length === 0) {
      setError("[CLUE_SCOPE_INVALID] 推理目标：还没有选择线索。建议：至少关联一条。");
      return;
    }
    if (lines(premises).length > 10 || lines(alternatives).length > 10) {
      setError("[CLUE_SCOPE_INVALID] 推理目标：前提或其他解释超过 10 条。建议：先保留与目标直接相关的条目。");
      return;
    }
    if (selected.length > 10) {
      setError("[CLUE_SCOPE_INVALID] 推理目标：关联线索超过 10 条。建议：拆成有明确目标的步骤。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const view = await saveClueInference(workspaceId, {
        requestKey: clueRequestKey("clue-inference"),
        expectedRevision: ledgerRevision,
        inferenceId: existing?.inference_id,
        inferenceRevision: existing?.inference_revision,
        clueIds: selected,
        goal: goal.trim(),
        intent,
        premises: statements(premises, existing?.premises),
        alternatives: statements(alternatives, existing?.alternatives),
        designLinkIds: selectedLinks,
      });
      onSaved(view);
      onClose();
    } catch (cause) {
      setError(errorMessage(cause, "保存推理目标"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <label className="block">目标结论
        <textarea className="ui-input mt-1 min-h-20 w-full" value={goal} maxLength={2000}
          onChange={event => setGoal(event.target.value)} placeholder="例如：林岚在案发前接触过遗书原件" />
      </label>
      <label className="block">意图
        <select className="ui-input mt-1 w-full" value={intent}
          onChange={event => setIntent(event.target.value as ClueIntent)}>
          {INTENTS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
      </label>
      <fieldset>
        <legend className="text-xs text-sub">关联线索</legend>
        <div className="mt-1 max-h-36 space-y-1 overflow-auto rounded border border-row-edge p-2">
          {options.length === 0 ? <p className="text-xs text-sub">正在读取线索…</p> : options.map(item => (
            <label key={item.clue_id} className="flex items-center gap-2">
              <input type="checkbox" checked={selected.includes(item.clue_id)}
                onChange={event => {
                  if (!event.target.checked && item.clue_id === clueId) setSelectedLinks([]);
                  setSelected(previous => event.target.checked
                    ? [...new Set([...previous, item.clue_id])]
                    : previous.filter(value => value !== item.clue_id));
                }} />
              <span className="min-w-0 break-words">{item.title}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {designs.length > 0 && (
        <fieldset>
          <legend className="text-xs text-sub">本线索的计划依据</legend>
          <div className="mt-1 space-y-1 rounded border border-row-edge p-2">
            {designs.map(item => (
              <label key={item.link_id} className="flex items-start gap-2">
                <input type="checkbox" checked={selectedLinks.includes(item.link_id)}
                  disabled={!selected.includes(clueId)}
                  onChange={event => setSelectedLinks(previous => event.target.checked
                    ? [...new Set([...previous, item.link_id])]
                    : previous.filter(value => value !== item.link_id))} />
                <span className="min-w-0 break-words text-xs"><span className="block text-sub">{item.label}</span>{item.quote}</span>
              </label>
            ))}
          </div>
          {!selected.includes(clueId) && <p className="mt-1 text-xs text-amber">当前线索未加入目标，计划依据暂不能选用。</p>}
        </fieldset>
      )}
      <label className="block">必要前提（每行一条）
        <textarea className="ui-input mt-1 min-h-20 w-full" value={premises}
          onChange={event => setPremises(event.target.value)} placeholder="林岚能说出遗书背面的内容" />
      </label>
      <label className="block">其他解释（每行一条）
        <textarea className="ui-input mt-1 min-h-20 w-full" value={alternatives}
          onChange={event => setAlternatives(event.target.value)} placeholder="警方此前公开过背面照片" />
      </label>
      {error && <StatusMessage tone="error">{error}</StatusMessage>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button size="compact" variant="quiet" onClick={onClose}>取消</Button>
        <Button size="compact" variant="primary" busy={busy} onClick={() => void submit()}>
          保存目标
        </Button>
      </div>
    </div>
  );
}

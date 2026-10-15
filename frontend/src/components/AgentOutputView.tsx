type Json = Record<string, unknown>;
const object = (value: unknown): Json => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Json : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const kinds: Record<string, string> = { "story.character": "人物", "story.clue": "线索", "story.red_herring": "误导", "story.mystery_truth": "案件真相", "narrative.series": "系列", "narrative.book": "单本", "narrative.arc": "故事弧", "narrative.chapter": "章节", "narrative.scene": "场景", "narrative.beat": "节拍" };
const fields: Record<string, string> = { name: "姓名", identity: "身份", goal: "目标", need: "内在需要", secret: "秘密", arc: "人物变化", role: "故事作用", traits: "特征", title: "名称", parent_id: "所属结构", purpose: "叙事目的", pov_character_id: "视角人物", participants: "参与人物", preconditions: "前置条件", constraints: "约束", statement: "线索表述", source: "来源", false_explanation: "错误解释", basis: "误导依据", resolution: "揭穿方式", culprit_id: "凶手", motive: "动机", method: "手法", cover_up: "掩饰", accomplice_ids: "共犯", true_timeline: "真实时间线" };
const values: Record<string, string> = { protagonist: "主角", antagonist: "对立者", accomplice: "共犯", support: "配角", witness: "证人" };
const categories: Record<string, string> = { style: "措辞与句式", fact: "事实修正", structure: "结构调整", formatting: "格式修改", typo: "文字纠错", uncertain: "尚待判断" };

export default function AgentOutputView({ output, names = {} }: { output: Json; names?: Record<string, string> }) {
  const kind = output.kind;
  if (kind === "manuscript_patch") return <div className="space-y-2 whitespace-pre-wrap">{output.text ? String(output.text) : list(output.patches).map((patch, i) => {
    const content = object(object(patch).content);
    return <p key={i}>{String(content.text || content.purpose || content.goal || "故事内容修订，待变更审阅")}</p>;
  })}{Boolean(output.rationale) && <p className="text-sm text-paper/60">{String(output.rationale)}</p>}</div>;
  if (kind === "story_proposal") {
    const items = list(output.items).map(object);
    const titles = { ...names, ...Object.fromEntries(items.map(item => [String(item.artifact_id), String(object(item.content).name || object(item.content).title || kinds[String(item.kind)] || "故事对象")])) };
    const display = (value: unknown): string => Array.isArray(value) ? value.map(display).join("、") : titles[String(value)] || values[String(value)] || (typeof value === "string" ? value : "见结构说明");
    return <div className="space-y-3">{items.map((item, index) => <div key={index} className="border-l-2 border-gold/40 pl-3">
      <h3 className="text-gold">{kinds[String(item.kind)] || "故事建议"} · {titles[String(item.artifact_id)]}</h3>
      <dl className="text-sm">{Object.entries(object(item.content)).filter(([key, value]) => fields[key] && value !== "" && value !== null && (!Array.isArray(value) || value.length > 0)).map(([key, value]) => <div key={key} className="mt-1"><dt className="inline text-paper/60">{fields[key]}：</dt><dd className="inline">{display(value)}</dd></div>)}</dl>
    </div>)}{Boolean(output.rationale) && <p className="text-sm text-paper/60">{String(output.rationale)}</p>}{items.length === 0 && !output.rationale && <p>已选择后续用例，尚未变更故事内容。</p>}</div>;
  }
  if (kind === "finding_set") return <div className="space-y-3">{list(output.findings).map((value, i) => {
    const finding = object(value);
    return <div key={i}><blockquote className="border-l-2 border-gold/40 pl-3">{String(finding.quote)}</blockquote><p className="mt-1">{String(finding.reason)}</p>{Boolean(finding.suggestion) && <p className="text-sm text-paper/70">建议：{String(finding.suggestion)}</p>}</div>;
  })}{list(output.findings).length === 0 && <p>本轮没有新的可定位问题。</p>}{list(output.editorial_notes).map((note, i) => <p key={i} className="text-sm text-paper/60">编辑意见：{String(note)}</p>)}</div>;
  if (kind === "observation_set") return <ul className="space-y-2">{list(output.observations).map((value, i) => {
    const observation = object(value);
    return <li key={i}><span className="text-gold">{categories[String(observation.category)] || "观察"}</span>：{String(observation.statement)}<span className="ml-2 text-xs text-paper/60">置信度 {Math.round(Number(observation.confidence) * 100)}%</span></li>;
  })}</ul>;
  if (kind === "clarification") return <ul className="list-inside list-disc space-y-1">{list(output.questions).map((question, i) => <li key={i}>{String(question)}</li>)}</ul>;
  return <p>{String(output.reason || "结果需要作者处理")}</p>;
}


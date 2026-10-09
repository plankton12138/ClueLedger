/**
 * 设置面板（P6）：工作区级写作语言选择。
 *
 * 数据流：选中 slug 变化即 GET /api/{slug}/settings 载入当前值；下拉变更即
 * PUT 落盘并热生效（服务端重建 system prompt，下一轮对话即用新语言）。
 * 失败文案原样展示（服务端三要素 detail 透传）。
 */

import { useEffect, useState } from "react";
import {
  WRITING_LANGUAGES,
  fetchSettings,
  saveSettings,
} from "../api/rest";
import { useChatStore } from "../state/store";

type Feedback = { kind: "ok" | "error"; text: string } | null;

export default function SettingsPanel() {
  const slug = useChatStore((s) => s.slug);
  const [language, setLanguage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);

  // 切换工作区即重新载入：清旧反馈，拉当前值（旧工作区无文件时回落默认中文）。
  useEffect(() => {
    setFeedback(null);
    setLanguage(null);
    if (slug === null) return;
    setLoading(true);
    let cancelled = false;
    fetchSettings(slug)
      .then((value) => {
        if (!cancelled) setLanguage(value);
      })
      .catch((exc: unknown) => {
        if (!cancelled) {
          setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  async function change(value: string): Promise<void> {
    if (slug === null || saving) return;
    setSaving(true);
    setFeedback(null);
    try {
      const saved = await saveSettings(slug, value);
      setLanguage(saved);
      setFeedback({ kind: "ok", text: `已切换为「${saved}」，下一轮对话即生效。` });
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setSaving(false);
    }
  }

  if (slug === null) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-paper/50">
        先从上方下拉选择一本案卷，再配置写作语言。
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl border border-line bg-ink-soft/35 p-5">
        <h2 className="font-serif text-lg font-bold text-gold">写作语言</h2>
        <p className="mt-2 text-xs leading-relaxed text-paper/60">
          约定模型产出（账本条目、章节正文、对话回复）使用的语言。故事背景可以设在任何国家、
          角色姓名可保留原写法，但叙事语言始终由这里决定。
        </p>

        <label htmlFor="writing-language" className="mt-5 block text-xs text-gold-soft">
          本工作区的写作语言
        </label>
        <select
          id="writing-language"
          value={language ?? ""}
          disabled={loading || saving}
          onChange={(e) => void change(e.target.value)}
          className="mt-2 w-56 border border-line bg-transparent px-2 py-1.5 text-sm text-paper focus:border-gold/60 focus:outline-none disabled:opacity-40"
        >
          {language === null && <option value="">载入中…</option>}
          {language !== null &&
            !(WRITING_LANGUAGES as readonly string[]).includes(language) && (
              <option value={language}>{language}</option>
            )}
          {WRITING_LANGUAGES.map((lang) => (
            <option key={lang} value={lang}>
              {lang}
            </option>
          ))}
        </select>

        {saving && <p className="mt-3 text-xs text-planned">保存中…</p>}
        {feedback && (
          <p
            className={`mt-3 text-xs leading-relaxed ${
              feedback.kind === "ok" ? "text-planted" : "text-overdue"
            }`}
          >
            {feedback.text}
          </p>
        )}
      </div>
    </div>
  );
}
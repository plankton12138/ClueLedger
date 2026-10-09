/**
 * 设置面板（P6 T3）：写作语言 + 模型端点档案 + 模型。
 *
 * 数据流：选中 slug 变化即并行 GET /api/{slug}/settings 与 /api/providers 载入
 * 当前值与可选档案；「保存设置」PUT 落盘并生效——同档案换模型热切（下一轮对话
 * 即生效），跨档案切换会重建会话（先经确认弹窗提示，对话历史仍保留）。
 * 「拉取模型列表」直连档案端点，兼作连通测试（key/网络/base_url 全链路）。
 * 失败文案原样展示（服务端三要素 detail 透传）。
 */

import { useEffect, useState } from "react";
import {
  WRITING_LANGUAGES,
  fetchProviderModels,
  fetchProviders,
  fetchSettings,
  saveSettings,
  type ProviderSummary,
  type SettingsResponse,
} from "../api/rest";
import { useChatStore } from "../state/store";

type Feedback = { kind: "ok" | "error"; text: string } | null;

export default function SettingsPanel() {
  const slug = useChatStore((s) => s.slug);
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [language, setLanguage] = useState("");
  const [profile, setProfile] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);

  // 切换工作区即重新载入：清旧状态，拉当前设置与档案目录。
  useEffect(() => {
    setFeedback(null);
    setSettings(null);
    setModels([]);
    if (slug === null) return;
    setLoading(true);
    let cancelled = false;
    Promise.all([fetchSettings(slug), fetchProviders()])
      .then(([current, catalog]) => {
        if (cancelled) return;
        setSettings(current);
        setProviders(catalog);
        setLanguage(current.writing_language);
        setProfile(current.provider_profile);
        setModel(current.model);
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

  const selected = providers.find((p) => p.name === profile) ?? null;
  const busy = loading || saving;

  async function pullModels(): Promise<void> {
    if (slug === null || profile === "" || fetching) return;
    setFetching(true);
    setFeedback(null);
    try {
      const list = await fetchProviderModels(profile);
      setModels(list);
      if (list.length === 0) {
        setFeedback({
          kind: "error",
          text: "连接成功，但该端点未返回模型列表——请手动输入模型名。",
        });
      } else {
        setFeedback({ kind: "ok", text: `连接成功，拉取到 ${list.length} 个候选模型。` });
      }
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setFetching(false);
    }
  }

  async function save(): Promise<void> {
    if (slug === null || settings === null || saving) return;
    const profileChanged = profile !== settings.provider_profile;
    if (
      profileChanged &&
      !window.confirm(
        `切换模型端点档案「${profile}」会重建该工作区会话` +
          `（对话历史保留；若有写作流在进行，请先等它结束）。继续？`,
      )
    ) {
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const saved = await saveSettings(slug, {
        writing_language: language,
        provider_profile: profile,
        model,
      });
      setSettings(saved);
      setLanguage(saved.writing_language);
      setProfile(saved.provider_profile);
      setModel(saved.model);
      setFeedback({
        kind: "ok",
        text: profileChanged
          ? `已切换到档案「${saved.provider_profile}」，会话已重建，下一轮对话即生效。`
          : `已保存，下一轮对话即生效（生效模型：${saved.model}）。`,
      });
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setSaving(false);
    }
  }

  if (slug === null) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-paper/50">
        先从上方下拉选择一本案卷，再配置模型接入与写作语言。
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl border border-line bg-ink-soft/35 p-5">
        <h2 className="font-serif text-lg font-bold text-gold">模型接入</h2>
        <p className="mt-2 text-xs leading-relaxed text-paper/60">
          端点档案来自服务级 config.yaml（`python -m hastings.server` 启动目录），API key
          仅在服务端使用、前端只见尾 4 位提示。同档案换模型下一轮对话即生效；换档案会重建会话，
          但对话历史保留。
        </p>

        <label htmlFor="provider-profile" className="mt-5 block text-xs text-gold-soft">
          端点档案
        </label>
        <select
          id="provider-profile"
          value={profile}
          disabled={busy}
          onChange={(e) => {
            setProfile(e.target.value);
            setModels([]);
            setFeedback(null);
          }}
          className="mt-2 w-full border border-line bg-transparent px-2 py-1.5 text-sm text-paper focus:border-gold/60 focus:outline-none disabled:opacity-40"
        >
          {loading && <option value="">载入中…</option>}
          {!loading && providers.length === 0 && <option value="">（无可用档案）</option>}
          {!loading && profile !== "" && !providers.some((p) => p.name === profile) && (
            <option value={profile}>{profile}（已不在 config.yaml 中）</option>
          )}
          {providers.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
              {p.available ? "" : "（缺 key）"}
            </option>
          ))}
        </select>
        {selected && (
          <p className="mt-1.5 text-[11px] text-paper/40">
            {selected.base_url}
            {selected.key_hint ? `　key ${selected.key_hint}` : "　未配置 key"}
          </p>
        )}

        <label htmlFor="provider-model" className="mt-4 block text-xs text-gold-soft">
          模型名（可下拉选择，也可手动输入）
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="provider-model"
            list="provider-model-options"
            value={model}
            disabled={busy}
            onChange={(e) => setModel(e.target.value)}
            placeholder={selected?.default_model || "如 deepseek-chat"}
            className="min-w-0 flex-1 border border-line bg-transparent px-2 py-1.5 text-sm text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none disabled:opacity-40"
          />
          <datalist id="provider-model-options">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <button
            type="button"
            onClick={() => void pullModels()}
            disabled={busy || fetching || profile === "" || selected?.available === false}
            className="shrink-0 border border-line px-3 py-1.5 text-sm text-paper/75 transition-colors hover:border-gold/50 hover:text-gold disabled:opacity-40"
          >
            {fetching ? "拉取中…" : "拉取模型列表"}
          </button>
        </div>
        {selected?.available === false && (
          <p className="mt-1.5 text-[11px] text-overdue">
            该档案缺少 API key，无法连通；请在 config.yaml 补 api_key / api_key_env。
          </p>
        )}

        <label htmlFor="writing-language" className="mt-4 block text-xs text-gold-soft">
          写作语言
        </label>
        <select
          id="writing-language"
          value={language}
          disabled={busy}
          onChange={(e) => setLanguage(e.target.value)}
          className="mt-2 w-56 border border-line bg-transparent px-2 py-1.5 text-sm text-paper focus:border-gold/60 focus:outline-none disabled:opacity-40"
        >
          {loading && <option value="">载入中…</option>}
          {!loading && language !== "" && !(WRITING_LANGUAGES as readonly string[]).includes(language) && (
            <option value={language}>{language}</option>
          )}
          {WRITING_LANGUAGES.map((lang) => (
            <option key={lang} value={lang}>
              {lang}
            </option>
          ))}
        </select>
        <p className="mt-1.5 text-[11px] leading-relaxed text-paper/40">
          约定模型产出（账本条目、章节正文、对话回复）使用的语言；故事背景可设在任何国家，
          角色姓名可保留原写法，但叙事语言由这里决定。
        </p>

        <div className="mt-6 flex items-center gap-3">
          <button
            type="button"
            onClick={() => void save()}
            disabled={busy || settings === null}
            className="border border-gold/60 px-4 py-1.5 text-sm text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
          >
            {saving ? "保存中…" : "保存设置"}
          </button>
          <span className="text-[11px] text-paper/40">
            {settings ? `当前生效模型：${settings.model}` : ""}
          </span>
        </div>

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
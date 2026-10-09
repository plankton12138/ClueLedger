/**
 * 设置面板（P6 T3，P7 T14 档案管理 UI 化）：写作语言 + 模型端点档案 + 模型 + 档案增删改。
 *
 * 数据流：选中 slug 变化即并行 GET /api/{slug}/settings 与 /api/providers 载入
 * 当前值与可选档案；「保存设置」PUT 落盘并生效——同档案换模型热切（下一轮对话
 * 即生效），跨档案切换会重建会话（先经确认弹窗提示，对话历史仍保留）。
 * 「拉取模型列表」直连档案端点，兼作连通测试（key/网络/base_url 全链路）。
 * 档案管理卡（T14）：PUT /api/providers/{name} upsert（修改时 key 留空 = 保留旧 key）、
 * DELETE（409 三保护文案原样透出）、POST /api/providers/test 未保存表单直测。
 * 失败文案原样展示（服务端三要素 detail 透传）。
 */

import { useEffect, useState } from "react";
import {
  WRITING_LANGUAGES,
  deleteProvider,
  fetchProviderModels,
  fetchProviders,
  fetchSettings,
  saveSettings,
  testProvider,
  upsertProvider,
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
        先在「文章」页选择一篇文章，再配置模型接入与写作语言。
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto flex max-w-xl flex-col gap-6">
      <div className="border border-line bg-ink-soft/35 p-5">
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

      <ProviderAdminCard
        providers={providers}
        currentProfile={profile}
        onProvidersChanged={(list) => setProviders(list)}
      />
      </div>
    </div>
  );
}

/** 档案表单空值（api_key 恒空起填：明文不可回显，留空 = 修改时保留旧 key）。 */
function emptyForm(): {
  name: string;
  base_url: string;
  api_key: string;
  api_key_env: string;
  default_model: string;
} {
  return { name: "", base_url: "", api_key: "", api_key_env: "", default_model: "" };
}

/**
 * 档案管理卡（P7 T14）：列表（编辑/删除）+ 新建/编辑表单 + 测试连接。
 * 服务端三要素 detail（409 三保护、422、500）原样透出，不做本地人话翻译。
 */
function ProviderAdminCard({
  providers,
  currentProfile,
  onProvidersChanged,
}: {
  providers: ProviderSummary[];
  currentProfile: string;
  onProvidersChanged: (list: ProviderSummary[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm());
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  function startCreate(): void {
    setEditing(null);
    setForm(emptyForm());
    setFeedback(null);
    setOpen(true);
  }

  function startEdit(p: ProviderSummary): void {
    setEditing(p.name);
    setForm({
      name: p.name,
      base_url: p.base_url,
      api_key: "",
      api_key_env: "",
      default_model: p.default_model,
    });
    setFeedback(null);
    setOpen(true);
  }

  async function refresh(): Promise<void> {
    onProvidersChanged(await fetchProviders());
  }

  async function runTest(): Promise<void> {
    if (testing || form.base_url.trim() === "") return;
    setTesting(true);
    setFeedback(null);
    try {
      const list = await testProvider({
        base_url: form.base_url.trim(),
        api_key: form.api_key,
        api_key_env: form.api_key_env.trim(),
      });
      setFeedback(
        list.length === 0
          ? { kind: "ok", text: "连接成功，但端点未返回模型列表——可手动填写默认模型。" }
          : { kind: "ok", text: `连接成功，拉取到 ${list.length} 个候选模型。` },
      );
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setTesting(false);
    }
  }

  async function save(): Promise<void> {
    const name = editing ?? form.name.trim();
    if (name === "" || form.base_url.trim() === "" || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const saved = await upsertProvider(name, {
        base_url: form.base_url.trim(),
        api_key: form.api_key,
        api_key_env: form.api_key_env.trim(),
        default_model: form.default_model.trim(),
      });
      await refresh();
      setOpen(false);
      setFeedback({
        kind: "ok",
        text:
          editing === null
            ? `档案「${saved.name}」已创建并写入 config.yaml。`
            : `档案「${saved.name}」已保存${form.api_key === "" ? "（key 沿用原值）" : ""}。`,
      });
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string): Promise<void> {
    if (deleting !== null) return;
    if (!window.confirm(`删除档案「${name}」？正在使用它的文章将回退到引导档案。`)) return;
    setDeleting(name);
    setFeedback(null);
    try {
      await deleteProvider(name);
      await refresh();
      setFeedback({ kind: "ok", text: `档案「${name}」已删除。` });
    } catch (exc) {
      // 409 三保护（引导档案/最后档案/被文章引用）文案原样展示
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setDeleting(null);
    }
  }

  const inputCls =
    "w-full border border-line bg-transparent px-2 py-1.5 text-sm text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none disabled:opacity-40";

  return (
    <div className="border border-line bg-ink-soft/35 p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-gold">档案管理</h2>
        <button
          type="button"
          onClick={startCreate}
          className="flex-none border border-gold/60 px-3 py-1 text-xs text-gold transition-colors hover:bg-gold/15"
        >
          ＋ 新建档案
        </button>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-paper/40">
        模型来源写在服务级 config.yaml（启动目录），保存即写回并在本服务热生效；key
        只在服务端使用，前端只见尾 4 位。
      </p>

      <ul className="mt-3 divide-y divide-line/60">
        {providers.map((p) => (
          <li key={p.name} className="flex min-w-0 items-center gap-3 py-2.5">
            <span className="min-w-0 flex-1 truncate text-sm text-paper" title={p.name}>
              {p.name}
              {p.name === currentProfile && (
                <span className="ml-2 text-[11px] text-gold-soft">当前</span>
              )}
            </span>
            <span
              className="hidden min-w-0 flex-1 truncate text-[11px] text-paper/45 sm:block"
              title={p.base_url}
            >
              {p.base_url}
            </span>
            <span className="flex-none text-[11px] text-paper/45">
              {p.key_hint === "" ? "未配 key" : `key ${p.key_hint}`}
            </span>
            <button
              type="button"
              onClick={() => startEdit(p)}
              className="flex-none border border-line px-2 py-0.5 text-xs text-paper/75 transition-colors hover:text-gold"
            >
              编辑
            </button>
            <button
              type="button"
              disabled={deleting !== null}
              onClick={() => void remove(p.name)}
              className="flex-none border border-line px-2 py-0.5 text-xs text-paper/75 transition-colors hover:border-overdue/60 hover:text-overdue disabled:opacity-40"
            >
              {deleting === p.name ? "删除中…" : "删除"}
            </button>
          </li>
        ))}
      </ul>

      {open && (
        <div className="mt-4 border border-line bg-ink p-4">
          <p className="text-xs text-gold-soft">
            {editing === null ? "新建档案" : `编辑档案「${editing}」`}
          </p>
          <label htmlFor="provider-form-name" className="mt-3 block text-xs text-paper/60">
            档案名（唯一标识，工作区设置按名引用）
          </label>
          <input
            id="provider-form-name"
            value={form.name}
            disabled={editing !== null}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="deepseek"
            className={`mt-1.5 ${inputCls}`}
          />
          <label htmlFor="provider-form-base-url" className="mt-3 block text-xs text-paper/60">
            Base URL（OpenAI 兼容端点，通常以 /v1 结尾）
          </label>
          <input
            id="provider-form-base-url"
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="https://api.deepseek.com/v1"
            className={`mt-1.5 ${inputCls}`}
          />
          <label htmlFor="provider-form-api-key" className="mt-3 block text-xs text-paper/60">
            API key（直填）
            {editing !== null && "——留空表示沿用原 key"}
          </label>
          <input
            id="provider-form-api-key"
            type="password"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            placeholder={editing === null ? "sk-…" : "留空沿用原 key"}
            className={`mt-1.5 ${inputCls}`}
          />
          <label htmlFor="provider-form-api-key-env" className="mt-3 block text-xs text-paper/60">
            或用环境变量名引用（服务进程内已设置的变量，如 OPENROUTER_API_KEY）
          </label>
          <input
            id="provider-form-api-key-env"
            value={form.api_key_env}
            onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
            placeholder="留空则只用直填 key"
            className={`mt-1.5 ${inputCls}`}
          />
          <label htmlFor="provider-form-default-model" className="mt-3 block text-xs text-paper/60">
            默认模型（可选；文章未显式指定模型时用它）
          </label>
          <input
            id="provider-form-default-model"
            value={form.default_model}
            onChange={(e) => setForm({ ...form, default_model: e.target.value })}
            placeholder="deepseek-chat"
            className={`mt-1.5 ${inputCls}`}
          />
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void runTest()}
              disabled={testing || busy || form.base_url.trim() === ""}
              className="border border-line px-3 py-1.5 text-xs text-paper/75 transition-colors hover:border-gold/50 hover:text-gold disabled:opacity-40"
            >
              {testing ? "测试中…" : "测试连接"}
            </button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={
                busy ||
                (editing === null && form.name.trim() === "") ||
                form.base_url.trim() === ""
              }
              className="border border-gold/60 px-4 py-1.5 text-xs text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
            >
              {busy ? "保存中…" : "保存"}
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="border border-line px-3 py-1.5 text-xs text-paper/60 transition-colors hover:text-paper"
            >
              取消
            </button>
          </div>
        </div>
      )}

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
  );
}
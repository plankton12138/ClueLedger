/**
 * 本地模型卡（D11）：Ollama 三态检测 + 一键下载 + 已装管理 + 设为当前模型。
 *
 * 三态 UI（服务端 status 恒 200，不可达是结果数据）：
 * - not_installed → 安装引导（官方链接 + 步骤 + 重新检测）；
 * - installed_not_running → 启动提示 + 重新检测；
 * - running → 预置清单一键下载（进度条）+ 自定义模型名 + 已装列表。
 *
 * 进度走 POST /api/ollama/pull 流式 NDJSON（fetch + ReadableStream），
 * AbortController 可取消；下载在 Ollama 侧继续，取消/断开后重发即续传。
 * 「设为当前模型」两跳：upsertProvider("ollama", no_auth) → saveSettings
 * （跨档案切换会重建会话，先经确认弹窗，与模型接入卡同一口径）。
 * 进度生命周期限于本卡（卸载即 abort），不进 Zustand 全局态。
 */

import { useEffect, useRef, useState } from "react";
import {
  deleteOllamaModel,
  fetchOllamaCatalog,
  fetchOllamaModels,
  fetchOllamaStatus,
  pullOllamaModel,
  saveSettings,
  upsertProvider,
  type OllamaCatalogEntryWire,
  type OllamaModelInfoWire,
  type OllamaStatusWire,
} from "../api/rest";
import { formatBytes, pullPercent, uiStateFromStatus } from "../state/ollama";

type Feedback = { kind: "ok" | "error"; text: string } | null;

interface PullTask {
  model: string;
  percent: number | null;
  note: string;
}

interface LocalModelCardProps {
  slug: string;
  writingLanguage: string;
  onProvidersChanged: () => Promise<void>;
  onModelSwitched: (profile: string, model: string) => void;
}

export default function LocalModelCard({
  slug,
  writingLanguage,
  onProvidersChanged,
  onModelSwitched,
}: LocalModelCardProps) {
  const [status, setStatus] = useState<OllamaStatusWire | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<OllamaCatalogEntryWire[]>([]);
  const [installed, setInstalled] = useState<OllamaModelInfoWire[]>([]);
  const [pull, setPull] = useState<PullTask | null>(null);
  const [customName, setCustomName] = useState("");
  const [switching, setSwitching] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function refreshInstalled(): Promise<void> {
    try {
      setInstalled(await fetchOllamaModels());
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    }
  }

  async function recheck(): Promise<void> {
    if (pull !== null) return; // 下载进行中不重检，避免状态抖动
    setStatusError(null);
    try {
      const current = await fetchOllamaStatus();
      setStatus(current);
      if (current.state === "running") await refreshInstalled();
    } catch (exc) {
      setStatusError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  // 挂载即检测 + 拉预置清单；卸载中断进行中的下载流。
  useEffect(() => {
    void recheck();
    fetchOllamaCatalog()
      .then(setCatalog)
      .catch((exc: unknown) =>
        setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) }),
      );
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startPull(model: string): Promise<void> {
    if (pull !== null || model.trim() === "") return;
    const controller = new AbortController();
    abortRef.current = controller;
    setPull({ model, percent: null, note: "连接 Ollama…" });
    setFeedback(null);
    try {
      await pullOllamaModel(
        model,
        (frame) => {
          setPull((prev) =>
            prev !== null && prev.model === model
              ? { ...prev, percent: pullPercent(frame), note: frame.status }
              : prev,
          );
        },
        controller.signal,
      );
      setFeedback({ kind: "ok", text: `模型 ${model} 下载完成，可点「设为当前模型」启用。` });
      await refreshInstalled();
    } catch (exc) {
      const aborted = exc instanceof DOMException && exc.name === "AbortError";
      setFeedback(
        aborted
          ? { kind: "ok", text: `已取消 ${model} 下载；Ollama 侧可能仍在继续，重新点击即续传。` }
          : { kind: "error", text: exc instanceof Error ? exc.message : String(exc) },
      );
    } finally {
      setPull(null);
      abortRef.current = null;
    }
  }

  async function removeModel(name: string): Promise<void> {
    if (pull !== null || deleting !== null) return;
    if (!window.confirm(`从 Ollama 删除模型「${name}」？删除后需重新下载才能使用。`)) return;
    setDeleting(name);
    setFeedback(null);
    try {
      await deleteOllamaModel(name);
      await refreshInstalled();
      setFeedback({ kind: "ok", text: `模型「${name}」已删除。` });
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setDeleting(null);
    }
  }

  async function useAsCurrent(model: string): Promise<void> {
    if (status === null || switching !== null) return;
    if (
      !window.confirm(
        `切换到本地模型「${model}」会重建该工作区会话（对话历史保留；` +
          `若有写作流在进行，请先等它结束）。继续？`,
      )
    ) {
      return;
    }
    setSwitching(model);
    setFeedback(null);
    try {
      await upsertProvider("ollama", {
        base_url: `${status.base_url}/v1`,
        api_key: "",
        api_key_env: "",
        default_model: model,
        no_auth: true,
      });
      await onProvidersChanged();
      const saved = await saveSettings(slug, {
        writing_language: writingLanguage,
        provider_profile: "ollama",
        model,
      });
      onModelSwitched(saved.provider_profile, saved.model);
      setFeedback({ kind: "ok", text: `已切换到本地模型 ${saved.model}，下一轮对话即生效。` });
    } catch (exc) {
      setFeedback({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setSwitching(null);
    }
  }

  const uiState = uiStateFromStatus(status?.state);
  const inputCls =
    "min-w-0 flex-1 border border-line bg-transparent px-2 py-1.5 text-sm text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none disabled:opacity-40";
  const buttonCls =
    "flex-none border border-line px-3 py-1.5 text-sm text-paper/75 transition-colors hover:border-gold/50 hover:text-gold disabled:opacity-40";

  return (
    <div className="border border-line bg-ink-soft/35 p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-gold">本地模型</h2>
        {pull === null && (
          <button
            type="button"
            onClick={() => void recheck()}
            className="flex-none border border-line px-3 py-1 text-xs text-paper/60 transition-colors hover:text-paper"
          >
            重新检测
          </button>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-paper/40">
        经 Ollama 使用本机运行的小模型（推荐 Qwen 系列），无需 API key；推理完全在本地进行。
      </p>

      {statusError !== null && (
        <p className="mt-3 text-xs leading-relaxed text-overdue">{statusError}</p>
      )}

      {uiState === "unknown" && statusError === null && (
        <p className="mt-3 text-xs text-paper/50">正在检测本机 Ollama…</p>
      )}

      {uiState === "guide" && status !== null && (
        <div className="mt-3 border border-dashed border-line p-4 text-xs leading-relaxed text-paper/70">
          <p>本机尚未安装 Ollama。三步启用本地模型：</p>
          <ol className="mt-2 list-inside list-decimal space-y-1 text-paper/60">
            <li>
              前往{" "}
              <a
                href={status.install_url}
                target="_blank"
                rel="noreferrer"
                className="text-gold underline decoration-gold/40 underline-offset-2"
              >
                ollama.com/download
              </a>{" "}
              下载并安装（Windows 默认一路下一步）；
            </li>
            <li>安装后 Ollama 会自动在后台运行（任务栏可见羊驼图标）；</li>
            <li>回到本页点「重新检测」，即可一键下载推荐模型。</li>
          </ol>
        </div>
      )}

      {uiState === "start" && status !== null && (
        <div className="mt-3 border border-dashed border-line p-4 text-xs leading-relaxed text-paper/70">
          <p>已检测到本机装有 Ollama，但服务未运行。启动方式任选其一：</p>
          <ul className="mt-2 list-inside list-disc space-y-1 text-paper/60">
            <li>启动 Ollama 桌面应用（开始菜单搜 Ollama）；</li>
            <li>或在终端执行 <code className="text-gold-soft">ollama serve</code>。</li>
          </ul>
          <p className="mt-2 text-paper/40">启动后点右上角「重新检测」。</p>
        </div>
      )}

      {uiState === "ready" && status !== null && (
        <>
          <p className="mt-3 text-[11px] text-paper/45">
            Ollama v{status.version || "?"} · {status.base_url}
          </p>

          {pull !== null && (
            <div className="mt-3 border border-line bg-ink p-3">
              <div className="flex min-w-0 items-center justify-between gap-2 text-[11px] text-paper/60">
                <span className="min-w-0 truncate" title={`${pull.model}：${pull.note}`}>
                  {pull.model}：{pull.note}
                </span>
                <span className="flex-none font-mono">
                  {pull.percent === null ? "…" : `${pull.percent}%`}
                </span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden bg-line/60">
                <div
                  className="h-full bg-gold/70 transition-all"
                  style={{ width: `${pull.percent ?? 0}%` }}
                />
              </div>
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="mt-2 border border-line px-2 py-0.5 text-[11px] text-paper/60 transition-colors hover:text-paper"
              >
                取消下载
              </button>
            </div>
          )}

          <p className="mt-4 text-xs text-gold-soft">推荐模型（点击即下载）</p>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {catalog.map((entry) => {
              const has = installed.some((m) => m.name === entry.name);
              return (
                <div key={entry.name} className="border border-line p-3">
                  <div className="flex min-w-0 items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-sm text-paper" title={entry.name}>
                      {entry.name}
                    </span>
                    <span className="flex-none text-[11px] text-paper/40">{entry.params}</span>
                  </div>
                  <p className="mt-1 truncate text-[11px] text-paper/40">
                    {entry.size_label} · {entry.note}
                  </p>
                  <button
                    type="button"
                    disabled={pull !== null}
                    onClick={() => void startPull(entry.name)}
                    className="mt-2 w-full border border-gold/60 px-2 py-1 text-xs text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
                  >
                    {has ? "重新下载" : "一键下载"}
                  </button>
                </div>
              );
            })}
          </div>

          <p className="mt-4 text-xs text-gold-soft">拉取其他模型</p>
          <div className="mt-2 flex gap-2">
            <input
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
              placeholder="如 llama3.2（ollama.com/library 可查模型名）"
              disabled={pull !== null}
              className={inputCls}
            />
            <button
              type="button"
              disabled={pull !== null || customName.trim() === ""}
              onClick={() => void startPull(customName.trim())}
              className={buttonCls}
            >
              下载
            </button>
          </div>

          <p className="mt-4 text-xs text-gold-soft">已安装</p>
          {installed.length === 0 ? (
            <p className="mt-2 text-xs text-paper/50">
              还没有任何已装模型——先从推荐清单下载一个。
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-line/60">
              {installed.map((m) => (
                <li key={m.name} className="flex min-w-0 items-center gap-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-sm text-paper" title={m.name}>
                    {m.name}
                  </span>
                  <span className="flex-none text-[11px] text-paper/45">
                    {formatBytes(m.size_bytes)}
                  </span>
                  <button
                    type="button"
                    disabled={switching !== null || pull !== null}
                    onClick={() => void useAsCurrent(m.name)}
                    className="flex-none border border-gold/60 px-2 py-0.5 text-xs text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
                  >
                    {switching === m.name ? "切换中…" : "设为当前模型"}
                  </button>
                  <button
                    type="button"
                    disabled={deleting !== null || pull !== null}
                    onClick={() => void removeModel(m.name)}
                    className="flex-none border border-line px-2 py-0.5 text-xs text-paper/75 transition-colors hover:border-overdue/60 hover:text-overdue disabled:opacity-40"
                  >
                    {deleting === m.name ? "删除中…" : "删除"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {feedback !== null && (
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

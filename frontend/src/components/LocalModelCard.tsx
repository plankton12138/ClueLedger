/**
 * 本地模型卡（D11 / V03-1.4）：Ollama 三态检测 + 任务化下载/测试 + 已装管理。
 *
 * 三态 UI（服务端 status 恒 200，不可达是结果数据）：
 * - not_installed → 安装引导（官方链接 + 步骤 + 重新检测）；
 * - installed_not_running → 启动提示 + 重新检测；
 * - running → 预置清单一键下载 + 自定义模型名 + 测试调用 + 已装列表。
 *
 * 下载/测试经 /api/local-model-tasks 任务记录：POST 建任务 → 1s 轮询回读
 * → 终态即停。阶段即事实——percent 可空（无进度数只显示阶段），恢复靠
 * ?active=true 服务端重检（不凭旧百分比宣称运行中）；下载完成 ≠ 测试
 * 通过，成功后引导「测试调用」。取消语义如实回报：仅限本应用发起，
 * Ollama 侧拉取可续传。卸载只停轮询不取消服务端任务，刷新后可恢复。
 */

import { useEffect, useRef, useState } from "react";
import {
  cancelLocalModelTask,
  createLocalModelTask,
  fetchActiveLocalModelTasks,
  fetchLocalModelTask,
  isTerminalState,
  taskNotice,
  type LocalModelTaskWire,
} from "../api/localModels";
import {
  deleteOllamaModel,
  fetchOllamaCatalog,
  fetchOllamaModels,
  fetchOllamaStatus,
  saveSettings,
  upsertProvider,
  type OllamaCatalogEntryWire,
  type OllamaModelInfoWire,
  type OllamaStatusWire,
} from "../api/rest";
import { formatBytes, uiStateFromStatus } from "../state/ollama";

type Feedback = { kind: "ok" | "error"; text: string } | null;

interface LocalModelCardProps {
  slug?: string;
  writingLanguage?: string;
  onProvidersChanged: () => Promise<void>;
  onModelSwitched: (profile: string, model: string) => void;
  onUseModel?: (model: string) => Promise<void>;
}

export default function LocalModelCard({
  slug,
  writingLanguage,
  onProvidersChanged,
  onModelSwitched,
  onUseModel,
}: LocalModelCardProps) {
  const [status, setStatus] = useState<OllamaStatusWire | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<OllamaCatalogEntryWire[]>([]);
  const [installed, setInstalled] = useState<OllamaModelInfoWire[]>([]);
  const [pull, setPull] = useState<LocalModelTaskWire | null>(null);
  const [testing, setTesting] = useState<LocalModelTaskWire | null>(null);
  const [customName, setCustomName] = useState("");
  const [switching, setSwitching] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  // 轮询会话号：递增使旧轮询失效（新任务/重开即换会话）；定时器句柄统一清理。
  const sessionRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  function stopPolling(): void {
    sessionRef.current += 1;
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  /**
   * 1s 轮询任务直到终态。apply 落当前快照；终态时 apply(null) 并给出通知。
   * 轮询失败（网络/服务重启清记录）显示归因并停止——不静默吞错误。
   */
  function pollUntilTerminal(
    taskId: string,
    apply: (task: LocalModelTaskWire | null) => void,
    session: number,
  ): void {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = window.setInterval(() => {
      if (sessionRef.current !== session) return;
      fetchLocalModelTask(taskId)
        .then((task) => {
          if (sessionRef.current !== session) return;
          if (isTerminalState(task)) {
            stopPolling();
            apply(null);
            setFeedback(taskNotice(task));
            if (task.action === "download" && task.state === "succeeded") {
              void refreshInstalled();
            }
          } else {
            apply(task);
          }
        })
        .catch((exc: unknown) => {
          if (sessionRef.current !== session) return;
          stopPolling();
          apply(null);
          setFeedback({
            kind: "error",
            text:
              exc instanceof Error
                ? exc.message
                : `任务 ${taskId} 状态回读失败。`,
          });
        });
    }, 1000);
  }

  async function refreshInstalled(): Promise<void> {
    try {
      setInstalled(await fetchOllamaModels());
    } catch (exc) {
      setFeedback({
        kind: "error",
        text: exc instanceof Error ? exc.message : String(exc),
      });
    }
  }

  async function recheck(): Promise<void> {
    if (pull !== null || testing !== null) return; // 任务进行中不重检，避免状态抖动
    setStatusError(null);
    try {
      const current = await fetchOllamaStatus();
      setStatus(current);
      if (current.state === "running") await refreshInstalled();
    } catch (exc) {
      setStatusError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  // 挂载即检测 + 拉预置清单 + 恢复进行中任务（服务端已重检实际状态）。
  // 恢复只接第一个任务：同 model+action 服务端本就复用，单卡同时至多
  // 一个下载一个测试。卸载只停轮询——不取消服务端任务，回来还能接上。
  useEffect(() => {
    void recheck();
    fetchOllamaCatalog()
      .then(setCatalog)
      .catch((exc: unknown) =>
        setFeedback({
          kind: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        }),
      );
    fetchActiveLocalModelTasks()
      .then((tasks) => {
        const task = tasks[0];
        if (task === undefined || isTerminalState(task)) return;
        setFeedback({ kind: "ok", text: `已恢复任务：${task.stage}` });
        if (task.action === "download") {
          setPull(task);
          pollUntilTerminal(task.task_id, setPull, sessionRef.current);
        } else {
          setTesting(task);
          pollUntilTerminal(task.task_id, setTesting, sessionRef.current);
        }
      })
      .catch(() => undefined); // 无进行中任务/服务重启清零：静默即可
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startTask(action: "download" | "test", model: string): void {
    if (model.trim() === "") return;
    stopPolling();
    setFeedback(null);
    const placeholder: LocalModelTaskWire = {
      task_id: "",
      request_id: "",
      action,
      model,
      provider: "ollama",
      base_url: "",
      state: "queued",
      stage: action === "download" ? "准备中" : "准备测试",
      percent: null,
      detail: "",
      error: null,
      created_at: "",
      updated_at: "",
    };
    const apply =
      action === "download"
        ? (t: LocalModelTaskWire | null) => setPull(t)
        : (t: LocalModelTaskWire | null) => setTesting(t);
    apply(placeholder);
    createLocalModelTask({
      requestId: `req_${crypto.randomUUID()}`,
      action,
      model,
      connectionId: "ollama",
    })
      .then((task) => {
        apply(task);
        if (isTerminalState(task)) {
          apply(null);
          setFeedback(taskNotice(task));
          if (action === "download" && task.state === "succeeded") {
            void refreshInstalled();
          }
          return;
        }
        pollUntilTerminal(task.task_id, apply, sessionRef.current);
      })
      .catch((exc: unknown) => {
        apply(null);
        setFeedback({
          kind: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        });
      });
  }

  function cancelTask(task: LocalModelTaskWire): void {
    cancelLocalModelTask(task.task_id)
      .then((stopped) => {
        setFeedback({
          kind: "ok",
          text: stopped.detail !== "" ? stopped.detail : "任务已停止。",
        });
      })
      .catch((exc: unknown) => {
        setFeedback({
          kind: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        });
      });
    // 取消是异步落账：立刻置空进度区，让轮询/终态通知接手后续。
    stopPolling();
    if (task.action === "download") setPull(null);
    else setTesting(null);
  }

  async function removeModel(name: string): Promise<void> {
    if (pull !== null || testing !== null || deleting !== null) return;
    if (
      !window.confirm(
        `从 Ollama 删除模型「${name}」？删除后需重新下载才能使用。`,
      )
    )
      return;
    setDeleting(name);
    setFeedback(null);
    try {
      await deleteOllamaModel(name);
      await refreshInstalled();
      setFeedback({ kind: "ok", text: `模型「${name}」已删除。` });
    } catch (exc) {
      setFeedback({
        kind: "error",
        text: exc instanceof Error ? exc.message : String(exc),
      });
    } finally {
      setDeleting(null);
    }
  }

  async function useAsCurrent(model: string): Promise<void> {
    if (status === null || switching !== null) return;
    if (
      !onUseModel &&
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
      if (onUseModel) {
        await onUseModel(model);
        setFeedback({
          kind: "ok",
          text: `已切换到本地模型 ${model}，后续任务生效。`,
        });
        return;
      }
      await upsertProvider("ollama", {
        base_url: `${status.base_url}/v1`,
        api_key: "",
        api_key_env: "",
        default_model: model,
        no_auth: true,
      });
      await onProvidersChanged();
      const saved = await saveSettings(slug!, {
        writing_language: writingLanguage ?? "中文",
        provider_profile: "ollama",
        model,
      });
      onModelSwitched(saved.provider_profile, saved.model);
      setFeedback({
        kind: "ok",
        text: `已切换到本地模型 ${saved.model}，下一轮对话即生效。`,
      });
    } catch (exc) {
      setFeedback({
        kind: "error",
        text: exc instanceof Error ? exc.message : String(exc),
      });
    } finally {
      setSwitching(null);
    }
  }

  const uiState = uiStateFromStatus(status?.state);
  const pullActive = pull !== null;
  const testActive = testing !== null;
  const busy = pullActive || testActive;
  const inputCls =
    "min-w-0 flex-1 border border-line bg-transparent px-2 py-1.5 text-sm text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none disabled:opacity-40";
  const buttonCls =
    "flex-none border border-line px-3 py-1.5 text-sm text-paper/75 transition-colors hover:border-gold/50 hover:text-gold disabled:opacity-40";

  /** 进度区：阶段即事实，percent 为 null 显示「—」不伪造进度。 */
  function taskProgress(task: LocalModelTaskWire): JSX.Element {
    return (
      <div className="mt-3 border border-line bg-ink p-3">
        <div className="flex min-w-0 items-center justify-between gap-2 text-[11px] text-paper/60">
          <span className="min-w-0 truncate" title={`${task.model}：${task.stage}`}>
            {task.model}：{task.stage}
          </span>
          <span className="flex-none font-mono">
            {task.percent === null ? "—" : `${task.percent}%`}
          </span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden bg-line/60">
          <div
            className="h-full bg-gold/70 transition-all"
            style={{ width: `${task.percent ?? 0}%` }}
          />
        </div>
        <button
          type="button"
          onClick={() => cancelTask(task)}
          className="mt-2 border border-line px-2 py-0.5 text-[11px] text-paper/60 transition-colors hover:text-paper"
        >
          取消{task.action === "download" ? "下载" : "测试"}
        </button>
      </div>
    );
  }

  return (
    <div className="border border-line bg-ink-soft/35 p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-gold">本地模型</h2>
        {!busy && (
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
        经 Ollama 使用本机运行的小模型（推荐 Qwen 系列），无需 API
        key；推理完全在本地进行。
      </p>

      {statusError !== null && (
        <p className="mt-3 text-xs leading-relaxed text-overdue">
          {statusError}
        </p>
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
            <li>
              或在终端执行 <code className="text-gold-soft">ollama serve</code>
              。
            </li>
          </ul>
          <p className="mt-2 text-paper/40">启动后点右上角「重新检测」。</p>
        </div>
      )}

      {uiState === "ready" && status !== null && (
        <>
          <p className="mt-3 text-[11px] text-paper/45">
            Ollama v{status.version || "?"} · {status.base_url}
          </p>

          {pull !== null && taskProgress(pull)}
          {testing !== null && taskProgress(testing)}

          <p className="mt-4 text-xs text-gold-soft">推荐模型（点击即下载）</p>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {catalog.map((entry) => {
              const has = installed.some((m) => m.name === entry.name);
              return (
                <div key={entry.name} className="border border-line p-3">
                  <div className="flex min-w-0 items-center justify-between gap-2">
                    <span
                      className="min-w-0 truncate text-sm text-paper"
                      title={entry.name}
                    >
                      {entry.name}
                    </span>
                    <span className="flex-none text-[11px] text-paper/40">
                      {entry.params}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-[11px] text-paper/40">
                    {entry.size_label} · {entry.note}
                  </p>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => startTask("download", entry.name)}
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
              disabled={busy}
              className={inputCls}
            />
            <button
              type="button"
              disabled={busy || customName.trim() === ""}
              onClick={() => startTask("download", customName.trim())}
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
                <li
                  key={m.name}
                  className="flex min-w-0 items-center gap-3 py-2"
                >
                  <span
                    className="min-w-0 flex-1 truncate text-sm text-paper"
                    title={m.name}
                  >
                    {m.name}
                  </span>
                  <span className="flex-none text-[11px] text-paper/45">
                    {formatBytes(m.size_bytes)}
                  </span>
                  <button
                    type="button"
                    disabled={switching !== null || busy}
                    onClick={() => void useAsCurrent(m.name)}
                    className="flex-none border border-gold/60 px-2 py-0.5 text-xs text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
                  >
                    {switching === m.name ? "切换中…" : "设为当前模型"}
                  </button>
                  <button
                    type="button"
                    disabled={busy || testActive}
                    onClick={() => startTask("test", m.name)}
                    className="flex-none border border-line px-2 py-0.5 text-xs text-paper/75 transition-colors hover:border-gold/50 hover:text-gold disabled:opacity-40"
                  >
                    {testing?.model === m.name ? "测试中…" : "测试调用"}
                  </button>
                  <button
                    type="button"
                    disabled={busy || deleting !== null}
                    onClick={() => void removeModel(m.name)}
                    className="flex-none border border-line px-2 py-0.5 text-xs text-paper/75 transition-colors hover:border-overdue/60 hover:text-overdue disabled:opacity-40"
                  >
                    {deleting === m.name ? "删除中…" : "删除"}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11px] leading-relaxed text-paper/35">
            测试调用经已保存的 Ollama 连接跑一次最小推理——下载完成不等于
            测试通过；通过后再「设为当前模型」。
          </p>
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

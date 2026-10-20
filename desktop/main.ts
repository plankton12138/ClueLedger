/**
 * Hastings Electron 主进程（P9 T10，D8）：
 * 动态端口 → spawn sidecar（PyInstaller 后端）→ 轮询 /api/health →
 * loadURL；退出 taskkill /T /F 杀进程树；sidecar 崩溃 → 错误页 + 重启。
 *
 * 环境变量（dev/冒烟覆盖，均为可选项）：
 * - HASTINGS_DEV_URL        纯 Web dev：跳过 sidecar，直接加载该 URL（Vite）
 * - HASTINGS_SIDECAR        sidecar exe 路径
 * - HASTINGS_STATIC_DIR     前端 dist 目录（sidecar 托管）
 * - HASTINGS_STORIES_ROOT / HASTINGS_CORPUS_ROOT
 * - HASTINGS_CONFIG / HASTINGS_CONFIG_DIR
 * - HASTINGS_MODEL
 * - HASTINGS_CDP            开 remote-debugging-port（自动化冒烟）
 */

import { app, BrowserWindow, ipcMain } from "electron";
import { execFile, spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createServer } from "node:net";
import { join } from "node:path";

type SidecarHandle = { pid: number; stop: () => Promise<void> };

const HEALTH_TIMEOUT_MS = 30_000;

// 决定 userData 为 %APPDATA%/Hastings（D10），必须在 ready 与任何 getPath 前调用。
app.setName("Hastings");

let win: BrowserWindow | null = null;
let sidecar: SidecarHandle | null = null;
let currentPort = 0;
let quitting = false;

function pickPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      const port = typeof addr === "object" && addr !== null ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

function userDataPath(...parts: string[]): string {
  return join(app.getPath("userData"), ...parts);
}

function sidecarExePath(): string {
  return process.env.HASTINGS_SIDECAR ?? join(process.resourcesPath, "sidecar", "hastings-server.exe");
}

function buildSidecarArgs(port: number): string[] {
  const args = ["--port", String(port)];
  const staticDir =
    process.env.HASTINGS_STATIC_DIR ??
    (app.isPackaged ? join(process.resourcesPath, "frontend-dist") : null);
  if (staticDir !== null) args.push("--static-dir", staticDir);
  // 0.4 起作品就是本地 Markdown 目录，服务端不再有 stories/corpus 根目录；
  // 模型来源仍走 --config/--model，作品目录由界面选择。
  if (process.env.HASTINGS_CONFIG) {
    args.push("--config", process.env.HASTINGS_CONFIG);
  }
  if (process.env.HASTINGS_WORKSPACE) {
    args.push("--workspace", process.env.HASTINGS_WORKSPACE);
  }
  if (process.env.HASTINGS_MODEL) args.push("--model", process.env.HASTINGS_MODEL);
  return args;
}

function startSidecar(port: number): SidecarHandle {
  const child = spawn(sidecarExePath(), buildSidecarArgs(port), { windowsHide: true });
  child.stdout.on("data", (chunk: Buffer) => process.stdout.write(`[sidecar] ${chunk.toString()}`));
  child.stderr.on("data", (chunk: Buffer) => process.stderr.write(`[sidecar] ${chunk.toString()}`));
  child.on("exit", (code) => {
    sidecar = null;
    if (!quitting && win !== null && !win.isDestroyed()) {
      // 运行时崩溃：主进程直接切错误页（React UI 不订阅此事件），
      // 保证错误可见 + 重启入口（D8）。
      void showFatal(`本地写作后端意外退出（退出码 ${code ?? "未知"}）。`);
    }
  });

  const handle: SidecarHandle = {
    pid: child.pid ?? 0,
    stop: () =>
      new Promise((resolve) => {
        if (child.pid === undefined || child.exitCode !== null) {
          resolve();
          return;
        }
        execFile(
          "taskkill",
          ["/PID", String(child.pid), "/T", "/F"],
          () => resolve(),
        );
      }),
  };
  sidecar = handle;
  return handle;
}

async function waitHealthy(port: number): Promise<void> {
  const startedAt = Date.now();
  let lastError: unknown = null;
  while (Date.now() - startedAt < HEALTH_TIMEOUT_MS) {
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (resp.ok) return;
    } catch (exc) {
      lastError = exc;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(
    `后端 30 秒内未就绪（最后错误：${lastError instanceof Error ? lastError.message : String(lastError)}）`,
  );
}

function errorPagePath(): string {
  return join(__dirname, "..", "error.html");
}

async function showFatal(reason: string): Promise<void> {
  if (win === null || win.isDestroyed()) return;
  await win.loadFile(errorPagePath(), { query: { reason } });
}

async function boot(): Promise<void> {
  const devUrl = process.env.HASTINGS_DEV_URL ?? null;
  win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 800,
    minHeight: 600,
    backgroundColor: "#141210",
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.on("closed", () => {
    win = null;
  });

  if (devUrl !== null) {
    await win.loadURL(devUrl);
    return;
  }

  currentPort = await pickPort();
  // 数据目录落 %APPDATA%/Hastings（D10）：启动前确保存在，
  // stories 首建文章才会级联创建，corpus 由语料功能直接读取需预先就位。
  mkdirSync(userDataPath("stories"), { recursive: true });
  mkdirSync(userDataPath("corpus"), { recursive: true });
  mkdirSync(userDataPath("config"), { recursive: true });
  startSidecar(currentPort);
  try {
    await waitHealthy(currentPort);
  } catch (exc) {
    await showFatal(exc instanceof Error ? exc.message : String(exc));
    return;
  }
  await win.loadURL(`http://127.0.0.1:${currentPort}/`);
}

async function restartSidecar(): Promise<void> {
  if (sidecar !== null) await sidecar.stop();
  if (currentPort === 0) currentPort = await pickPort();
  startSidecar(currentPort);
  await waitHealthy(currentPort);
  await win?.loadURL(`http://127.0.0.1:${currentPort}/`);
}

if (process.env.HASTINGS_CDP) {
  // 必须在 app ready 前挂载开关。
  app.commandLine.appendSwitch("remote-debugging-port", process.env.HASTINGS_CDP);
}

app.whenReady().then(() => {
  ipcMain.handle("sidecar:restart", () => restartSidecar());
  void boot().catch((exc: unknown) => {
    void showFatal(exc instanceof Error ? exc.message : String(exc));
  });
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (quitting || sidecar === null) return;
  event.preventDefault();
  quitting = true;
  const active = sidecar;
  sidecar = null;
  void active.stop().finally(() => app.quit());
});

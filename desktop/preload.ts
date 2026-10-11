/**
 * Preload（P9 T10，D8）：contextIsolation 下只暴露最小能力——
 * sidecar 退出事件订阅 + 重启 IPC。
 */

import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("hastings", {
  onSidecarExit: (callback: (code: number | null) => void) =>
    ipcRenderer.on("sidecar:exit", (_event, code: number | null) => callback(code)),
  restart: () => ipcRenderer.invoke("sidecar:restart"),
});

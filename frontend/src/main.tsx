import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { useLayoutStore } from "./state/layout";

// 首次绘制前应用本机偏好；订阅保证弹层与工作台始终使用相同字号。
function applyFontScale(scale: number) {
  document.documentElement.style.setProperty("--font-scale", String(scale / 100));
}
applyFontScale(useLayoutStore.getState().fontScale);
useLayoutStore.subscribe((state, previous) => {
  if (state.fontScale !== previous.fontScale) applyFontScale(state.fontScale);
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

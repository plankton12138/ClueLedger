import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "../index.css";
import "./workbench.css";
import { WorkbenchApp } from "./App";
import { applyTheme, resolveTheme } from "./theme";

applyTheme(resolveTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WorkbenchApp />
  </StrictMode>,
);

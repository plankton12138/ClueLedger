import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "../index.css";
import "./workbench.css";
import { WorkbenchApp } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WorkbenchApp />
  </StrictMode>,
);

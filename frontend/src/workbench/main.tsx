import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Launch } from "./Launch";
import { Runs } from "./Runs";
import { Tasks } from "./Tasks";
import { SearchLink, taskKey } from "./ui";
import "./workbench.css";

function Workbench() {
  const [params, setParams] = useState(() => new URLSearchParams(window.location.search));
  useEffect(() => {
    const update = () => setParams(new URLSearchParams(window.location.search));
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  const page = params.get("page") ?? (taskKey(params) && !params.has("run_id") ? "tasks" : "runs");
  return <div className="wb-shell">
    <aside className="wb-side">
      <div className="wb-brand"><span aria-hidden="true">线</span><div><strong>线簿</strong><small>ClueLedger / 评测工作台</small></div></div>
      <nav aria-label="工作台">{Object.entries({ tasks: "任务库", launch: "运行评测", runs: "运行与复盘" }).map(([id, label], i) =>
        <SearchLink key={id} to={{ page: id }} aria-current={page === id ? "page" : undefined}><small aria-hidden="true">0{i + 1}</small>{label}</SearchLink>,
      )}</nav>
      <p className="wb-local">仅限本地 · 开发与评测</p>
    </aside>
    <main>{page === "tasks" ? <Tasks params={params} /> : page === "launch" ? <Launch params={params} /> : <Runs params={params} />}</main>
  </div>;
}

createRoot(document.getElementById("root")!).render(<Workbench />);

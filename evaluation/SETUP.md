# 评测平台启动

本机独立服务，不属于产品。默认地址 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。扫描 `data/evaluation/runs`、`evaluation/cases` 与 suites/envs/evaluators 资产。

## 前置

仓库根目录已 `uv sync`。工作台共用产品侧 Trace 组件，打开前先构建前端：

```powershell
npm --prefix frontend run build
```

未构建时会回退旧版静态页（仅报告表）。

## 启动工作台

```powershell
uv run python -m evaluation workbench
```

打开后顶栏三区：**资产** / **运行** / **报告与质检**。在「运行 · Pipeline」可选 Suite × Env 触发冒烟或门禁（需本机已启动本地模式服务）。

可选参数：

| 参数 | 作用 |
| --- | --- |
| `--run <名字或路径>` | 直接打开某次运行（如 `smoke-1`） |
| `--runs-root <目录>` | 运行根目录；默认 `data/evaluation/runs` |
| `--cases` / `--works` | 案例与基础作品目录 |
| `--host` / `--port` | 监听地址；默认 `127.0.0.1:8765` |

## 尚无运行结果时

先启动本地模式产品服务，再在工作台 Pipeline 开始，或用 CLI：

```powershell
uv run python -m hastings.server
uv run python -m evaluation run --base-url http://127.0.0.1:8000 --suite smoke_revise --out data/evaluation/runs/smoke-1
uv run python -m evaluation workbench --run smoke-1
```

失败先修产品或案例，再单例复跑。命令一览见 [README](README.md)。

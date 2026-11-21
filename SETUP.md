# 线簿 · 从源码运行

需要 **Python 3.12+**、**Node.js**、**uv**。以下命令均在仓库根目录执行。

## 安装依赖

```powershell
uv sync
npm --prefix frontend ci
```

## 本地模式（默认）

构建前端并由后端托管，打开写作台：

```powershell
npm --prefix frontend run build
uv run python -m hastings.server
```

浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。作品在「作品 → 打开 / 新建」中选择本地文件夹；模型在「设置 → 模型设置」中配置。

## 前后端分开开发

终端 1 — 后端：

```powershell
uv run python -m hastings.server
```

终端 2 — 前端热更新：

```powershell
npm --prefix frontend run dev
```

开发服务器把 `/api` 转到本机 8000。细节见 [frontend/README.md](frontend/README.md)。

## Server 模式（账号与订阅）

需要登录、账号隔离或订阅额度时，显式指定账号库：

```powershell
uv run python -m hastings.server --mode server --database .\data\clueledger.sqlite3
```

未配置 SMTP 时，验证码邮件写到应用状态目录下的 `mail-outbox/`。

## 相关文档

| 文档 | 内容 |
| --- | --- |
| [README.md](README.md) | 产品能力与使用路径 |
| [frontend/README.md](frontend/README.md) | 前端开发与构建 |
| [dev-notes/README.md](dev-notes/README.md) | 各版本开发记录 |

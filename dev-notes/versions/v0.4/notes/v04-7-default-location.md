# V04-7 新作品默认存放在文档目录

目标：新建作品不再靠作者自己想路径，默认落在系统「文档」目录下，避免作品被建到源码目录或临时目录里。

## 实际改动

- **服务端解析默认位置**：新增 `workspace/locations.py`。Windows 走系统已知文件夹
  （`SHGetFolderPathW`，会跟随 OneDrive 之类的重定向），其他平台用 `~/Documents`，
  两者都取不到则退回用户主目录——位置一定存在，新建才不会因为目录缺失直接失败。
- **新接口**：`GET /api/file-workspaces/default-location` 返回 `{"root": "<文档目录>"}`。
  只给父目录，不替作者起作品名。
- **界面预填**：目录对话框勾选「新建作品」且路径为空时，填入 `<文档目录>/未命名作品`，
  输入框下方注明默认目录；占位符也跟着默认目录走。打开已有目录的行为不变，
  不会被预填成文档目录。
- **前端接口测试**：`fetchDefaultLocation` 的 URL 与返回值钉在 `api/workspace.test.ts`。

## 接口与数据影响

- 新增只读接口 `/api/file-workspaces/default-location`；已有接口与请求体不变。
- 不写任何配置项：默认位置是运行时解析的事实，不是可配置项；作者想放别处，直接在对话框里改路径。

## 实现取舍

- **为什么放在服务端**：浏览器只能看到自己沙箱里的路径，拿不到本机主目录；文件本来就由本机服务读写，位置解析放在同一侧才不会出现「前端猜一个路径、后端再拒绝」。
- **为什么不用 `Path.home()/"Documents"` 一把梭**：Windows 的文档目录常被 OneDrive 接管，猜路径会得到不存在的目录或错误位置；系统已知文件夹是唯一可靠的来源，猜法只作为非 Windows 平台的兜底。
- **为什么不是「自动创建作品」**：建目录是写盘动作，作者应该看见最终路径；界面只预填，提交仍按原流程走 `create=true`。

## 验证

- `uv run pytest tests/workspace -q`：142 项通过（新增 3 项位置解析、1 项接口）。
- `uv run mypy src/hastings`、`uv run ruff check src/hastings tests/workspace`：无问题。
- 前端：`npx tsc --noEmit` 通过；`npx vitest run` 10 项通过（含新增的默认位置接口用例）。
- 实测：`curl /api/file-workspaces/default-location` → `{"root":"C:\\Users\\plankton\\Documents"}`；
  前端 `npm run build` 已刷新 `frontend/dist`。

## 偏差与未完成

- 对话框没有系统目录选择器（需要桌面壳提供原生对话框），仍是手填路径 + 预填。
- 未在浏览器里点这一轮「新建」流程取证；接口与单元层已验证，界面沿用同一 `open(path, create)` 调用。

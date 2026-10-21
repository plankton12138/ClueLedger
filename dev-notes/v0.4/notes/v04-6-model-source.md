# V04-6 模型来源单一化与失败可见

目标：把「模型从哪来」收成一条产品路径，并让模型侧失败不再假装成功。

## 实际改动

- **删除环境变量引用**：`ProviderProfile.api_key_env`、`env_fallback_config()` 与
  `load_provider_book` 里的回落分支全部移除。缺 `config.yaml` 等于没有档案：服务照常启动，
  发送消息的那一轮返回 `MODEL_NOT_CONFIGURED`。
- **旧字段给迁移提示**：`config.yaml` 里仍写 `api_key_env` 时抛 `ProviderConfigError`，
  指出是哪个档案，并说明「删掉该字段、把 key 写进 `api_key`」。不靠 `extra="forbid"`
  的通用报错，是因为通用文案说不出「这个字段为什么没了」。
- **失败可查询**：`WorkspaceService._consume` 遇到带 `error_message` 的助手消息（厂商报错、
  key 不对、端点不通）时抛 `MODEL_CALL_FAILED`，由 `_run` 落 `error` 事件并把
  `run_finished` 标成 `failed`。此前的行为是记 `completed` + `changed_paths=[]`，
  作者看到「已完成、没改动」，真正原因丢失。
- **配置模板与诊断脚本**：`config.example.yaml` 改成「一个档案 = 端点 + key + 默认模型」；
  新增 `scripts/check_model.py`，按同一条装配路径发一句请求，输出档案、模型、key 尾四位、
  回复与用量，失败按三要素退出 1。
- **依赖与模板**：移除 `python-dotenv`，删除 `.env.example`；桌面壳不再从
  `HASTINGS_MODEL` 传 `--model`，模型名只认 `config.yaml`。
- **状态目录也改为启动参数**：`HASTINGS_STATE_DIR` 换成 `--state-dir`，默认仍是
  `~/.hastings`。这样「配置」只剩命令行参数与 `config.yaml` 两个入口，服务不再读任何
  进程环境变量；E2E 的 webServer 命令相应改成传参。

## 接口与数据影响

- `config.yaml` 不再接受 `api_key_env`；已有配置需要把 key 改成直填一次（迁移后磁盘只留
  `credential_ref`）。这是有意的不兼容：保留字段会让「配好了」与「有 key」变成两件事。
- 启动参数新增 `--state-dir`；原先靠 `HASTINGS_STATE_DIR` 指定状态目录的启动脚本需要改传参。
- 轮次事件新增可能出现的 `error`（`code="MODEL_CALL_FAILED"`）与
  `run_finished.status="failed"`；事件结构本身未变。
- `/api/file-workspaces/*` 路由与请求体不变。

## 实现取舍

- **为什么不给环境变量留后门**：作者在自己的机器上用产品，不会去设 `OPENAI_API_KEY`；
  让某个字段能引用进程环境，只有开发期方便，代价是「同一份配置在不同机器上行为不同」，
  并且把运维概念放进了产品配置。
- **为什么缺配置不阻止启动**：写作台的其他部分（目录、阅读、Git、搜索）不依赖模型；
  启动即失败会让「先看看作品」都用不了。把错误推到真正需要模型的那一轮。
- **为什么在 `_consume` 抛而不是在 provider 层**：provider 只负责流式事件，是否算一轮失败
  属于轮次语义；放在事件翻译处，停止与失败两种收尾仍走同一段代码。

## 验证

- `uv run pytest tests/workspace -q`：138 项通过（新增 6 项：缺文件/空文件无档案、
  环境变量补不出 key、`api_key_env` 被拒绝、直填与 `credential_ref` 往返、
  仅 `credential_ref` 未物化不可用、模型报错让轮次失败）。
- `uv run mypy src/hastings scripts/check_model.py`、`uv run ruff check src/hastings tests/workspace scripts/check_model.py`：无问题。
- 真实模型：`scripts/check_model.py` 一句请求正常；经 HTTP 接口一轮写作写出
  `正文/0001 封山的旅馆.md`（723 字）与 `设定集/真相.md`，产生一次 Git 提交。证据见
  [real-model-2026-10-20.md](../artifacts/real-model-2026-10-20.md)。

## 偏差与未完成

- 浏览器界面未用真实模型点过：本轮取证走 HTTP 接口，界面渲染仍是脚本化假 provider。
- 仍没有模型设置界面，端点与 key 只能手改 `config.yaml`。
- 验证机上有系统代理，直连本机服务的第二个请求会被代理改写请求行（`http%3A//…`）；
  排除代理后验证正常。产品侧不受影响（本机 provider 已 `trust_env=False`），但这类环境
  差异会让「本机接口偶发 404」看起来像应用 bug，记录在此避免重复排查。

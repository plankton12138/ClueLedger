# 真实模型验证（2026-10-20）

核对对象：模型来源改为「只读 `config.yaml`」之后，产品是否真的能用真实厂商跑完一轮写作。
环境：Windows，`uv run`，端点 `https://api.deepseek.com/v1`，模型 `deepseek-flash`。

## 1. 一句请求

```powershell
uv run python scripts/check_model.py
```

```text
档案：deepseek｜端点：https://api.deepseek.com/v1
模型：deepseek-flash｜key：…114a
回复：你好！
用量：input=47 output=63 ... total_tokens=110
```

进程环境里没有 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `HASTINGS_MODEL`：key 由系统凭据
存储提供，说明「配置只从文件来」这条链路成立。

## 2. 一轮写作（经 HTTP 接口）

建作品（`POST /api/file-workspaces`，`create: true`）→ 建对话 → 发轮次：

> 请直接写第一章，不要只给提纲。场景：暴雪封山的旅馆，清晨发现一位住客死在反锁的房内。
> 大约 600 字，第三人称，节奏克制。先在 `设定集/真相.md` 写下本章涉及的真相要点
> （凶手是谁、手法），再创建 `正文/0001 封山的旅馆.md` 写入正文。

事件序列（`GET .../messages`）：

```text
turn_started
tool_started  list_files
tool_started  read_file  设定集/真相.md
tool_started  read_file  设定集/线索.md
tool_started  read_file  设定集/角色小传.md
tool_started  write_file 设定集/真相.md
tool_started  write_file 正文/0001 封山的旅馆.md
assistant_message
run_finished status=completed recording=committed
             changed_paths=["正文/0001 封山的旅馆.md","设定集/真相.md"]
             commit=da227e0
```

作品目录：

```text
.gitignore  正文/0001 封山的旅馆.md  设定集/真相.md  设定集/线索.md  设定集/角色小传.md
```

`正文/0001 封山的旅馆.md` 正文 723 字；`git log` 两条：

```text
da227e0 写作：请直接写第一章，不要只给提纲。场景：暴雪封山的旅馆，清晨发现一位住客死在反锁的房
01f8a53 初始化作品目录
```

## 3. 凭据迁移

`config.yaml` 里写入直填 key 后启动一次，再读磁盘：

```yaml
providers:
- name: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: ''
  default_model: deepseek-flash
  credential_ref: hastings:provider:deepseek
```

明文已不在文件里；运行时 key 从 Windows 凭据管理器读出（`…114a` 与迁移前一致）。

## 4. 过程中确认的两件事

- **环境变量从未进入产品**：同一份 `config.yaml` 在没有 `OPENAI_API_KEY` 的终端里可用，
  说明旧版 `.env` 只是开发习惯，不是产品能力；`.env` 与 `.env.example` 已随之退出。
- **厂商报错曾被当成成功**：验证期间请求过一次错误端点，拿到 `502 Bad Gateway`，但轮次
  记录为 `completed`、`changed_paths=[]`、回复为空——错误被吞掉了。现已改为落
  `MODEL_CALL_FAILED` 事件、`run_finished` 状态 `failed`（`tests/workspace/test_chat.py`
  覆盖）。

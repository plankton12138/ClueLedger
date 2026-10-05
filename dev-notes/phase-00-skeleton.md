# Phase 0 构建笔记：项目骨架（Skeleton）

> 对应 [phase-00-skeleton-plan.md](phase-00-skeleton-plan.md)，2026-10-05 完成并关闭。
> 结论：**R5 验收通过，P0 正式关闭，可进入 P1。**

## 1. 交付内容

| 交付 | 提交 | 说明 |
|---|---|---|
| 仓库基线 | `5619cf5` `56f50bc` `670f016` `4a0cfbe` | 定名文档、P0 计划、.gitignore/.python-version、清理 114 个误入库 .pyc |
| 构建配置 | `c47183f` | pyproject.toml + uv.lock |
| 包骨架 | `0ac7eeb` | `hastings` 0.1.0 + bible/tools/rag/server/eval 五个占位子包 + py.typed |
| hello-world | `b12cd32` | tests/test_hello_harness.py + examples/hello.py |
| CI | `17b0707` | 单 job 四道门，`uv sync --dev --locked` |

## 2. 关键决策

### 2.1 依赖最小化（直接证据）

全量 import 扫描结论落地：`dependencies = ["httpx[socks]>=0.27", "pydantic>=2.11"]`，dev 组 `anyio/mypy/pytest/ruff`。`uv pip list` 共 25 包，**无 textual/rich/pillow/typer**（上游因 tau_coding 引入的四个重依赖全部甩掉）。socksio 在 Windows 安装正常，计划 §5 的备选预案未触发。

### 2.2 tau_coding 排除方式（三处配置）

保留源码参考但移出全部工程链路：wheel `packages` 不含它；ruff `extend-exclude = ["src/tau_coding"]`；mypy `packages = ["tau_ai", "tau_agent", "hastings"]` 白名单天然排除。pytest `testpaths = ["tests"]` 不触及它。

### 2.3 mypy 两处 overrides（环境差异豁免，不动上游一行）

1. `tau_agent.session.storage`：fcntl/msvcrt 平台分支在 Windows 必报 `attr-defined`/`unused-ignore`（上游 CI 在 Linux 不触发，本地豁免在 Linux 为无操作）；
2. `tau_ai.openai_codex`：`cast()` 在 mypy 2.4 改进的字面量收窄下判 `redundant-cast`（上游 uv.lock 锁 mypy 2.1 不触发；本仓库 lock 到 2.4，CI 同受影响，故豁免对 CI 同样必要）。

两处的动机注释直接写在 pyproject.toml 里，保证"豁免可审计"。

## 3. 踩过的坑

1. **基线提交误含 114 个 .pyc**：复制上游源码时把 `__pycache__` 一起commit了。T1 删除并单独 `cleanup:` 提交，.gitignore 防复发。教训：跨仓库复制源码后先 `git status --ignored` 体检再提交。
2. **hastings 缺 py.typed**：mypy strict 报 "cannot be type checked due to missing py.typed marker"。补空 `src/hastings/py.typed`（上游 tau_ai/tau_agent 本就带）。
3. **ruff I001**：pytest（third-party）与 tau_agent（first-party）之间需空行分组，`ruff check --fix` 自动修。
4. **.pyc 干扰 T7 diff**：跑过 pytest 后 src 下重新生成 .pyc，与上游 .pyc 形成二进制噪音。T7 改用"逐 .py 文件 SHA-256 对比"取证，结论更硬。

## 4. 验证输出存档

四道质量门（提交前复跑，全绿）：

```text
uv run ruff check .        → All checks passed!
uv run ruff format --check → 48 files already formatted
uv run mypy                → Success: no issues found in 42 source files
uv run pytest              → 1 passed
```

T7 上游零改动（逐文件哈希对比）：

```text
tau_agent : 本地 16 个 .py / 上游 16 个 .py / 差异 0 个
tau_ai    : 本地 20 个 .py / 上游 20 个 .py / 差异 0 个
```

hello-world 事件流（`uv run python examples/hello.py`，17 步完整契约）：
agent_start → turn_start → user 消息 → assistant（tool_call）→ tool_execution_start/end → toolResult 消息 → turn_end → 第二轮 assistant 最终文本 → turn_end → agent_end。

## 5. 评审结论

- **R1**：每次提交前四道门全绿，9 笔提交均满足。
- **R2**：T7 哈希对比 0 差异；pyproject 三处配置确认 tau_coding 不在任何链路。
- **R3**（TRAE-code-review，双验证者交叉核对）：**0 blocker，2 建议**
  1. `provider.calls[0][3]` 魔数索引 → 已修（注释说明四元组结构）；
  2. CI 仅 ubuntu-latest → **已知留白**：与上游一致；Windows 平台分支由本地四道门覆盖，后续如需 Windows CI 再加 runner。
- **R4**：CP1（uv pip list 25 包、依赖表逐项核对）、CP2（断言密度对照上游 test_agent_harness.py：事件序列 + 消息历史 + 工具透传三点全覆盖）均通过。

## 6. R5 验收：DoD 逐条打勾

- [x] `git log` 有基线提交，工作树干净（9 笔原子提交）
- [x] `uv sync --dev` 成功，`uv.lock` 已提交（c47183f）
- [x] `uv pip list` 无 textual/rich/pillow/typer
- [x] `import hastings` 成功（0.1.0），五个子包占位存在
- [x] 测试断言事件序列，FakeProvider 回放通过（1 passed）
- [x] `uv run python examples/hello.py` 打印完整事件流
- [x] 四道质量门全绿
- [x] `.github/workflows/ci.yml` 存在且语法有效（沿用上游动作固定 SHA）
- [x] T7 上游 diff 为空（输出见 §4）
- [x] R3 完成，blocker 清零
- [x] 本构建笔记发布

**验收结论：通过（带 1 项已知留白：Windows CI runner）。P0 关闭。**

## 7. 对 P1 的接口确认

- `AgentHarnessConfig(provider, model, system, tools, ...)` 已验证可换工具集即得新 agent；
- FakeProvider 回放 + 事件序列断言的测试范式已就位，P1 的 Bible 状态机工具直接套用；
- mypy strict 对 hastings 生效，P1 的 Pydantic 数据模型从第一天起全程类型覆盖。

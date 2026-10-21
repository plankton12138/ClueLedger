# V04-9 模型连接：真实验证材料

日期：2026-10-21。供应商：DeepSeek（作者已有凭据，存于系统凭据存储）。环境：Windows，仓库根目录，`uv run`。

## 1. 隔离方式

- 配置用 `config.yaml` 的副本（`copy → data/v04-9-verify/config.yaml`），服务以 `--config` 指向它，仓库里的真实配置与其凭据引用不受影响。
- 作品是新建的独立测试作品 `data/v04-9-verify/testbook`，不在真实作品上取证。
- 状态目录也指向验证目录（`--state-dir`），不污染 `~/.hastings`。
- 凭据只从系统凭据存储读出后放进请求体，不打印、不落盘。

命令：

```text
uv run python -m hastings.server --config data/v04-9-verify/config.yaml \
    --state-dir data/v04-9-verify/state --port 8765
uv run python dev-notes/v0.4/artifacts/v04-9-verify.py \
    --config data/v04-9-verify/config.yaml --workspace data/v04-9-verify/testbook
```

（进程重启后追加 `--phase restart` 再跑一次。）

注意：本机环境有 `HTTP_PROXY`，验证脚本访问 `127.0.0.1` 必须 `trust_env=False`，否则请求被代理接手并返回 404。产品的本机地址请求已按这一行为处理（本机不经系统代理）。

## 2. 主流程输出

```text
== 连接列表 ==
revision=f1c4b32b5cee916b
- deepseek | deepseek | openai-completions | 1 模型 | has_key=True | key=…114a | 存储=system
当前选择：{"connection_id": "deepseek", "model": "deepseek-flash", "source": "automatic", "available": true, "reason": ""}

== 模型发现（真实接口） ==
code=None complete=True 数量=2
前 5 个： ['deepseek-flash', 'deepseek-v4-pro']

== 响应测试 / 工具测试（模型 deepseek-flash） ==
response: response=passed tool_call=untested 响应测试通过。
tool: response=untested tool_call=passed 工具测试通过。

== 新增连接（复用同一份凭据） ==
新建 conn_5a48402f key=…114a 选择未变：automatic
设为当前： {'connection_id': 'conn_5a48402f', 'model': 'deepseek-flash', 'source': 'saved', 'available': True, 'reason': ''}

== 一轮写作（真实模型 → 文件 → Git） ==
轮次快照： {"connection_id": "conn_5a48402f", "connection_name": "验证用连接", "model": "deepseek-flash"}
工具过程： ['read_file', 'read_file', 'read_file', 'list_files', 'write_file']
结果： {"status": "completed", "recording": "committed", "changed_paths": ["正文/0001 雨夜.md"], "commit": "a1e5868315cde6f89e41243dc51be53916c23035"}
章节： [('正文/0001 雨夜.md', 709)]
Git 提交： [('a1e58683', '写作：写第一章，标题「雨夜」，200 字左右：侦探在雨夜抵达一家小旅馆。'), ('cdc26c22', '初始化作品目录')]

== 删除临时连接（选择被清空） ==
删除后选择： none
恢复选择： {'connection_id': 'deepseek', 'model': 'deepseek-flash', 'source': 'saved', 'available': True, 'reason': ''}
```

## 3. 重启后

```text
== 连接列表 ==
revision=96fa336054e9acae
- deepseek | deepseek | openai-completions | 1 模型 | has_key=True | key=…114a | 存储=system
当前选择：{"connection_id": "deepseek", "model": "deepseek-flash", "source": "saved", "available": true, "reason": ""}
重启后选择一致： deepseek deepseek-flash
```

## 4. 产物

`data/v04-9-verify/testbook/正文/0001 雨夜.md`（709 字节，首段）：

```text
# 雨夜

雨是傍晚下起来的，到镇口已经连成一片。

沈砚把伞收在旅馆门廊下，伞骨断了三根，水顺着裂口滴在鞋面上。招牌只剩两个字认得出——"旅"和"社"，中间的字被风刮走了。屋里亮着一盏灯，柜台后有人趴着打盹。

他敲了两下门框。
```

## 5. 另一条路径：不启动写作台核对

```text
> uv run python scripts/check_model.py
连接：deepseek（deepseek）｜端点：https://api.deepseek.com/v1
协议：openai-completions｜模型：deepseek-flash｜key：…114a
回复：你好！
用量：input=47 output=82 ... total_tokens=129
```

## 6. 本次未覆盖

- Anthropic、Google、通义千问、Moonshot、智谱、OpenRouter 的真实服务调用：没有对应凭据，仅验证到适配契约（受控传输）。
- 浏览器内逐屏操作：界面由构建与状态测试覆盖，真实链路取证在接口层完成。

验证脚本保留在 `dev-notes/v0.4/artifacts/v04-9-verify.py`，它调用的是界面使用的同一组接口。

# 模型思考深度

日期：2026-10-26。状态：已实施；版本保持 `0.8.0`。

## 设计与边界

模型选择决定使用哪种能力，思考深度决定这次请求投入多少推理。作者需要在响应时间、
用量与任务复杂度之间选择，因此在对话栏的模型选择旁提供一个下拉框，不增加独立设置页面。
提高档位不保证结果一定更好；不同模型的档位也不代表相同的推理用量。

- 默认：不发送思考参数，保留供应商原有行为；旧配置自动使用此状态。
- 低、中、高、极高、最大、极低、关闭：仅展示当前模型已接线的档位，不把所有选项交给所有模型。
- 修改保存后从下一轮生效；受理快照保存本轮的模型与档位，运行中的请求不读取新设置。
- 这是应用级当前选择，重启后恢复；切换连接或模型时恢复默认，不保存每模型偏好。
- 设置失败保留原选择并显示错误；版本冲突可直接重新读取配置。

本次接通当前配置所用的 DeepSeek 与 OpenAI / ChatGPT 订阅模型。
DeepSeek Flash、V4 / V4.1 Flash / Pro 提供关闭、低、高、最大。
GPT-6.1 Sol、GPT-6 Astra 提供低、中、高、极高、最大；GPT-6 Sol / Luna 和 GPT-5.6 系列
另提供关闭。旧 GPT 推理模型按各自规则展示，不向 GPT-4.1、聊天快照或未知别名推断能力。
其他供应商与尚未接线的协议保持默认，下拉框禁用；没有声明档位不等于供应商本身不支持思考。

能力规则集中在 `src/hastings/server/model_thinking.py`，界面不自行根据模型名生成选项。
本次没有接入在线目录的能力元数据；新增型号、网关别名或供应商需要先核对并更新这份规则。

## 接口与存储

沿用 `PUT /api/model-selection` 与 `If-Match`，新增可空字段 `thinking_level`。
省略或传 `null` 均恢复供应商默认；传已知但不支持的档位返回 400，错误码为
`MODEL_THINKING_LEVEL_INVALID`，不改变文件或内存选择。
`GET /api/model-selection` 与连接配置响应中的 `selection` 同时返回：

```json
{
  "connection_id": "conn_example",
  "model": "deepseek-flash",
  "source": "saved",
  "available": true,
  "reason": "",
  "thinking_level": "high",
  "thinking_levels": ["off", "low", "high", "max"]
}
```

`config.yaml` 仅在显式选择且非默认时增加 `active_thinking_level`。
恢复默认、清空选择或删除当前连接时移除该值；不改变凭据存储。
启动参数临时覆盖模型时不继承另一个已保存选择的思考深度。

OpenAI Responses 使用 `reasoning.effort`，Completions 使用 `reasoning_effort`；
界面的 `off` 映射为协议值 `none`。DeepSeek Completions 使用 `thinking.type` 开关，
开启时另传 `reasoning_effort`。默认均不添加参数，避免误把默认解释成关闭。
订阅连接继续使用原有凭据续期、流式请求与不传输出上限的行为。

同时修正适配器的两处接线：Responses 显式 `none` 不再被丢弃；Completions 的结束校验
使用该协议的 `finish_reason`，避免读取仅 Responses 解析器持有的字段。

## 验证

确定性测试覆盖能力差异、非法档位拒绝且不写盘、版本冲突、默认省略、重启恢复、模型切换、
清空选择和每轮快照隔离；通过真实适配器与替代传输检查 DeepSeek、OpenAI 两种协议及订阅请求。
这些测试不调用模型，不属于模型效果评测。

浏览器专项检查下拉选项、保存与刷新恢复、切换模型、恢复默认、冲突恢复、窄栏与 150% 字号。
本轮执行结果：

| 验证 | 结果 |
|---|---|
| `uv run pytest tests/workspace -q` | 911 项通过，含新增思考深度 31 项 |
| 前端 `npm test` | 25 个文件、278 项通过 |
| `playwright test e2e/model-thinking.spec.ts --project=workspace` | 3 项通过，使用独立端口 8938；截图复查后调整字号伸缩并再次通过 |
| 受影响后端的 Ruff / mypy、前端生产构建 | 通过 |

截图：[默认栏宽](../artifacts/model-thinking/thinking-depth-1440.png)、
[窄对话栏](../artifacts/model-thinking/thinking-depth-narrow.png)、
[640px 窗口与 150% 字号](../artifacts/model-thinking/thinking-depth-640.png)。
未执行在线模型效果评测或桌面安装包验证。

## 参数依据

本次核对日期为 2026-10-26，后续新增模型须重新核对。

- [OpenAI 推理参数](https://developers.openai.com/api/docs/guides/reasoning)：参数位置及模型差异。
- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)、[GPT-5](https://developers.openai.com/api/docs/models/gpt-5)：对应型号档位。
- [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[模型目录](https://api-docs.deepseek.com/api/list-models/)：思考开关、档位与当前模型声明。

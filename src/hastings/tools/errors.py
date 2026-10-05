"""工具层错误类型。"""


class ToolInputError(ValueError):
    """工具参数不合法。

    agent loop 会捕获工具异常并转为 is_error=True 的工具结果喂回模型
    （tau_agent/loop.py），因此消息文案必须让模型能据此自我修正。
    """


__all__ = ["ToolInputError"]

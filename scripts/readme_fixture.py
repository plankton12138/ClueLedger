"""README 截图的隔离示例正文与对话；不调用模型。"""

import sys
from pathlib import Path

from hastings.workspace.files import Workspace
from hastings.workspace.state import ConversationStore, conversation_root

book, state, wid = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
workspace = Workspace.open(book)
chapters = [
    (
        "0001 雪夜来客.md",
        """# 第一章 雪夜来客

最后一班车离开后，雪把山路埋住了。林岚站在旅馆门口，看见玻璃里映出一盏还亮着的灯。

“明早能下山吗？”她问。

老板没有立刻回答，只把柜台上的收音机调小。天气预报说，这场雪要下到后天。

门厅里另有三个人。一个在擦眼镜，一个翻着过期的报纸，还有一个低头看手里的信。林岚进来时，他们都抬了头。

“你也收到了？”拿信的人问。

林岚把行李放在脚边。信封就在大衣口袋里，封口已经拆过。她没有拿出来。

老板从柜台后取下一把钥匙。“二楼，靠走廊尽头。晚饭七点，不用等钟响。”

林岚看了一眼墙上的钟。分针停在十一和十二之间，秒针却还在走。

上楼时，第三阶木板轻轻响了一声。走到转角，她又回头看了一眼：拿信的人已经把信藏进了报纸。

走廊尽头的窗户没有关严。她伸手去推，发现窗台上有一个干净的方印，像是刚有人拿走了一张照片。
""",
    ),
    (
        "0002 晚宴上的空位.md",
        "# 第二章 晚宴上的空位\n\n"
        "七点钟，餐桌旁多摆了一把椅子。老板说还有一位客人没下来。\n\n"
        "林岚数了数杯子。少的不是人，是一只杯子。\n",
    ),
    (
        "0003 没有响的木阶.md",
        "# 第三章 没有响的木阶\n\n"
        "午夜，林岚听见走廊尽头的门开了。有人下楼，却没有踩响第三阶木板。\n\n"
        "天亮以后，她在雪地里找到了半张照片。\n",
    ),
]
for name, body in chapters:
    workspace.write(f"正文/{name}", body, expected_digest=None)
store = ConversationStore(conversation_root(state, wid))
info = store.create_with_first_turn(
    text="先讨论开篇：保留雪夜封路的氛围，让信封和停住的钟各留下一个疑问，暂时不改正文。",
    request_key="readme-example",
    turn_id="t-example",
)
reply = (
    "可以把开篇的注意力放在三个变化上：\n\n"
    "1. **道路封闭**：来客意识到自己暂时无法离开。\n"
    "2. **信封被认出**：一句‘你也收到了？’把陌生人连在一起。\n"
    "3. **钟的异常**：先让读者记住，不急着解释。\n\n"
    "第三阶木板可以在后文回应：有人熟悉旅馆，知道怎样避开它。"
    "这样环境细节也会推动调查。\n\n本轮仅讨论，正文保持原样。"
)
message = {"turn_id": "t-example", "message_id": "m-example"}
block = {**message, "block_id": "m-example:text:0", "kind": "text", "order": 0}
store.append_batch(
    info.conversation_id,
    [
        {"type": "assistant_started", **message},
        {"type": "assistant_block_started", **block},
        {"type": "assistant_block_delta", **block, "part": 0, "text": reply},
        {
            "type": "assistant_block_finished",
            **block,
            "text": reply,
            "last_part": 0,
            "completion": "completed",
        },
        {"type": "assistant_message", **message, "text": reply, "completion": "completed"},
        {"type": "assistant_finished", **message, "completion": "completed"},
        {
            "type": "run_finished",
            "turn_id": "t-example",
            "status": "completed",
            "recording": "no_change",
        },
    ],
)

"""v1.3 线索注入的真模型样例（``clue_model_probe.py`` 使用）。

每个样例是一本迷你作品：结构文件（铁律 + 计划线索）、若干已完成的章节正文、
章级线索登记，以及一个待起草的目标章。样例只声明「作品长什么样」与「期望看到
什么」，装配、调用与评分由探针脚本负责——样例本身不碰文件和模型。

设计取舍：

- 正文刻意写短（每章 4—6 段）。真模型按 token 计费，样例的价值在触发哪条注入
  路径，不在文学性；短正文让每次调用的输入稳定在两千 token 量级。
- 计划线索、章级登记、锚三者都按产品真实写入路径（``save_chapter_prose`` 的
  ``clue_entries``）落盘，不手写 JSON——探针顺带验证事务层。
- ``rewrite`` 用来制造「锚失联」：先带锚提交，再把那段正文删掉重存，登记留在
  原地，与作者改稿后的真实情形一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 全书通用铁律；每个样例共用，避免把「铁律遵守」变成变量。
_IRON = (
    "## 铁律\n"
    "- 不写人物心理分析；只写可观察的动作、对白与物件。\n"
    "- 林远在第 5 章之前不得知道真相。\n"
)


def _structure(*plans: str) -> str:
    """拼一份 STRUCTURE.md；没有计划线索时不写该节（用于无线索样例）。"""
    body = f"# 雨夜旅馆\n\n{_IRON}"
    if plans:
        rows = "\n".join(f"- {plan}" for plan in plans)
        body += f"\n## 计划线索\n{rows}\n"
    return body


# ---------------------------------------------------------------------------
# 共用正文
# ---------------------------------------------------------------------------

_CH1 = """# 第一章 雨夜

晚上九点，雨敲在旅馆的玻璃门上。林远收起伞，把湿漉漉的手套塞进大衣口袋。

柜台后站着一个瘦高的老人，正在擦一盏煤油灯。他抬头看了林远一眼，没有笑。

「住店？」老人问。

「住店。」林远把身份证放在柜台上，「要一间靠里的。」

老人从抽屉里摸出一把钥匙，铜牌上刻着「七」。钥匙串上还挂着一只小皮箱的搭扣，箱扣上的漆掉了一块，露出底下的黄铜。

林远接过钥匙时，注意到老人的右手在发抖。
"""

_CH2 = """# 第二章 走廊

走廊尽头的灯忽明忽暗。林远经过三号房时，听见里面有水声。

他低头，看见门缝下积着一小片水。水边放着一双湿手套，指尖朝外，像是有人刚脱下来。

林远敲了敲门。水声停了。

没有人应。他把耳朵贴在门上，只听见自己的呼吸。

回到房间，他把钥匙放在桌上。铜牌上的「七」字被磨得发亮，钥匙齿缝里卡着一小片暗红色的漆皮。
"""

#: 第二章的另一次保存：抽掉「钥匙齿缝卡着漆皮」那段，用来制造锚失联。
_CH2_REWRITTEN = """# 第二章 走廊

走廊尽头的灯忽明忽暗。林远经过三号房时，听见里面有水声。

他低头，看见门缝下积着一小片水。水边放着一双湿手套，指尖朝外，像是有人刚脱下来。

林远敲了敲门。水声停了。

没有人应。他把耳朵贴在门上，只听见自己的呼吸。
"""

_CH3 = """# 第三章 账本

第二天清晨，雨停了。林远在餐厅找到老人，把那只小皮箱放在桌上。

「这箱子是你的？」他问。

老人盯着箱扣看了很久。缺漆的地方露出黄铜，像一道没有愈合的伤口。

「是我父亲的。」老人终于开口，「四十年了，我一直以为它跟着船沉了。」

他伸手去摸箱扣，指尖停在缺口上，没有再往前。
"""

_CH4 = """# 第四章 空房

三号房的门开着。床铺得很平，枕头下露出一角白色的布。

林远掀开枕头，是一只没有配对的男式手套。布料已经半干，掌心有一道裂口。

窗外，旅馆的锅炉房冒出一点白汽。
"""


# ---------------------------------------------------------------------------
# 样例数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedClue:
    """预先登记的一条线索；``anchor_text`` 是本章正文里的一段（含即可）。"""

    id: str
    kind: str
    status: str
    note: str
    anchor_text: str | None = None
    resolves: tuple[str, int] | None = None
    origin: str = "planned"


@dataclass(frozen=True)
class SeedChapter:
    title: str
    text: str = ""
    clues: tuple[SeedClue, ...] = ()
    #: 写完后再存一次这段文本（不重登记线索），用于制造锚失联。
    rewrite: str | None = None


@dataclass(frozen=True)
class Sample:
    key: str
    title: str
    note: str
    structure: str
    chapters: tuple[SeedChapter, ...]
    target_index: int
    #: 提示词里必须出现的片段（确定性注入是否到位）。
    expect_prompt: tuple[str, ...] = ()
    #: 提示词里不得出现的片段（已收线索不应占上下文等）。
    forbid_prompt: tuple[str, ...] = ()
    #: 正文里每组至少命中一个词（观察项：模型是否把线索写进正文）。
    expect_prose: tuple[tuple[str, ...], ...] = ()
    #: 自查第 4 层里应出现的片段（观察项）。
    expect_self_check: tuple[str, ...] = ()
    #: 自查第 4 层应提到的线索编号（观察项）。
    expect_clue_mentions: tuple[str, ...] = ()
    #: 作者补充指令（随请求下发）：用来稳定触发某个写作动作。
    author_note: str = ""
    #: 「### 线索登记」小节至少要解析出的条数（v1.3 P5，硬检查）。
    expect_proposals: int = 0
    #: 至少一条建议的短引能定位到正文段落（硬检查）。
    expect_proposals_anchored: bool = False
    extra: dict[str, str] = field(default_factory=dict)


SAMPLES: tuple[Sample, ...] = (
    Sample(
        key="plant_ch1",
        title="第 1 章埋设计划线索",
        note="计划线索 CL-01 要求本章埋设；检查「本章计划」注入与模型是否把箱扣写进正文。",
        structure=_structure("CL-01 箱扣缺漆：第1章埋、第3章收", "CL-02 湿手套：第2章埋、第4章收"),
        chapters=(SeedChapter(title="雨夜"),),
        target_index=0,
        expect_prompt=("### 线索清单", "【本章计划】", "CL-01", "计划本章埋设"),
        forbid_prompt=("【未回收线索】",),
        expect_prose=(("箱扣", "搭扣", "铜扣"), ("缺漆", "掉漆", "脱漆", "缺口")),
        expect_clue_mentions=("CL-01",),
    ),
    Sample(
        key="resolve_ch3",
        title="第 3 章回收已埋线索",
        note="CL-01 在第 1 章埋、第 3 章收；检查「未回收 + 本章收」合并成一行的注入，"
        "以及模型是否写出回收动作。",
        structure=_structure("CL-01 箱扣缺漆：第1章埋、第3章收", "CL-02 湿手套：第2章埋、第4章收"),
        chapters=(
            SeedChapter(
                title="雨夜",
                text=_CH1,
                clues=(SeedClue("CL-01", "伏笔", "planted", "箱扣缺漆", "箱扣上的漆掉了一块"),),
            ),
            SeedChapter(
                title="走廊",
                text=_CH2,
                clues=(
                    SeedClue("CL-02", "物件", "planted", "门缝边的湿手套", "水边放着一双湿手套"),
                ),
            ),
            SeedChapter(title="账本"),
        ),
        target_index=2,
        expect_prompt=("【未回收线索】", "CL-01", "计划本章收", "第 1 章埋"),
        forbid_prompt=("【失联待重锚】",),
        expect_prose=(("箱扣", "搭扣", "铜扣"),),
        expect_clue_mentions=("CL-01",),
    ),
    Sample(
        key="carry_ch4",
        title="第 4 章带两条未回收线索推进",
        note="已收的 CL-01 不应再占上下文；CL-02 本章计划回收，CL-03 仍未收，检查清单取舍。",
        structure=_structure(
            "CL-01 箱扣缺漆：第1章埋、第3章收",
            "CL-02 湿手套：第2章埋、第4章收",
            "CL-03 旧钥匙：第2章埋、第5章收",
        ),
        chapters=(
            SeedChapter(
                title="雨夜",
                text=_CH1,
                clues=(SeedClue("CL-01", "伏笔", "planted", "箱扣缺漆", "箱扣上的漆掉了一块"),),
            ),
            SeedChapter(
                title="走廊",
                text=_CH2,
                clues=(
                    SeedClue("CL-02", "物件", "planted", "门缝边的湿手套", "水边放着一双湿手套"),
                    SeedClue("CL-03", "物件", "planted", "钥匙齿缝的漆皮", "钥匙齿缝里卡着一小片"),
                ),
            ),
            SeedChapter(
                title="账本",
                text=_CH3,
                clues=(
                    SeedClue(
                        "CL-01",
                        "伏笔",
                        "resolved",
                        "箱扣缺漆",
                        "老人盯着箱扣看了很久",
                        resolves=("CL-01", 1),
                    ),
                ),
            ),
            SeedChapter(title="空房"),
        ),
        target_index=3,
        expect_prompt=("【未回收线索】", "CL-02", "CL-03", "计划本章收"),
        forbid_prompt=("CL-01（",),
        expect_prose=(("手套",),),
        expect_clue_mentions=("CL-02",),
    ),
    Sample(
        key="orphan_ch5",
        title="第 5 章面对失联锚与哑火",
        note="改稿删掉 CL-03 的锚段 + CL-07 计划埋在第 3 章却从未登记；"
        "检查「锚失联」与「哑火」两类发现项是否进提示词。",
        structure=_structure(
            "CL-01 箱扣缺漆：第1章埋、第3章收",
            "CL-03 旧钥匙：第2章埋、第5章收",
            "CL-07 火柴盒：第3章埋、第5章收",
        ),
        chapters=(
            SeedChapter(
                title="雨夜",
                text=_CH1,
                clues=(SeedClue("CL-01", "伏笔", "planted", "箱扣缺漆", "箱扣上的漆掉了一块"),),
            ),
            SeedChapter(
                title="走廊",
                text=_CH2,
                clues=(
                    SeedClue("CL-03", "物件", "planted", "钥匙齿缝的漆皮", "钥匙齿缝里卡着一小片"),
                ),
                rewrite=_CH2_REWRITTEN,
            ),
            SeedChapter(
                title="账本",
                text=_CH3,
                clues=(
                    SeedClue(
                        "CL-01",
                        "伏笔",
                        "resolved",
                        "箱扣缺漆",
                        "老人盯着箱扣看了很久",
                        resolves=("CL-01", 1),
                    ),
                ),
            ),
            SeedChapter(title="空房", text=_CH4),
            SeedChapter(title="锅炉房"),
        ),
        target_index=4,
        expect_prompt=(
            "CLUE_ORPHANED_ANCHOR",
            "锚失联，先重锚再用",
            "CLUE_NOT_PLANTED",
            "CL-03",
            "CL-07",
        ),
        forbid_prompt=("CL-01（",),
        expect_self_check=("CL-03",),
        expect_clue_mentions=("CL-03",),
    ),
    Sample(
        key="no_clue_ch1",
        title="无线索作品不注入线索段",
        note="结构里没有计划线索、目录里没有登记；检查提示词不出现线索清单，自查如实写「本周无线索」。",
        structure=_structure(),
        chapters=(SeedChapter(title="雨夜"),),
        target_index=0,
        expect_prompt=(),
        forbid_prompt=("### 线索清单", "【未回收线索】", "【本章计划】", "### 线索对账"),
        expect_self_check=("无线索",),
    ),
    Sample(
        key="dormant_ch4",
        title="第 4 章回收一条从未埋设的线索",
        note="CL-07 计划第 3 章埋、第 4 章收，但第 3 章没有任何登记；检查「哑火」是否提示模型。",
        structure=_structure("CL-07 火柴盒：第3章埋、第4章收"),
        chapters=(
            SeedChapter(title="雨夜", text=_CH1),
            SeedChapter(title="走廊", text=_CH2),
            SeedChapter(title="账本", text=_CH3),
            SeedChapter(title="空房"),
        ),
        target_index=3,
        expect_prompt=("CLUE_NOT_PLANTED", "CL-07", "计划本章回收"),
        expect_self_check=("CL-07",),
        expect_clue_mentions=("CL-07",),
    ),
    Sample(
        key="emergent_ch2",
        title="第 2 章新埋计划外线索并按格式登记",
        note="结构里没有计划线索；作者补充要求新写一只停摆的怀表。检查模型是否在"
        "「### 线索登记」里按 类型｜说明｜「短引」 提交、短引能否定位到正文段落。",
        structure=_structure(),
        chapters=(SeedChapter(title="雨夜", text=_CH1), SeedChapter(title="走廊")),
        target_index=1,
        author_note=(
            "本章让林远在抽屉底层发现一只停摆的怀表，表盖内侧刻着「三点十分」。"
            "这只表是全新的细节，之前的章节没有出现过。"
        ),
        expect_proposals=1,
        expect_proposals_anchored=True,
        expect_prose=(("怀表", "表"),),
    ),
)


def sample_by_key(key: str) -> Sample:
    for sample in SAMPLES:
        if sample.key == key:
            return sample
    raise KeyError(key)

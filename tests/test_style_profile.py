"""文风档案纯函数测试（P7 T6，计划 D6）：分句/六桶/对话占比/分段/聚合。"""

import pytest

from hastings.rag.style_profile import (
    build_style_profile,
    count_non_whitespace,
    dialogue_ratio,
    sentence_length_bucket,
    split_paragraphs,
    split_sentences,
)


class TestSplitSentences:
    def test_按终止符分句且去终止符(self) -> None:
        assert split_sentences("他推开门。外面下着雨。") == ["他推开门", "外面下着雨"]

    def test_连续标点合并为一个切点(self) -> None:
        assert split_sentences("雨！？还在下……他没动。") == ["雨", "还在下", "他没动"]

    def test_无终止符的尾巴算一句(self) -> None:
        assert split_sentences("他没说完") == ["他没说完"]

    def test_纯标点不产空句(self) -> None:
        assert split_sentences("。。。！！！") == []


class TestSentenceLengthBucket:
    @pytest.mark.parametrize(
        ("length", "bucket"),
        [(1, "1-10"), (10, "1-10"), (11, "11-20"), (30, "21-30"), (51, "51+"), (80, "51+")],
    )
    def test_六桶边界(self, length: int, bucket: str) -> None:
        assert sentence_length_bucket(length) == bucket


class TestDialogueRatio:
    def test_直角引号占比_引号计入分母(self) -> None:
        # 引号内「你好」= 2；总非空白（含引号与标点）= 6
        assert dialogue_ratio("「你好」世界") == pytest.approx(2 / 6)

    def test_弯引号与双直角同口径(self) -> None:
        # “好”…：引号内 1 字；总非空白 4（两引号 + 好 + 省略号）
        assert dialogue_ratio("\u201c好\u201d…") == pytest.approx(1 / 4)
        assert dialogue_ratio("『嗯』。") == pytest.approx(1 / 4)  # 『嗯』。= 4 个非空白字符

    def test_引号外与空白不计入分子(self) -> None:
        assert dialogue_ratio("「a b」xyz") == pytest.approx(2 / 7)

    def test_空文本为合法零(self) -> None:
        assert dialogue_ratio("") == 0.0


class TestSplitParagraphs:
    def test_空行分段且连续空白行合并(self) -> None:
        assert split_paragraphs("第一段。\n\n\n第二段。") == ["第一段。", "第二段。"]

    def test_结尾无空行的尾巴成段(self) -> None:
        assert split_paragraphs("甲。\n\n乙。") == ["甲。", "乙。"]


class TestCountNonWhitespace:
    def test_去空白口径(self) -> None:
        assert count_non_whitespace("他 说\n\t雨。") == 4


class TestBuildStyleProfile:
    def test_聚合统计逐字段钉住(self) -> None:
        profile = build_style_profile({"a.txt": "推开门。雨不停。\n\n他回头问她是谁。"})
        # 句：推开门(3) / 雨不停(3) / 他回头问她是谁(7)
        assert profile.sentence_count == 3
        assert profile.avg_sentence_length == pytest.approx(13 / 3)
        assert profile.sentence_buckets["1-10"] == 3
        assert profile.sentence_buckets["51+"] == 0
        # 总字数去空白口径（标点计入，与 stats 同款）= 3+1+3+1+7+1 = 16
        assert profile.paragraph_count == 2
        assert profile.total_chars == 16
        assert profile.avg_paragraph_length == pytest.approx(8.0)
        assert profile.dialogue_ratio == 0.0

    def test_空语料全零合法(self) -> None:
        profile = build_style_profile({})
        assert profile.total_chars == 0
        assert profile.sentence_count == 0
        assert profile.avg_sentence_length == 0.0
        assert profile.dialogue_ratio == 0.0
        assert profile.warnings == []

"""1.2 M1：结构文档的定位、铁律段切分、指纹与预算。

铁律段是装配契约的唯一结构约定：标记必须在文件首个非空行，丢失即降级全文
注入。切分与预算全部纯函数，离线测试。
"""

from __future__ import annotations

from hastings.workspace.files import digest_bytes
from hastings.workspace.structure_document import (
    BUDGET_LIMIT,
    check_budget,
    split_iron_rules,
    structure_digest,
    structure_path,
)

SAMPLE = """## 铁律
1. 回收之前读者的主判断必须站错边。
2. 每条伏笔要么按计划回收，要么显式声明不收。
3. 真解用到的物件必须早于揭示章出现。

## 路线
陈守拙回县城开面馆，旧案随一只旧手提箱找上门。

## 章节骨架
1. 开门
2. 认物
"""


def test_铁律段切分取到三条规则与余下正文() -> None:
    result = split_iron_rules(SAMPLE)

    assert result.ok is True
    assert result.rules == (
        "1. 回收之前读者的主判断必须站错边。",
        "2. 每条伏笔要么按计划回收，要么显式声明不收。",
        "3. 真解用到的物件必须早于揭示章出现。",
    )
    assert result.remainder.startswith("## 路线")
    assert "## 章节骨架" in result.remainder
    assert "铁律" not in result.remainder


def test_铁律后直接到文件尾也算完整段() -> None:
    result = split_iron_rules("## 铁律\n不写血腥。\n只呈现物件。")

    assert result.ok is True
    assert result.rules == ("不写血腥。", "只呈现物件。")
    assert result.remainder == ""


def test_标记丢失降级全文注入不猜() -> None:
    text = "# 我的作品\n\n## 铁律\n不写血腥。\n"

    result = split_iron_rules(text)

    assert result.ok is False
    assert result.rules == ()
    assert result.remainder == text


def test_铁律不在开头按丢失处理() -> None:
    text = "## 路线\n先有路线。\n\n## 铁律\n不写血腥。\n"

    result = split_iron_rules(text)

    assert result.ok is False
    assert result.remainder == text


def test_空文件与全空行按丢失处理() -> None:
    assert split_iron_rules("").ok is False
    assert split_iron_rules("\n  \n").ok is False


def test_指纹与正文指纹同一算法且区分内容() -> None:
    assert structure_digest(SAMPLE) == digest_bytes(SAMPLE.encode("utf-8"))
    assert structure_digest(SAMPLE) != structure_digest(SAMPLE + "\n附注。")


def test_预算未超与超限() -> None:
    within = check_budget("字" * (BUDGET_LIMIT - 1))
    over = check_budget("字" * (BUDGET_LIMIT + 1))

    assert within.ok is True
    assert within.length == BUDGET_LIMIT - 1
    assert over.ok is False
    assert over.limit == BUDGET_LIMIT


def test_结构文档位于作品根目录(tmp_path) -> None:
    assert structure_path(tmp_path).name == "STRUCTURE.md"
    assert structure_path(tmp_path).parent == tmp_path

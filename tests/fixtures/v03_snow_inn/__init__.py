"""雪山旅馆连贯夹具（计划 §9.4 / 执行指导 §13.2）。

同一部「雪山旅馆」贯穿 0.3 的十场景验收，另建一部隔离作品。夹具内容：

- 三章正文（雪夜来客 / 空房 / 九点的钟声）+ 一次历史提交（旧正文来源）；
- 账本：侦探林昭、雪姐、雪妹、旅馆；姐妹关系；真实事件（九点·储藏间）；
  真相事实与误导事实各一条；三条关键线索；雪妹的证词（knowledge）；
  一条「后文揭示」事实（第 5 章才披露，用于披露检查）；
- 隔离作品「雾港」（一章 + 一个人物），用于换作品场景。

构建只经 HTTP（与作者操作同一路径），不使用 FakeProvider 之外的测试开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

CHAPTERS: dict[str, str] = {
    "第一章 雪夜来客": (
        "暴雪封山前，林昭赶到了雪山旅馆。她受雇来查十年前的一笔账，"
        "没想到前台只坐着一个人：雪姐把登记簿推过来，说妹妹病了，二楼只剩一间空房。\n\n"
        "大厅的落地钟停在九点。雪姐说它坏了很久，修不好。"
    ),
    "第二章 空房": (
        "二楼走廊尽头是一扇没有门牌的门。林昭问起，雪妹从楼梯口探出头，"
        "说那间房漏风，不适合住客。她的靴底有雪水，化了又冻，留下淡白的盐渍。\n\n"
        "夜里十一点，林昭听见楼下有钟声，很短，像被谁按住了。"
    ),
    "第三章 九点的钟声": (
        "清晨，储藏间的门自己开了。雪姐倒在货架之间，手边是那只停摆的落地钟的摆锤。\n\n"
        "雪妹站在门口，说她九点后就睡下了，什么也没听见。林昭注意到她袖口的雪，"
        "比昨夜更厚。"
    ),
}

#: 后文才揭示的真相（第 5 章）：不写进正文，只在账本里，供披露检查使用。
LATE_REVEAL = "雪妹在九点半后进入过储藏间，并动过落地钟的摆锤。"


@dataclass(frozen=True)
class SnowInnFixture:
    """夹具句柄：项目、章、账本对象与关键提交。"""

    project_id: str
    chapters: dict[str, str] = field(default_factory=dict)
    ledger: dict[str, str] = field(default_factory=dict)
    commits: dict[str, str] = field(default_factory=dict)

    def chapter(self, title: str) -> str:
        return self.chapters[title]

    def entry(self, label: str) -> str:
        return self.ledger[label]


def _ledger_entry(
    client: TestClient, project: str, kind: str, fields: dict[str, object], key: str
) -> str:
    response = client.post(
        f"/api/v2/projects/{project}/ledger/entries",
        json={"kind": kind, "fields": fields, "hypothesis": False, "request_id": key},
    )
    assert response.status_code in (200, 201), response.text
    return str(response.json()["entry"]["id"])


def build_snow_inn(client: TestClient, *, name: str = "雪山旅馆") -> SnowInnFixture:
    """按计划 §9.4 前提构建夹具（三章 + 账本 + 历史提交）。"""
    project = client.post("/api/v2/projects", json={"name": name}).json()["project_id"]
    chapters: dict[str, str] = {}
    for order, (title, text) in enumerate(CHAPTERS.items(), start=1):
        imported = client.post(
            f"/api/v2/projects/{project}/chapters/import",
            json={
                "idempotency_key": f"snow-ch{order}",
                "title": title,
                "text": text,
                "order_key": f"{order:04d}",
            },
        )
        assert imported.status_code == 200, imported.text
        chapters[title] = str(imported.json()["chapter_id"])

    ledger: dict[str, str] = {}
    ledger["林昭"] = _ledger_entry(
        client,
        project,
        "story.character",
        {"name": "林昭", "identity": "受雇查账的侦探", "role": "protagonist"},
        "snow-char-1",
    )
    ledger["雪姐"] = _ledger_entry(
        client,
        project,
        "story.character",
        {"name": "雪姐", "identity": "旅馆长女", "role": "support"},
        "snow-char-2",
    )
    ledger["雪妹"] = _ledger_entry(
        client,
        project,
        "story.character",
        {"name": "雪妹", "identity": "旅馆次女", "role": "antagonist"},
        "snow-char-3",
    )
    ledger["旅馆"] = _ledger_entry(
        client,
        project,
        "story.place",
        {"name": "雪山旅馆", "description": "封山季节只有姐妹两人打理"},
        "snow-place",
    )
    ledger["姐妹"] = _ledger_entry(
        client,
        project,
        "story.relationship",
        {
            "source_id": ledger["雪姐"],
            "target_id": ledger["雪妹"],
            "relation_type": "姐妹",
            "public_state": "相依为命的姐妹",
            "true_state": "姐姐在查妹妹的账",
        },
        "snow-rel",
    )
    ledger["九点事件"] = _ledger_entry(
        client,
        project,
        "story.timeline_event",
        {
            "time": "当夜九点",
            "place_id": ledger["旅馆"],
            "participants": [ledger["雪姐"]],
            "before_state": "雪姐在大厅看账",
            "after_state": "雪姐死在储藏间",
        },
        "snow-event",
    )
    ledger["真相"] = _ledger_entry(
        client,
        project,
        "story.truth_fact",
        {"statement": "雪姐在当夜九点死在储藏间，落地钟被摆锤击停", "event_id": ledger["九点事件"]},
        "snow-fact-truth",
    )
    ledger["误导"] = _ledger_entry(
        client,
        project,
        "story.truth_fact",
        {"statement": "九点前有一辆雪橇离开旅馆（误）", "event_id": ledger["九点事件"]},
        "snow-fact-decoy",
    )
    ledger["钟停九点"] = _ledger_entry(
        client,
        project,
        "story.clue",
        {
            "statement": "储藏间的落地钟停在九点",
            "source": "储藏间现场",
            "points_to": [ledger["真相"]],
            "necessity": True,
        },
        "snow-clue-1",
    )
    ledger["雪水靴印"] = _ledger_entry(
        client,
        project,
        "story.clue",
        {
            "statement": "雪妹靴底有反复冻融的雪水",
            "source": "二楼走廊",
            "points_to": [ledger["误导"]],
            "necessity": False,
        },
        "snow-clue-2",
    )
    ledger["门牌被摘"] = _ledger_entry(
        client,
        project,
        "story.clue",
        {
            "statement": "二楼尽头空房的门牌被人摘掉",
            "source": "二楼尽头",
            "points_to": [ledger["真相"]],
            "necessity": True,
        },
        "snow-clue-3",
    )
    ledger["雪妹证词"] = _ledger_entry(
        client,
        project,
        "story.knowledge",
        {
            "character_id": ledger["雪妹"],
            "fact_id": ledger["真相"],
            "mode": "suspects",
            "note": "雪妹作证：九点后就睡下了，什么也没听见",
        },
        "snow-knowledge",
    )
    ledger["后文真相"] = _ledger_entry(
        client,
        project,
        "story.truth_fact",
        {"statement": LATE_REVEAL, "event_id": ledger["九点事件"]},
        "snow-fact-late",
    )

    head = client.get(f"/api/v2/projects/{project}/manuscript").json()["commit_id"]
    return SnowInnFixture(
        project_id=project,
        chapters=chapters,
        ledger=ledger,
        commits={"seed": str(head)},
    )


def build_isolation_project(client: TestClient, *, name: str = "雾港") -> str:
    """第二部作品：验证换作品时状态/归属隔离。"""
    project = client.post("/api/v2/projects", json={"name": name}).json()["project_id"]
    client.post(
        f"/api/v2/projects/{project}/chapters/import",
        json={
            "idempotency_key": "fog-ch1",
            "title": "第一章 雾港",
            "text": "雾散之前，港口没有船进港。",
        },
    )
    _ledger_entry(
        client, project, "story.character", {"name": "周雾", "identity": "港务员"}, "fog-char"
    )
    return str(project)

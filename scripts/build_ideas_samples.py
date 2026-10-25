"""建立灵感专项的合成样本作品（0.8 I0）。

样本只用于开发与验收：全部内容由脚本生成，不含作者真实作品。默认写到
``data/ideas-samples/``（已在 .gitignore 内），可以反复重建：

    uv run python scripts/build_ideas_samples.py
    uv run python scripts/build_ideas_samples.py --root D:/tmp/ideas-samples

每个样本都覆盖验收矩阵里的一类边界：旧标题与标签、无标题、长文、Unicode、
全空白、已删除、多修订历史、损坏文件、两个作品分别保存草稿。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from hastings.workspace.files import Workspace
from hastings.workspace.ideas import IDEAS_DIR, IdeaStore

MANUSCRIPT = """第一章 雨夜

雨下到后半夜才停。她把伞收起来，靠在门边，听见楼上有脚步在走。

第二章 空房间

门锁没有被撬的痕迹，桌上的茶还是温的。
"""

LONG_BODY = "她记得那天所有的细节。" + "雨声一直没有停。" * 900
UNICODE_BODY = (
    "第三章的伏笔：门牌号 404 与 4 月 4 日重复出现——是不是巧合？🎭\n\t缩进与换行也要原样保留。"
)
LEGACY_TITLE = "旧标题：灯塔"
LEGACY_TAGS = ["伏笔", "第二幕"]
CORRUPT_BODY = '{"schema_version": 1, "idea_id": "idea_broken"'


def _workspace(root: Path, name: str) -> Workspace:
    directory = root / name
    (directory / "正文").mkdir(parents=True, exist_ok=True)
    (directory / "正文" / "0001-第一章-雨夜.md").write_text(MANUSCRIPT, encoding="utf-8")
    (directory / "正文" / "0002-第二章-空房间.md").write_text(
        "第二章 空房间\n\n她推开门，房间里只有一张桌子。\n", encoding="utf-8"
    )
    return Workspace(directory)


def _build(root: Path) -> dict[str, object]:
    if root.exists():
        shutil.rmtree(root)
    workspace = _workspace(root, "sample-a")
    other = _workspace(root, "sample-b")
    store = IdeaStore(workspace)
    summary: dict[str, object] = {"root": str(root), "samples": []}

    def note(kind: str, view: dict[str, object], extra: str = "") -> None:
        samples = summary["samples"]
        assert isinstance(samples, list)
        samples.append(
            {
                "kind": kind,
                "idea_id": view["idea_id"],
                "revision_id": view["revision_id"],
                "chars": len(str(view["body"])),
                "note": extra,
            }
        )

    note("plain", store.create(body="她第三次回到那间空房间。", request_key="sample-plain-0001"))
    note(
        "legacy-title-tags",
        store.create(
            title=LEGACY_TITLE,
            body="灯塔在退潮时才会露出第二层台阶。",
            tags=LEGACY_TAGS,
            request_key="sample-legacy-0001",
        ),
        "旧客户端写入的标题与标签，新界面不再生成",
    )
    note(
        "untitled",
        store.create(
            body="没有人解释过那把钥匙为什么在花盆里。", request_key="sample-untitled-0001"
        ),
    )
    note("long", store.create(body=LONG_BODY, request_key="sample-long-0001"), "超过摘要长度")
    note(
        "unicode",
        store.create(body=UNICODE_BODY, request_key="sample-unicode-0001"),
        "表情、制表符与中文标点",
    )
    # 全空白正文：当前服务已拒绝写入，样本按旧文件形态构造，用来验证列表与编辑器的诊断。
    blank = store.create(body="占位正文", request_key="sample-blank-0001")
    blank_path = workspace.root / IDEAS_DIR / f"{blank['idea_id']}.json"
    blank_document = json.loads(blank_path.read_text(encoding="utf-8"))
    blank_document["revisions"][-1]["body"] = "   \u3000 \n"
    blank_path.write_text(
        json.dumps(blank_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    note("whitespace-only", store.get(str(blank["idea_id"])), "旧文件形态的全空白正文")

    # 多修订历史：修改两次再恢复一次，列表仍按创建时间排。
    edited = store.create(body="第一稿：她在码头等了整夜。", request_key="sample-history-0001")
    first = store.get(str(edited["idea_id"]))
    second = store.update(
        str(edited["idea_id"]),
        expected_digest=str(first["digest"]),
        expected_revision=str(first["revision_id"]),
        body="第二稿：她在码头等到天亮，船没有来。",
        request_key="sample-history-0002",
    )
    third = store.update(
        str(edited["idea_id"]),
        expected_digest=str(second["digest"]),
        expected_revision=str(second["revision_id"]),
        body="第三稿：船来了，下来的不是她等的人。",
        request_key="sample-history-0003",
    )
    store.restore(
        str(edited["idea_id"]),
        target_revision=str(second["revision_id"]),
        expected_digest=str(third["digest"]),
        request_key="sample-history-0004",
    )
    note("history", store.get(str(edited["idea_id"])), "四条修订：新建、两次修改、一次恢复")

    trashed = store.create(
        body="这条会被删除，用于最近删除与撤销。", request_key="sample-deleted-0001"
    )
    head = store.get(str(trashed["idea_id"]))
    store.trash(
        str(trashed["idea_id"]),
        expected_digest=str(head["digest"]),
        expected_revision=str(head["revision_id"]),
        request_key="sample-deleted-0002",
    )
    note("deleted", store.get(str(trashed["idea_id"])), "已删除，可在最近删除里恢复")

    # 损坏条目：直接写一个无法解析的文件，验证列表不隐藏其他记录。
    corrupt = workspace.root / IDEAS_DIR / "idea_00000000000000ff.json"
    corrupt.write_text(CORRUPT_BODY, encoding="utf-8")

    # 第二个作品：草稿与选择按作品隔离。
    IdeaStore(other).create(
        body="另一个作品的灵感，不应出现在第一个作品里。", request_key="sample-b-0001"
    )

    summary["corrupt"] = str(corrupt)
    summary["listing"] = IdeaStore(workspace).list(limit=50)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="生成灵感专项合成样本作品")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "ideas-samples",
        help="样本根目录（默认 data/ideas-samples）",
    )
    args = parser.parse_args()
    summary = _build(args.root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

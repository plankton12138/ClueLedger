"""给浏览器提供真实账本与重复、Markdown、未来回收样本；不调用模型。"""

import sys
from pathlib import Path

from hastings.workspace.clues import ClueEntry, ClueStore, publish_clues
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace

workspace = Workspace.open(Path(sys.argv[1]))
store = ClueStore(Path(sys.argv[2]))
first, second = "正文/0001 雨夜.md", "正文/0002 照片.md"
old = workspace.read(first)
workspace.write(
    first,
    "# 雨夜\n\n🔑同一句线索。\n\n" + "前文是走廊的描述。\n\n" * 65
    + "第二处𠮷。**同一句线索。**\n\n实体 &amp; 线索。\n\n记下 `代码线索`。\n",
    expected_digest=old.digest,
)
workspace.write(second, "# 照片\n\n公开照片解释了背面内容。\n", expected_digest=None)
source = workspace.read(first)
later = workspace.read(second)


def proof(view, quote, last=False):
    start = view.text.rindex(quote) if last else view.text.index(quote)
    return ObservedEvidence(view.path, view.digest, start, start + len(quote), quote)


publish_clues(workspace, store, (
    ClueEntry("遗书背面内容", 1, 2, "resolved", "", proof(source, "同一句线索。", True),
              2, proof(later, "公开照片解释了背面内容。")),
    ClueEntry("实体来源", 1, None, "open", "", proof(source, "&amp;")),
    ClueEntry("代码来源", 1, None, "open", "", proof(source, "代码线索")),
), source_chapter=1)

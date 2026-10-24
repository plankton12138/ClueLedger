"""全局模块库（0.8 B）。

作者内容按类型分实例保存，每份内容以不可变版本发布；作品只固定引用版本，不复制原文。
入口：``ModuleLibrary`` 提供服务，``presets`` 定义七种类型，``records`` 负责条目格式。
"""

from hastings.library.catalog import CatalogEntry, CatalogRevision, LibraryCatalog
from hastings.library.modules import ModuleInfo, ModuleLibrary, RevisionInfo
from hastings.library.presets import PRESETS, ModuleSpec, is_kind, presets_view, spec
from hastings.library.records import EntryDraft, entry_markdown, parse_entry
from hastings.library.storage import DEFAULT_LIBRARY_DIR, LibraryStorage

__all__ = [
    "DEFAULT_LIBRARY_DIR",
    "PRESETS",
    "CatalogEntry",
    "CatalogRevision",
    "EntryDraft",
    "LibraryCatalog",
    "LibraryStorage",
    "ModuleInfo",
    "ModuleLibrary",
    "ModuleSpec",
    "RevisionInfo",
    "entry_markdown",
    "is_kind",
    "parse_entry",
    "presets_view",
    "spec",
]

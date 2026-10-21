"""作品目录的文件规则（0.5）。

作品就是一个本地目录：``正文/`` 下每章一个 Markdown 文件，``设定集/角色小传/``
下一名角色一个文件，线索与真相为固定文件。这个模块只回答「哪些路径属于作品、当前内容是什么、这次写入是否基于我读到的
那一份」，不解析段落、不保存章节清单、不写数据库。目录本身就是内容依据，因此外部
编辑器的改动无需同步即可被读到。

路径校验分两层：词法上只接受章节、角色和固定设定的相对路径；访问前再把解析后的目录与作品根比对，
拒绝符号链接、目录联接和任何越出根目录的结果。只做词法检查不足以保护 Windows 上的
目录联接，所以两层都保留。

写入用同目录临时文件替换，避免出现半个文件；写入前必须给出上次读取的指纹，
``expected_digest=None`` 只表示「创建尚不存在的文件」，不能当作无条件覆盖。
"""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from hastings.workspace.errors import WorkspaceError

__all__ = [
    "BODY_DIR",
    "CHARACTERS_DIR",
    "FileKind",
    "SETTINGS_DIR",
    "SETTING_FILES",
    "SETTING_PATHS",
    "Catalog",
    "FileEntry",
    "FileView",
    "SearchHit",
    "SearchResult",
    "Workspace",
    "chapter_title",
    "character_title",
    "digest_bytes",
    "is_managed_path",
    "managed_kind",
    "normalize_newlines",
]

BODY_DIR = "正文"
SETTINGS_DIR = "设定集"
CHARACTERS_DIR = f"{SETTINGS_DIR}/角色小传"
FileKind = Literal["chapter", "character", "setting"]

#: 角色按文件列出；其余两份设定路径固定，顺序即界面顺序。
SETTING_FILES: tuple[tuple[str, str], ...] = (
    (f"{SETTINGS_DIR}/线索.md", "线索"),
    (f"{SETTINGS_DIR}/真相.md", "真相"),
)
SETTING_PATHS = frozenset(path for path, _ in SETTING_FILES)

_ILLEGAL_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED_STEMS = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
_ORDER_PREFIX = re.compile(r"^(\d{4})(?!\d)")
_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$")

#: 应用新建章节的文件名长度上限（不含扩展名与序号）。
_MAX_TITLE_CHARS = 60
#: 搜索单行摘录上限，避免把整段长文塞进事件流。
_SNIPPET_CHARS = 200


def managed_kind(path: str) -> FileKind | None:
    """统一分类受管路径；真实目录与链接另由 Workspace 校验。"""
    if not path or "\\" in path:
        return None
    parsed = PurePosixPath(path)
    if parsed.is_absolute():
        return None
    parts = parsed.parts
    # "." / ".." / 空段 / 连续分隔符都会让规范化后的 parts 与原串不一致。
    if len(parts) not in (2, 3) or "/".join(parts) != path:
        return None
    directory, filename = parsed.parent.as_posix(), parsed.name
    if _ILLEGAL_NAME.search(filename) or filename != filename.strip(" ."):
        return None
    stem = PurePosixPath(filename).stem
    if not stem or stem.split(".")[0].casefold() in _RESERVED_STEMS:
        return None
    if path in SETTING_PATHS:
        return "setting"
    if parsed.suffix != ".md":
        return None
    if directory == BODY_DIR:
        return "chapter"
    if directory == CHARACTERS_DIR:
        return "character"
    return None


def is_managed_path(path: str) -> bool:
    """词法判断：是否属于作品受管路径（不代表文件存在或可安全访问）。"""
    return managed_kind(path) is not None


def normalize_newlines(text: str) -> str:
    """统一换行为 LF；应用写入与读取返回的文本都使用 LF。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def digest_bytes(data: bytes) -> str:
    """按实际字节计算指纹：外部编辑器改了换行或 BOM 也算改动。"""
    return hashlib.sha256(data).hexdigest()


def decode_text(data: bytes, *, where: str) -> str:
    """UTF-8（可带 BOM）→ LF 文本；其他编码与二进制内容显式失败。"""
    if b"\x00" in data:
        raise WorkspaceError(
            code="FILE_ENCODING_INVALID",
            where=where,
            why="文件包含 NUL 字节，看起来不是 Markdown 文本",
            fix="用编辑器另存为 UTF-8 纯文本后再放入作品目录",
        )
    try:
        text = (
            data.decode("utf-8-sig") if data.startswith(codecs.BOM_UTF8) else data.decode("utf-8")
        )
    except UnicodeDecodeError as exc:
        raise WorkspaceError(
            code="FILE_ENCODING_INVALID",
            where=where,
            why=f"文件不是 UTF-8（第 {exc.start} 字节起无法解码）",
            fix="用编辑器把文件另存为 UTF-8；本版不做自动转码",
        ) from exc
    return normalize_newlines(text)


def chapter_title(text: str, filename: str) -> str:
    """章节标题：首个一级标题优先，缺失时回退文件名（去掉序号前缀）。"""
    for line in text.split("\n")[:20]:
        match = _H1.match(line)
        if match:
            return match.group(1).strip()
    stem = PurePosixPath(filename).stem
    stripped = _ORDER_PREFIX.sub("", stem).strip(" -_·")
    return stripped or stem


def character_title(text: str, filename: str) -> str:
    """角色名优先取一级标题，缺失时使用完整文件名，不剥离数字。"""
    for line in text.split("\n")[:20]:
        match = _H1.match(line)
        if match:
            return match.group(1).strip()
    return PurePosixPath(filename).stem


@dataclass(frozen=True, slots=True)
class FileView:
    """一次读取或写入的结果：路径、LF 文本、实际字节指纹。"""

    path: str
    text: str
    digest: str
    size: int


@dataclass(frozen=True, slots=True)
class FileEntry:
    """目录条目：存在性与标题可直接用于列表，读取原文再单独请求。"""

    path: str
    kind: FileKind
    title: str
    order: str
    exists: bool
    size: int


@dataclass(frozen=True, slots=True)
class Catalog:
    """章节、角色各按文件名排序，线索与真相缺失时也列出。"""

    chapters: tuple[FileEntry, ...]
    characters: tuple[FileEntry, ...]
    settings: tuple[FileEntry, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    line: int
    text: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    unreadable: tuple[str, ...]
    truncated: bool


class Workspace:
    """一个作品目录。所有方法只作用于受管路径；不做跨文件事务。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    # -- 建立与打开 ---------------------------------------------------------

    @classmethod
    def create(cls, root: Path) -> Workspace:
        """新建（或补齐）正文、角色目录与两份空设定；不覆盖已有内容。"""
        target = Path(root).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise WorkspaceError(
                code="WORKSPACE_NOT_DIRECTORY",
                where="workspace.create",
                why=f"路径已存在且不是目录：{target}",
                fix="换一个目录名，或打开那个已有的文件所在目录",
            ) from exc
        except OSError as exc:
            raise WorkspaceError(
                code="FILE_WRITE_FAILED",
                where="workspace.create",
                why=f"无法创建目录 {target}：{exc.strerror or exc}",
                fix="检查父目录是否存在、是否有写权限",
            ) from exc
        workspace = cls(target)
        workspace.ensure_structure()
        return workspace

    @classmethod
    def open(cls, root: Path) -> Workspace:
        """打开已有目录：只检查可读性，不创建、不改写任何文件。"""
        target = Path(root).expanduser()
        if not target.exists():
            raise WorkspaceError(
                code="WORKSPACE_NOT_FOUND",
                where="workspace.open",
                why=f"目录不存在：{target}",
                fix="确认路径拼写，或先用「新建」创建作品目录",
            )
        if not target.is_dir():
            raise WorkspaceError(
                code="WORKSPACE_NOT_DIRECTORY",
                where="workspace.open",
                why=f"路径不是目录：{target}",
                fix="作品目录必须是一个文件夹，请重新选择",
            )
        return cls(target)

    def ensure_structure(self) -> None:
        """补齐受管目录与缺失的设定文件；已存在的文件保持原样。"""
        for name in (BODY_DIR, SETTINGS_DIR, CHARACTERS_DIR):
            self._writable_directory(name)
        for path, _ in SETTING_FILES:
            if not self._target(path).exists():
                self.write(path, "", expected_digest=None)

    @property
    def name(self) -> str:
        return self.root.name or str(self.root)

    # -- 目录与读取 ---------------------------------------------------------

    def catalog(self) -> Catalog:
        """列出两块内容。设定文件缺失时仍列出，界面据此提示补齐。"""
        return Catalog(
            chapters=self._markdown_entries(BODY_DIR, "chapter"),
            characters=self._markdown_entries(CHARACTERS_DIR, "character"),
            settings=self._settings(),
        )

    def read(self, path: str) -> FileView:
        target = self._target(path)
        data = self._read_bytes(target, path)
        return FileView(
            path=path,
            text=decode_text(data, where=f"files.read:{path}"),
            digest=digest_bytes(data),
            size=len(data),
        )

    def search(self, query: str, *, limit: int = 50) -> SearchResult:
        """受管文本的逐行搜索；无法解码的文件单独报告，不静默跳过。"""
        needle = query.strip()
        if not needle:
            raise WorkspaceError(
                code="SEARCH_QUERY_EMPTY",
                where="files.search",
                why="搜索词为空",
                fix="输入至少一个字符再搜索",
            )
        folded = needle.casefold()
        hits: list[SearchHit] = []
        unreadable: list[str] = []
        truncated = False
        catalog = self.catalog()
        for entry in (*catalog.chapters, *catalog.characters, *catalog.settings):
            if not entry.exists:
                continue
            try:
                view = self.read(entry.path)
            except WorkspaceError:
                unreadable.append(entry.path)
                continue
            for number, line in enumerate(view.text.split("\n"), start=1):
                if folded not in line.casefold():
                    continue
                if len(hits) == limit:
                    # 再命中一条才说明确实还有更多结果；刚好等于上限不算截断。
                    truncated = True
                    break
                hits.append(
                    SearchHit(path=entry.path, line=number, text=line.strip()[:_SNIPPET_CHARS])
                )
            if truncated:
                break
        return SearchResult(tuple(hits), tuple(unreadable), truncated)

    # -- 写入 ---------------------------------------------------------------

    def write(self, path: str, text: str, *, expected_digest: str | None) -> FileView:
        """按指纹前提写入；``expected_digest=None`` 只用于创建新文件。"""
        target = self._target(path)
        self._writable_directory(PurePosixPath(path).parent.as_posix())
        self._check_expected(target, path, expected_digest)
        payload = normalize_newlines(text).encode("utf-8")
        _atomic_replace(target, payload, path)
        return FileView(
            path=path,
            text=normalize_newlines(text),
            digest=digest_bytes(payload),
            size=len(payload),
        )

    def create_chapter(self, title: str, text: str = "") -> FileView:
        """新增一章：序号取当前最大序号加一，文件名与内容都不覆盖已有文件。"""
        clean = _safe_title(title)
        path = self._next_chapter_path(clean)
        body = text if text.strip() else f"# {clean}\n"
        return self.write(path, body, expected_digest=None)

    def move(self, source: str, target: str, *, expected_digest: str) -> FileView:
        """在同类目录内重命名章节或角色；线索与真相路径固定。"""
        self._reject_setting_path(source, "files.move")
        self._reject_setting_path(target, "files.move")
        src = self._target(source)
        dst = self._target(target)
        if managed_kind(source) != managed_kind(target):
            raise WorkspaceError(
                code="FILE_KIND_MISMATCH",
                where="files.move",
                why="不能把章节和角色小传互相移动",
                fix="章节保留在正文目录，角色保留在角色小传目录；改内容类型时明确新建文件",
            )
        self._check_expected(src, source, expected_digest)
        if dst.exists():
            raise WorkspaceError(
                code="FILE_EXISTS_CONFLICT",
                where="files.move",
                why=f"目标文件已存在：{target}",
                fix="换一个文件名，或先删除目标文件",
                details={"path": target},
            )
        self._writable_directory(PurePosixPath(target).parent.as_posix())
        try:
            os.replace(src, dst)
        except OSError as exc:
            raise WorkspaceError(
                code="FILE_WRITE_FAILED",
                where="files.move",
                why=f"无法移动文件：{exc.strerror or exc}",
                fix="确认两个路径在同一磁盘、文件未被其他程序占用",
            ) from exc
        return self.read(target)

    def delete(self, path: str, *, expected_digest: str) -> None:
        """按指纹删除章节或角色文件；线索与真相不能删除。"""
        self._reject_setting_path(path, "files.delete")
        target = self._target(path)
        self._check_expected(target, path, expected_digest)
        try:
            target.unlink()
        except OSError as exc:
            raise WorkspaceError(
                code="FILE_WRITE_FAILED",
                where="files.delete",
                why=f"无法删除文件：{exc.strerror or exc}",
                fix="确认文件未被其他程序占用",
            ) from exc

    # -- 导入 ---------------------------------------------------------------

    def import_text(self, filename: str, data: bytes) -> tuple[FileView, ...]:
        """把上传的正文直接写成章文件：``.md`` 一个文件一章，``.txt`` 按分章规则切分。"""
        name = PurePosixPath(filename.replace("\\", "/")).name or "导入正文.md"
        suffix = PurePosixPath(name).suffix.lower()
        text = decode_text(data, where=f"imports:{name}")
        if not text.strip():
            raise WorkspaceError(
                code="IMPORT_EMPTY",
                where=f"imports:{name}",
                why="文件里没有可导入的正文",
                fix="确认文件不是空文件或只有标题",
            )
        if suffix in (".md", ".markdown"):
            title = chapter_title(text, name) or "导入正文"
            return (self.create_chapter(title, text),)
        if suffix == ".txt":
            # 分章规则仍沿用 0.3 的纯函数：它只做文本切分，不写存储。
            # 局部导入，避免纯文件读写也拉起旧领域包。
            from hastings.interfaces.importers.manuscript_text import parse_manuscript_text

            parsed = parse_manuscript_text(text, filename=name)
            if not parsed.chapters:
                raise WorkspaceError(
                    code="IMPORT_EMPTY",
                    where=f"imports:{name}",
                    why="文件里没有可导入的正文",
                    fix="确认文件不是空文件或只有标题",
                )
            return tuple(
                self.create_chapter(chapter.title, chapter.text) for chapter in parsed.chapters
            )
        raise WorkspaceError(
            code="IMPORT_FORMAT_INVALID",
            where=f"imports:{name}",
            why=f"不支持的文件类型：{suffix or '无扩展名'}",
            fix="只导入 .md 或 .txt 文件",
        )

    # -- 内部：路径 ---------------------------------------------------------

    def _directory(self, name: str) -> Path:
        """受管目录必须是作品根下的真实目录，不能是符号链接或目录联接。"""
        directory = self.root / name
        if directory.is_symlink():
            raise _escaped(name)
        try:
            resolved = directory.resolve(strict=False)
        except OSError as exc:
            raise _escaped(name) from exc
        if resolved != directory:
            raise _escaped(name)
        return directory

    def _writable_directory(self, name: str) -> Path:
        directory = self._directory(name)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(
                code="FILE_WRITE_FAILED",
                where=f"files.mkdir:{name}",
                why=f"无法创建受管目录 {name}：{exc.strerror or exc}",
                fix="检查作品目录权限，并确认同名路径不是文件",
            ) from exc
        return directory

    def _target(self, path: str) -> Path:
        if not is_managed_path(path):
            raise WorkspaceError(
                code="PATH_NOT_MANAGED",
                where="files.path",
                why=f"不是作品受管路径：{path}",
                fix="正文用「正文/文件名.md」，角色用「设定集/角色小传/姓名.md」，其余设定为线索.md、真相.md",
                details={"path": path},
            )
        parsed = PurePosixPath(path)
        target = self._directory(parsed.parent.as_posix()) / parsed.name
        if target.is_symlink():
            raise _escaped(path)
        return target

    def _reject_setting_path(self, path: str, where: str) -> None:
        if path in SETTING_PATHS:
            raise WorkspaceError(
                code="SETTING_PATH_FIXED",
                where=where,
                why=f"设定文件不能移动或删除：{path}",
                fix="线索.md、真相.md 只允许改写；角色小传目录内的角色文件可以改名或删除",
                details={"path": path},
            )

    # -- 内部：内容 ---------------------------------------------------------

    def _read_bytes(self, target: Path, path: str) -> bytes:
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise WorkspaceError(
                code="FILE_NOT_FOUND",
                where=f"files.read:{path}",
                why="文件不存在",
                fix="刷新目录后重新选择；若刚被删除，可重新创建同名文件",
                details={"path": path},
            ) from exc
        except OSError as exc:
            raise WorkspaceError(
                code="FILE_READ_FAILED",
                where=f"files.read:{path}",
                why=f"无法读取文件：{exc.strerror or exc}",
                fix="确认文件未被其他程序独占，且当前用户有读权限",
            ) from exc

    def _check_expected(self, target: Path, path: str, expected_digest: str | None) -> None:
        current = self._current_digest(target, path)
        if expected_digest is None:
            if current is not None:
                raise WorkspaceError(
                    code="FILE_EXISTS_CONFLICT",
                    where=f"files.write:{path}",
                    why="文件已存在，而这次写入声明为新建",
                    fix="先读取该文件，再用读到的指纹改写；需要另一份文件时换一个文件名",
                    details={"path": path},
                )
            return
        if current is None:
            raise WorkspaceError(
                code="FILE_NOT_FOUND",
                where=f"files.write:{path}",
                why="要改写的文件不存在",
                fix="刷新目录后重新读取；文件被删除时重新创建",
                details={"path": path},
            )
        if current != expected_digest:
            raise WorkspaceError(
                code="FILE_STALE_CONFLICT",
                where=f"files.write:{path}",
                why="文件在这次写入之前已被改动（可能是外部编辑器或另一轮任务）",
                fix="重新读取该文件，确认当前内容后再写入；不要用旧内容覆盖",
                details={
                    "path": path,
                    "expected_digest": expected_digest,
                    "current_digest": current,
                },
            )

    def _current_digest(self, target: Path, path: str) -> str | None:
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorkspaceError(
                code="FILE_READ_FAILED",
                where=f"files.write:{path}",
                why=f"无法读取文件现状：{exc.strerror or exc}",
                fix="确认文件未被其他程序独占，且当前用户有读权限",
            ) from exc
        return digest_bytes(data)

    def _markdown_entries(self, directory: str, kind: FileKind) -> tuple[FileEntry, ...]:
        body = self._directory(directory)
        if not body.is_dir():
            return ()
        entries: list[FileEntry] = []
        for child in sorted(body.iterdir(), key=lambda item: item.name):
            path = f"{directory}/{child.name}"
            if managed_kind(path) != kind:
                continue
            try:
                if not child.is_file() or child.is_symlink():
                    continue
                data = child.read_bytes()
                size = len(data)
            except OSError:
                continue
            try:
                text = decode_text(data, where=f"files.list:{path}")
            except WorkspaceError:
                text = ""
            order = _ORDER_PREFIX.match(child.name)
            entries.append(
                FileEntry(
                    path=path,
                    kind=kind,
                    title=(
                        character_title(text, child.name)
                        if kind == "character"
                        else chapter_title(text, child.name)
                    ),
                    order=order.group(1) if order and kind == "chapter" else "",
                    exists=True,
                    size=size,
                )
            )
        return tuple(entries)

    def _settings(self) -> tuple[FileEntry, ...]:
        entries: list[FileEntry] = []
        for path, title in SETTING_FILES:
            target = self._target(path)
            try:
                exists = target.is_file() and not target.is_symlink()
                size = target.stat().st_size if exists else 0
            except OSError:
                exists, size = False, 0
            entries.append(
                FileEntry(
                    path=path, kind="setting", title=title, order="", exists=exists, size=size
                )
            )
        return tuple(entries)

    def _next_chapter_path(self, title: str) -> str:
        return f"{BODY_DIR}/{max(self._chapter_numbers(), default=0) + 1:04d} {title}.md"

    def _chapter_numbers(self) -> list[int]:
        """只读文件名取已用序号：新建章不需要为了编号读每个文件的内容。"""
        body = self.root / BODY_DIR
        if not body.is_dir():
            return []
        numbers: list[int] = []
        for child in body.iterdir():
            match = _ORDER_PREFIX.match(child.name)
            if match and child.suffix == ".md":
                numbers.append(int(match.group(1)))
        return numbers


def _escaped(path: str) -> WorkspaceError:
    return WorkspaceError(
        code="PATH_NOT_MANAGED",
        where="files.path",
        why=f"路径解析后越出作品目录，或受管目录是链接：{path}",
        fix="把内容放在作品目录内的普通文件夹里；链接与目录联接不作为作品内容",
        details={"path": path},
    )


def _safe_title(title: str) -> str:
    """文件名用的标题：去掉路径分隔与非法字符，限制长度，空标题给默认名。"""
    cleaned = _ILLEGAL_NAME.sub("_", title).replace("/", "_").replace("\\", "_")
    cleaned = " ".join(cleaned.split()).strip(" .")
    cleaned = cleaned[:_MAX_TITLE_CHARS].strip(" .")
    return cleaned or "未命名"


def _atomic_replace(target: Path, payload: bytes, path: str) -> None:
    """同目录临时文件 + 替换：写入中断不会留下半个文件。"""
    handle: int | None = None
    temp_name: str | None = None
    try:
        handle, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".hastings-", suffix=".tmp"
        )
        with os.fdopen(handle, "wb") as stream:
            handle = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        temp_name = None
    except OSError as exc:
        raise WorkspaceError(
            code="FILE_WRITE_FAILED",
            where=f"files.write:{path}",
            why=f"无法写入文件：{exc.strerror or exc}",
            fix="检查磁盘空间与目录写权限；文件可能被其他程序占用",
        ) from exc
    finally:
        if handle is not None:
            os.close(handle)
        if temp_name is not None:
            with suppress(OSError):
                os.unlink(temp_name)

"""语料文件仓（P7 T6，计划 D6）：作者级语料资产的服务级存储。

设计要点（D6）：
- 语料是作者级资产，跨工作区共享——放服务级 ``corpus/`` 目录（与 stories/
  平级），放 workspace 内会误导「每个故事重新上传」；
- 文件名即主键：词干复用 slug/实体名同款路径安全规则（validate_entity_name），
  后缀仅 ``.txt``/``.md``（v1 口径）——杜绝路径穿越与坏后缀落盘；
- 列表只报 size（chars 惰性：档案面板走 style-profile 才算字数）；
- ``uploaded_at`` 取文件 mtime（无元数据文件——文件系统就是账本）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hastings.bible.store import InvalidEntityNameError, validate_entity_name

ALLOWED_SUFFIXES = (".txt", ".md")


class CorpusNameError(ValueError):
    """语料文件名不合法（坏后缀或路径穿越词干）。"""


class CorpusExistsError(KeyError):
    """同名语料已存在（覆盖走显式 DELETE）。"""


class CorpusNotFoundError(KeyError):
    """语料文件不存在。"""


def validate_corpus_name(name: str) -> str:
    """校验语料文件名并原样返回；不合法抛 CorpusNameError（三要素消息）。"""
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    if suffix not in ALLOWED_SUFFIXES:
        msg = (
            f"语料文件名不合法：{name!r}。"
            f"为什么错：v1 仅支持 {'/'.join(ALLOWED_SUFFIXES)}（收到后缀 {suffix or '（无）'}）。"
            f"怎样算对：改为 .txt 或 .md 后重新上传。"
        )
        raise CorpusNameError(msg)
    try:
        validate_entity_name(stem)  # slug 同款规则：禁路径字符、Windows 保留名
    except InvalidEntityNameError as exc:
        msg = (
            f"语料文件名不合法：{name!r}。"
            f"为什么错：{exc}。"
            "怎样算对：文件名只允许字母、数字、中文、空格或连字符（扩展名除外）。"
        )
        raise CorpusNameError(msg) from exc
    return name


@dataclass(frozen=True, slots=True)
class CorpusFileInfo:
    """语料文件条目（列表响应 wire；chars 惰性，列表恒 None）。"""

    name: str
    size_bytes: int
    chars: int | None
    uploaded_at: str


@dataclass(frozen=True, slots=True)
class CorpusStore:
    """服务级语料目录的文件仓（root 即 corpus-root，调用方负责 CLI 装配）。"""

    root: Path

    def _path(self, name: str) -> Path:
        validate_corpus_name(name)
        return self.root / name

    def list_files(self) -> list[CorpusFileInfo]:
        """按文件名排序的语料清单；目录不存在视为空（合法空结果）。"""
        if not self.root.is_dir():
            return []
        files: list[CorpusFileInfo] = []
        for path in self.root.iterdir():
            if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            stat = path.stat()
            files.append(
                CorpusFileInfo(
                    name=path.name,
                    size_bytes=stat.st_size,
                    chars=None,  # 惰性：档案面板走 style-profile 才算字数
                    uploaded_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                )
            )
        return sorted(files, key=lambda f: f.name)

    def exists(self, name: str) -> bool:
        return self._path(name).is_file()

    def save(self, name: str, data: bytes) -> CorpusFileInfo:
        """落盘语料；同名须先显式 DELETE（覆盖不做隐式）。"""
        path = self._path(name)
        if path.is_file():
            msg = (
                f"语料已存在：{name!r}。"
                "为什么错：同名上传被拒绝（覆盖不是隐式行为）。"
                "怎样算对：先 DELETE /api/corpus/{name} 再重新上传，或换一个文件名。"
            )
            raise CorpusExistsError(msg)
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return CorpusFileInfo(
            name=name,
            size_bytes=len(data),
            chars=None,
            uploaded_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        )

    def delete(self, name: str) -> None:
        path = self._path(name)
        if not path.is_file():
            msg = f"语料不存在：{name!r}。怎样算对：先 GET /api/corpus 查看现有语料。"
            raise CorpusNotFoundError(msg)
        path.unlink()

    def load_text(self, name: str) -> str:
        """读入并解码单个语料文件；UnicodeDecodeError / OSError 原样上抛
        （泄压阀分层：读码错由 style-profile 端点转 warnings，盘故障归 500）。"""
        return self._path(name).read_bytes().decode("utf-8")

    def load_all(self) -> tuple[dict[str, str], list[str]]:
        """读入全部语料：返回 {文件名: 文本} 与逐文件跳过的三要素告警。

        读码错（UnicodeDecodeError）是「合法但部分不可读」——跳过该文件并
        告警，不中断整体；OSError 是环境故障，原样上抛交 REST 归因 500。
        """
        texts: dict[str, str] = {}
        warnings: list[str] = []
        for info in self.list_files():
            try:
                texts[info.name] = self.load_text(info.name)
            except UnicodeDecodeError as exc:
                warnings.append(
                    f"语料 {info.name!r} 跳过：不是 UTF-8 编码"
                    f"（为什么错：{exc}；怎样算对：转码为 UTF-8 后重新上传）。"
                )
        return texts, warnings

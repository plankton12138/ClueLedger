"""模块写作轮次的旧文件授权：核对明确的存储位置要求，不推断全部创作意图。

普通的「写人物小传」不产生旧目录写权限；当前请求可以指定旧文件或明确沿用旧目录。
历史要求只有在当前作者要求继续、范围也明确承接它时才参与核对。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import managed_kind

if TYPE_CHECKING:
    from hastings.workspace.task_scope import Mutation

_WRITE = re.compile(
    r"修改|改写|更新|替换|补充|补写|写入|写到|写进|保存|存入|存到|存进|"
    r"新建|创建|撰写|删除|删掉|移除|重命名|改名|移动|沿用|改成|改为|改|写"
)
_DENY = re.compile(
    r"不要|不用|无需|不必|不准|不能|禁止|不得|别(?!忘)|不想|不需要|不允许|不可以|不再|暂不|"
    r"不(?:修改|更新|删除|移动|重命名|改|写|动|保存|使用|沿用)"
)
_REFERENCE = re.compile(r"参考|读取|阅读|查看|看看|对照|根据|依据|仅供")
_CONTINUE = re.compile(r"继续|接着|接下去")
_OLD_STRUCTURE = re.compile(r"旧(?:版)?(?:结构|目录|文件结构)")
_EXISTING_FILES = re.compile(r"(?:已有|现有|原有|原来的)文件")
_CHARACTER_DIRECTORY = re.compile(r"设定集/角色小传(?:/?$|/?目录|下|里|中)")
_SETTINGS_DIRECTORY = re.compile(r"设定集/(?:$|目录)|设定集目录")
_CLAUSE = re.compile(r"[，,。!！？?；;\n]|\.(?=\s|$)|但是?|不过")
_QUOTED = re.compile(
    r"「([^」]*)」|『([^』]*)』|“([^”]*)”|‘([^’]*)’|\"([^\"]*)\"|'([^']*)'|`([^`]*)`"
)


def _clauses(text: str) -> list[str]:
    # 保留引号中的文件名；引号中的整条操作指令不授予权限。
    def quoted(match: re.Match[str]) -> str:
        value = next(group for group in match.groups() if group is not None)
        if managed_kind(value) is not None:
            return f" {value} "
        return "" if _WRITE.search(value) or _DENY.search(value) else f" {value} "

    text = _QUOTED.sub(quoted, text.replace("\\", "/"))
    return [part.strip() for part in _CLAUSE.split(text) if part.strip()]


def _writing_clause(clause: str) -> str:
    """参考对象不作为写入对象；保留参考动作之后明确提出的修改。"""
    action, reference = _WRITE.search(clause), _REFERENCE.search(clause)
    if action is None:
        return ""
    if reference is not None:
        clause = (
            clause[action.start() :]
            if reference.start() < action.start()
            else clause[: reference.start()]
        )
    return clause if _WRITE.search(clause) else ""


def _names_path(clause: str, path: str, *, existing_files: bool) -> bool:
    filename, stem = PurePosixPath(path).name, PurePosixPath(path).stem
    if re.search(re.escape(path) + r"(?![A-Za-z0-9_.])", clause):
        return True
    filename_pattern = (
        r"(?:^|[\s/「」『』“”\"`]|把|将|对|在|"
        + _WRITE.pattern
        + r")\s*"
        + re.escape(filename)
        + r"(?![A-Za-z0-9_.])"
    )
    if re.search(filename_pattern, clause):
        return True
    if _CHARACTER_DIRECTORY.search(clause) or "旧版角色小传" in clause:
        return managed_kind(path) == "character"
    if _SETTINGS_DIRECTORY.search(clause) or _OLD_STRUCTURE.search(clause):
        return True
    if existing_files and stem in clause:
        return True
    return stem in clause and ("文件" in clause or "旧版" in clause or "旧的" in clause)


def require_legacy_file_authorization(
    grants: Sequence[Mutation],
    *,
    author_texts: Sequence[tuple[int, str]],
    carried_from_seq: int | None,
    where: str,
) -> None:
    """在模块工具可用时逐项核对旧文件位置授权；本函数只读取数据。"""
    legacy = [
        (grant.action, path)
        for grant in grants
        for path in (grant.path, grant.target)
        if path is not None and managed_kind(path) in ("character", "setting")
    ]
    if not legacy:
        return
    latest_seq, latest = max(author_texts, default=(0, ""), key=lambda item: item[0])
    current = _clauses(latest)
    sources = [current]
    if carried_from_seq is not None and carried_from_seq < latest_seq:
        continuing = any(
            _CONTINUE.search(clause) and not _DENY.search(clause) for clause in current
        )
        if continuing:
            sources.extend(_clauses(text) for seq, text in author_texts if seq == carried_from_seq)
    for action, path in legacy:
        # 当前消息的明确禁止优先于被承接的历史授权。
        denied = any(
            _DENY.search(clause)
            and (
                _names_path(clause, path, existing_files=False)
                or _EXISTING_FILES.search(clause)
                or "旧文件" in clause
            )
            for clause in current
        )
        permitted = False
        if not denied:
            for clauses in sources:
                writing = [
                    _writing_clause(clause) for clause in clauses if not _DENY.search(clause)
                ]
                existing_files = any(_EXISTING_FILES.search(clause) for clause in writing)
                if any(
                    clause
                    and _names_path(clause, path, existing_files=existing_files)
                    and not (action == "create" and existing_files)
                    for clause in writing
                ):
                    permitted = True
                    break
        if not permitted:
            raise WorkspaceError(
                code="LEGACY_FILE_AUTHORIZATION_REQUIRED_CONFLICT",
                where=where,
                why=f"当前请求未匹配这份旧设定文件的写入授权：{path}",
                fix=(
                    "普通资料创作用 module_content_grants 登记，随后用 prepare_module_changes "
                    "和 apply_module_changes 保存并用于本作品；作者明确指定旧文件或要求沿用"
                    "旧目录时，按该请求登记对应路径。默认的模块保存无需再次询问作者"
                ),
                details={"path": path, "action": action},
            )

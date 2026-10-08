"""打包一份可直接提交的源码快照（不含 .git / .venv / 缓存）。

用法：uv run python scripts/package_submission.py
产物：hackathon/assets/案卷Hastings-源码快照.zip
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "hackathon" / "assets" / "案卷Hastings-源码快照.zip"

INCLUDE_DIRS = ("src", "tests", "scripts", "dev-notes")
INCLUDE_FILES = ("pyproject.toml", "uv.lock", ".env.example", "AGENTS.md", "README.md")
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXECUTABLE_SUFFIXES = {".py", ".md", ".toml", ".lock", ".yaml", ".yml", ".txt", ".example"}


def _collect() -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if EXCLUDE_PARTS & set(path.parts):
                continue
            if path.suffix not in EXECUTABLE_SUFFIXES:
                continue
            files.append(path)
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.exists():
            files.append(path)
    return sorted(files)


def main() -> int:
    files = _collect()
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"已打包 {len(files)} 个文件 → {ZIP_PATH}（{size_mb:.2f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

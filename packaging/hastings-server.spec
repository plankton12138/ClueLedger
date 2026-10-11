# -*- mode: python ; coding: utf-8 -*-
"""Hastings sidecar 打包规格（P9 T11，D9）。

onefile 控制台 exe；hidden imports 由 spike 验证（2026-10-11 实测 5 个一次通过）：
uvicorn 的 logging/loop/http/ws/lifespan 自动选择模块。pydantic v2 与
websockets 由 PyInstaller hooks 自动收集，无需 collect_all。

从仓库根构建（Analysis 路径相对 spec 所在目录 packaging/）：
    uv run pyinstaller packaging/hastings-server.spec \
        --distpath build-resources/sidecar --workpath build/pyinstaller --noconfirm
"""

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="hastings-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

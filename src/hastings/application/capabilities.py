"""0.3 统一配置与能力接口（V03-0.7）：初值是防护值，不是能力承诺。

计划 §6.2/§9.6：导入上限、调用预算、预览保留期、日志分页、图谱首屏等
集中在本模块登记，经 ``GET /api/v2/capabilities`` 统一公布——接口公布
什么，前端就只能看到什么（前端不另写一套常量）。实施基线来自计划 M06/
M10/§9.6 的工程初值；V03-0.7 以真实样本测量后更新，变更记录在
dev-notes/v0.3/artifacts/v03-0-config-and-baseline.md。
"""

from __future__ import annotations

from typing import Any, Final

__all__ = [
    "CAPABILITIES",
    "IMPORT_LIMITS",
    "PERFORMANCE_TARGETS",
    "capabilities_payload",
]

#: 正文导入上限（M06 实施基线）：单文件 10 MiB、单批 20 MiB、最多 1000 章。
IMPORT_LIMITS: Final[dict[str, Any]] = {
    "formats": (".txt", ".md"),
    "encodings": ("utf-8", "utf-8-sig"),
    "max_file_bytes": 10 * 1024 * 1024,
    "max_batch_bytes": 20 * 1024 * 1024,
    "max_chapters_per_batch": 1000,
    "preview_ttl_hours": 24,
}

#: 性能与容量目标（§9.6）：实施验收目标，尚未跨设备承诺。
PERFORMANCE_TARGETS: Final[dict[str, Any]] = {
    "baseline_sample": {
        "chapters": 100,
        "chinese_chars": 300_000,
        "ledger_objects": 500,
        "relations": 1000,
        "execution_events": 10_000,
    },
    "local_draft_save": {"debounce_ms": (500, 1000), "confirm_p95_ms": 1000},
    "chapter_read": {"warm_p95_ms": 500},
    "manual_finish_two_chapters": {"chars_per_chapter": 10_000, "p95_ms": 2000},
    "graph_first_screen": {"max_nodes": 200, "max_edges": 500},
    "execution_log": {"page_size": 200},
}

#: 能力公布：implemented=当前默认装配可用；draft=契约已冻结、实现进行中；
# excluded=本版排除（不公布为可用，也不提供占位入口）。
CAPABILITIES: Final[dict[str, Any]] = {
    "product_version": "0.3",
    "api_version": "v2",
    "import": {**IMPORT_LIMITS, "status": "implemented"},
    "edit_sessions": {"status": "implemented"},
    "operation_records": {"status": "implemented"},
    "chapter_drafts": {
        "status": "implemented",
        "max_chars": 120_000,
        "targets": ("new", "existing_chapter"),
    },
    "execution_grants": {"status": "implemented"},
    "execution_events": {"status": "implemented"},
    "summaries": {
        "status": "draft",
        "api": "implemented",
        "jobs": "implemented",
        "surface": "pending",
    },
    "ledger_suggestions": {
        "status": "draft",
        "api": "implemented",
        "surface": "pending",
    },
    "agreements": {"status": "draft", "api": "implemented", "surface": "pending"},
    "background": {
        "status": "implemented",
        "default_tokens": 12_000,
        "max_tokens": 64_000,
        "tiers": ("agreements", "core", "overview", "index", "target"),
        "prompt_wiring": ("manuscript", "discussion"),
    },
    "undo": {
        "status": "draft",
        "preview": "implemented",
        "apply": "implemented",
        "surface": "pending",
    },
    "graphs": {"status": "draft"},
    "author_model": {"status": "excluded"},
    "style_observation": {"status": "excluded"},
    "consistency_audit": {"status": "excluded"},
    "corpus_rag": {"status": "excluded"},
}


def capabilities_payload() -> dict[str, Any]:
    """``GET /api/v2/capabilities`` 响应体（草案）：统一配置的机器可读公布。"""
    return {
        "product_version": CAPABILITIES["product_version"],
        "api_version": CAPABILITIES["api_version"],
        "import": dict(IMPORT_LIMITS),
        "performance_targets": {
            key: value for key, value in PERFORMANCE_TARGETS.items() if key != "baseline_sample"
        },
        "features": {
            name: dict(spec)
            for name, spec in CAPABILITIES.items()
            if isinstance(spec, dict) and "status" in spec
        },
    }

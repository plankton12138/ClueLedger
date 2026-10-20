"""V03-0.2 四类稿件身份、请求指纹与前后端样例往返（计划 §4.1、§3.2、§6.1）。

冻结内容：
- 四类身份可辨识联合与 ID 前缀（不变量 I01：引用带类型、拒绝混用）；
- 业务层请求指纹排除并发凭据（对齐 CommitService._request_fingerprint）；
- 前后端共用样例 payload（frontend/src/api/__fixtures__/v03-identities.json）
  的 Python 侧往返：from_wire → to_wire 逐字段比对。

与 TS 侧 identities.test.ts 读同一夹具；改动契约必须两侧同时通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.application.errors import V03_ERROR_CODES
from hastings.application.idempotency import (
    CONCURRENCY_CREDENTIAL_FIELDS,
    operation_key_reused,
    request_fingerprint,
)
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.identities import (
    ID_PREFIXES,
    ManuscriptKind,
    ManuscriptRef,
    OperationEnvelope,
    kind_of_id,
    reject_mixed_kinds,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "api"
    / "__fixtures__"
    / "v03-identities.json"
)

FORMAL = ManuscriptRef(
    kind=ManuscriptKind.FORMAL,
    id="art_9f2c1d4e6a8b0c2d4e6f8a0b",
    commit_id="cmt_1a2b3c4d5e6f7a8b9c0d1e2f",
)
EDIT_DRAFT = ManuscriptRef(
    kind=ManuscriptKind.EDIT_DRAFT,
    id="eds_3c4d5e6f7a8b9c0d1e2f3a4b",
    save_seq=7,
)
CANDIDATE = ManuscriptRef(
    kind=ManuscriptKind.CANDIDATE,
    id="cnd_5e6f7a8b9c0d1e2f3a4b5c6d",
    content_hash="0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d",
)
TASK_SNAPSHOT = ManuscriptRef(
    kind=ManuscriptKind.TASK_SNAPSHOT,
    id="tks_7a8b9c0d1e2f3a4b5c6d7e8f",
    context_version=2,
)


# ---------------------------------------------------------------------------
# 身份与 ID 前缀
# ---------------------------------------------------------------------------


def test_kind_of_id_infers_from_prefix() -> None:
    assert kind_of_id("art_9f2c") is ManuscriptKind.FORMAL
    assert kind_of_id("rev_9f2c") is ManuscriptKind.FORMAL
    assert kind_of_id("cmt_9f2c") is ManuscriptKind.FORMAL
    assert kind_of_id("eds_9f2c") is ManuscriptKind.EDIT_DRAFT
    assert kind_of_id("edc_9f2c") is ManuscriptKind.EDIT_DRAFT
    assert kind_of_id("cnd_9f2c") is ManuscriptKind.CANDIDATE
    assert kind_of_id("tks_9f2c") is ManuscriptKind.TASK_SNAPSHOT
    assert kind_of_id("zzz_9f2c") is None
    assert kind_of_id("noprefix") is None


@pytest.mark.parametrize(
    ("kind", "object_id", "missing"),
    [
        (ManuscriptKind.FORMAL, "art_1", "commit_id"),
        (ManuscriptKind.EDIT_DRAFT, "eds_1", "save_seq"),
        (ManuscriptKind.CANDIDATE, "cnd_1", "content_hash"),
        (ManuscriptKind.TASK_SNAPSHOT, "tks_1", "context_version"),
    ],
)
def test_each_kind_requires_its_anchor(kind: ManuscriptKind, object_id: str, missing: str) -> None:
    with pytest.raises(ManuscriptViolation) as excinfo:
        ManuscriptRef(kind=kind, id=object_id)
    assert excinfo.value.code == "MANUSCRIPT_MISSING_REQUIRED_FIELD"
    assert missing in excinfo.value.where


def test_prefix_must_match_kind() -> None:
    with pytest.raises(ManuscriptViolation) as excinfo:
        ManuscriptRef(kind=ManuscriptKind.FORMAL, id="cnd_5e6f", commit_id="cmt_1")
    assert excinfo.value.code == "MANUSCRIPT_INVALID_OPERATION"
    assert excinfo.value.details["allowed_prefixes"] == list(ID_PREFIXES[ManuscriptKind.FORMAL])


def test_cross_kind_anchor_rejected() -> None:
    """正式稿引用携带副本序号：身份互斥（I01），wire 层硬约束。"""
    with pytest.raises(ManuscriptViolation) as excinfo:
        ManuscriptRef(
            kind=ManuscriptKind.FORMAL,
            id="art_9f2c1d4e6a8b0c2d4e6f8a0b",
            commit_id="cmt_1a2b3c4d5e6f7a8b9c0d1e2f",
            save_seq=7,
        )
    assert excinfo.value.details["field"] == "save_seq"


def test_reject_mixed_kinds() -> None:
    with pytest.raises(ManuscriptViolation) as excinfo:
        reject_mixed_kinds(FORMAL, EDIT_DRAFT, where="operations.accept")
    assert excinfo.value.details == {
        "left_kind": "formal",
        "right_kind": "edit_draft",
    }
    # 同类不拒
    reject_mixed_kinds(FORMAL, FORMAL, where="operations.accept")


def test_id_prefixes_cover_new_v03_prefixes() -> None:
    assert ID_PREFIXES[ManuscriptKind.EDIT_DRAFT] == ("eds", "edc")
    assert ID_PREFIXES[ManuscriptKind.CANDIDATE] == ("cnd",)
    assert ID_PREFIXES[ManuscriptKind.TASK_SNAPSHOT] == ("tks",)


# ---------------------------------------------------------------------------
# 请求指纹：排除并发凭据
# ---------------------------------------------------------------------------


def _publish_request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "operation_id": "op_2b3c",
        "project_id": "prj_4d5e",
        "target": {"kind": "formal", "chapter_ids": ["art_1", "art_2"]},
        "text_changes": [{"unit_id": "art_1", "text": "新文本"}],
    }
    base.update(overrides)
    return base


def test_fingerprint_excludes_concurrency_credentials() -> None:
    base = _publish_request()
    replayed = _publish_request(
        expected_head="cmt_new",
        expected_save_seq=9,
        expected_revision="rev_new",
        required_leases=["lease_1"],
    )
    assert request_fingerprint(base) == request_fingerprint(replayed)


def test_fingerprint_is_content_sensitive() -> None:
    base = _publish_request()
    other = _publish_request(text_changes=[{"unit_id": "art_1", "text": "别的文本"}])
    assert request_fingerprint(base) != request_fingerprint(other)


def test_credential_fields_registry_is_frozen_set() -> None:
    assert "expected_head" in CONCURRENCY_CREDENTIAL_FIELDS
    assert "expected_save_seq" in CONCURRENCY_CREDENTIAL_FIELDS
    # 「写什么」字段不在排除集
    assert "text_changes" not in CONCURRENCY_CREDENTIAL_FIELDS
    assert "operation_id" not in CONCURRENCY_CREDENTIAL_FIELDS


def test_operation_key_reused_error_shape() -> None:
    error = operation_key_reused(
        "op_2b3c4d5e6f7a8b9c0d1e2f3a",
        where="operations/op_2b3c4d5e6f7a8b9c0d1e2f3a",
        original={"operation_id": "op_2b3c4d5e6f7a8b9c0d1e2f3a", "status": "committed"},
    )
    assert error.code == "OPERATION_KEY_REUSED"
    assert error.code in V03_ERROR_CODES
    assert error.category.value == "violation"
    # 三要素齐全（where/why/fix）
    assert error.where and error.why and error.fix
    assert error.details["operation_id"] == "op_2b3c4d5e6f7a8b9c0d1e2f3a"
    assert error.details["original"]["status"] == "committed"


# ---------------------------------------------------------------------------
# 前后端共用夹具往返（Python 侧）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def samples() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_manuscript_ref_round_trip(samples: dict, index: int) -> None:
    wire = samples["manuscript_refs"][index]
    assert ManuscriptRef.from_wire(wire).to_wire() == wire


def test_operation_envelope_round_trip(samples: dict) -> None:
    for wire in samples["operation_envelopes"]:
        assert OperationEnvelope.from_wire(wire).to_wire() == wire


def test_no_change_envelope_has_no_commit(samples: dict) -> None:
    no_change = samples["operation_envelopes"][1]
    assert no_change["status"] == "no_change"
    assert "commit_id" not in no_change  # 无差异不伪造 commit（计划 §3.3）


def test_error_envelope_matches_operation_key_reused(samples: dict) -> None:
    expected = samples["error_envelope"]["error"]
    error = operation_key_reused(
        expected["details"]["operation_id"],
        where=expected["where"],
        original=expected["details"]["original"],
    )
    assert {
        "code": error.code,
        "category": error.category.value,
        "where": error.where,
        "why": error.why,
        "fix": error.fix,
        "details": dict(error.details),
    } == expected

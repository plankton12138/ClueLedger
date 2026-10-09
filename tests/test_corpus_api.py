"""语料库端点测试（P7 T6，计划 D6）。

泄压阀三情形逐条钉住：
- 参数违规（坏后缀、路径穿越词干、空文件、缺文件名）→ 422 三要素；
- 合法但空（空语料库列表、空档案全零）→ 200，不是错误；
- 同名上传 → 409；不存在删除 → 404；读码失败 → 200 + warnings（部分不可读）。
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.server.app import create_app
from test_server_app import stub_registry

_SLUG = "orient-express"


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[[Path], TestClient]:
    stories = tmp_path / "stories"

    def factory(corpus_root: Path) -> TestClient:
        stories.mkdir(parents=True, exist_ok=True)
        return TestClient(create_app(stub_registry(stories), corpus_root=corpus_root))

    return factory


def test_upload_list_delete_roundtrip(tmp_path: Path, make_client) -> None:
    client = make_client(tmp_path / "corpus")
    payload = "他推开门。外面下着雨。".encode()
    resp = client.post("/api/corpus", files={"file": ("旧作.txt", payload, "text/plain")})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "旧作.txt"
    assert body["size_bytes"] == len(payload)
    assert body["chars"] is None  # 惰性：列表只报 size，字数走 style-profile
    assert body["uploaded_at"] != ""

    listed = client.get("/api/corpus").json()["files"]
    assert [f["name"] for f in listed] == ["旧作.txt"]

    assert client.delete("/api/corpus/旧作.txt").status_code == 204
    assert client.get("/api/corpus").json()["files"] == []
    assert client.delete("/api/corpus/旧作.txt").status_code == 404


def test_upload_rejects_bad_suffix(tmp_path: Path, make_client) -> None:
    client = make_client(tmp_path / "corpus")
    resp = client.post("/api/corpus", files={"file": ("旧作.pdf", b"data", "application/pdf")})
    assert resp.status_code == 422
    assert "为什么错" in resp.json()["detail"]
    assert "怎样算对" in resp.json()["detail"]


def test_upload_rejects_path_traversal_stem(tmp_path: Path, make_client) -> None:
    client = make_client(tmp_path / "corpus")
    resp = client.post(
        "/api/corpus",
        files={"file": ("../evil.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 422
    assert (tmp_path / "evil.txt").exists() is False
    assert (tmp_path / "corpus").joinpath("evil.txt").exists() is False


def test_upload_duplicate_is_409(tmp_path: Path, make_client) -> None:
    client = make_client(tmp_path / "corpus")
    files = {"file": ("旧作.md", "# 一".encode(), "text/markdown")}
    assert client.post("/api/corpus", files=files).status_code == 201
    resp = client.post("/api/corpus", files=files)
    assert resp.status_code == 409
    assert "DELETE" in resp.json()["detail"]  # 怎样算对：显式删除或改名


def test_upload_empty_file_is_422(tmp_path: Path, make_client) -> None:
    client = make_client(tmp_path / "corpus")
    resp = client.post("/api/corpus", files={"file": ("空.txt", b"", "text/plain")})
    assert resp.status_code == 422


def test_style_profile_aggregates_and_warns(tmp_path: Path, make_client) -> None:
    corpus = tmp_path / "corpus"
    client = make_client(corpus)
    client.post("/api/corpus", files={"file": ("a.txt", "推开门。雨不停。".encode(), "text/plain")})
    client.post(
        "/api/corpus",
        files={"file": ("乱码.txt", b"\xff\xfe\xff", "text/plain")},  # 非 UTF-8
    )
    resp = client.get("/api/corpus/style-profile")
    assert resp.status_code == 200
    body = resp.json()
    # 乱码文件跳过不 500（合法但部分不可读 → warnings 三要素）
    assert len(body["warnings"]) == 1
    assert "乱码.txt" in body["warnings"][0]
    assert body["total_chars"] == 8  # 去空白口径（标点计入）：3+1+3+1
    assert body["sentence_count"] == 2
    assert body["sentence_buckets"]["1-10"] == 2


def test_style_profile_empty_corpus_is_legal_zero(tmp_path: Path, make_client) -> None:
    client = make_client(tmp_path / "corpus")
    resp = client.get("/api/corpus/style-profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_chars"] == 0
    assert body["warnings"] == []

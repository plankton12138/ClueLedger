"""SQLite schema（Phase 12 T12.3，modules/repository-kernel.md §6 建议表）。

约束策略：
- 主键/外键/唯一约束支撑核心不变量（branch 名项目内唯一、
  commit sequence 项目内唯一递增、outbox sequence 项目内唯一）；
- 外键必须开启（PRAGMA foreign_keys=ON，由连接工厂保证）；
- schema 版本保存在 PRAGMA user_version，迁移器据此升级或拒绝。
"""

from __future__ import annotations

__all__ = ["SCHEMA_VERSION", "SCHEMA_SQL"]

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_artifacts_project ON artifacts(project_id);

CREATE TABLE revisions (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    parent_id TEXT REFERENCES revisions(id),
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    author_kind TEXT NOT NULL,
    author_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_revisions_artifact ON revisions(artifact_id);

CREATE TABLE commits (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    message TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    UNIQUE(project_id, sequence)
);

CREATE TABLE commit_parents (
    commit_id TEXT NOT NULL REFERENCES commits(id),
    position INTEGER NOT NULL,
    parent_id TEXT NOT NULL REFERENCES commits(id),
    PRIMARY KEY (commit_id, position)
);

CREATE TABLE commit_entries (
    commit_id TEXT NOT NULL REFERENCES commits(id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    old_revision_id TEXT REFERENCES revisions(id),
    new_revision_id TEXT NOT NULL REFERENCES revisions(id),
    PRIMARY KEY (commit_id, artifact_id)
);
CREATE INDEX idx_entries_artifact ON commit_entries(artifact_id);

CREATE TABLE branches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    head_commit_id TEXT REFERENCES commits(id),
    version INTEGER NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE relation_revisions (
    id TEXT PRIMARY KEY,
    commit_id TEXT NOT NULL REFERENCES commits(id),
    action TEXT NOT NULL CHECK (action IN ('add', 'remove')),
    source TEXT NOT NULL REFERENCES artifacts(id),
    relation_type TEXT NOT NULL,
    target TEXT NOT NULL REFERENCES artifacts(id),
    metadata TEXT NOT NULL
);
CREATE INDEX idx_relations_commit ON relation_revisions(commit_id);

CREATE TABLE outbox_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    commit_id TEXT REFERENCES commits(id),
    sequence INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(project_id, sequence)
);
CREATE INDEX idx_outbox_unpublished ON outbox_events(project_id)
    WHERE published_at IS NULL;

CREATE TABLE leases (
    resource_key TEXT PRIMARY KEY,
    owner_job_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    version INTEGER NOT NULL
);

CREATE TABLE idempotency_records (
    project_id TEXT NOT NULL REFERENCES projects(id),
    key TEXT NOT NULL,
    commit_id TEXT NOT NULL,
    new_head TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    changed_artifact_ids TEXT NOT NULL,
    no_change_reason TEXT,
    request_fingerprint TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, key)
);
"""

import asyncio
from datetime import UTC, datetime

import pytest

from hastings.infrastructure.jobs import SqliteJobStore


def test_job_claim_cancel_and_restart_are_fenced(tmp_path):
    async def check():
        path = str(tmp_path / "jobs.sqlite")
        store = SqliteJobStore(path)
        jid = await store.create("writer", {"project_id": "p", "idempotency_key": "one"})
        assert await store.create("writer", {"project_id": "p", "idempotency_key": "one"}) == jid
        claimed = await store.claim(jid, "owner", now=datetime.now(UTC), ttl=60)
        assert claimed["state"] == "running"
        await store.cancel(jid)
        with pytest.raises(ValueError):
            await store.finish(jid, claimed["generation"], "waiting", {"kind": "no_result"})
        restored = SqliteJobStore(path)
        assert (await restored.get(jid))["state"] == "cancelled"
        events = await restored.events(jid)
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        assert all(e["commit_id"] is None for e in events)

    asyncio.run(check())


def test_expired_job_resumes_checkpoint_without_replaying_worker(tmp_path):
    async def check():
        store = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
        jid = await store.create("writer", {"project_id": "p"})
        now = datetime(2026, 10, 15, tzinfo=UTC)
        claimed = await store.claim(jid, "first", now=now, ttl=1)
        await store.checkpoint(jid, claimed["generation"], {"kind": "no_result", "reason": "done"})
        from datetime import timedelta

        assert await store.recover(now + timedelta(seconds=2)) == 1
        next_claim = await store.claim(jid, "second", now=now + timedelta(seconds=3), ttl=60)
        assert next_claim["checkpoint"]["reason"] == "done"
        with pytest.raises(ValueError):
            await store.finish(jid, claimed["generation"], "completed", {})

    asyncio.run(check())

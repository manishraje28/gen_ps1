"""Gmail reads are shared and cached so the two parallel agents and the UI stay under Google's per-minute
quota (offline: a fake Gmail service counts the API calls)."""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations import gmail  # noqa: E402


def _msg(i):
    return {"id": f"m{i}", "threadId": f"t{i}", "internalDate": str(1_700_000_000_000 + i * 1000),
            "payload": {"mimeType": "text/plain", "headers": [
                {"name": "From", "value": f"sender{i}@example.com"}, {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": f"Subject {i}"}, {"name": "Date", "value": "Fri, 16 Oct 2026 10:00:00 +0530"}],
                "body": {"data": ""}}}


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGmail:
    def __init__(self, n=30, quota_after=None, delay=0.0):
        self.calls = {"list": 0, "get": 0}
        self.n, self.quota_after, self.delay = n, quota_after, delay
        self.lock = threading.Lock()

    def _count(self, kind):
        with self.lock:
            self.calls[kind] += 1
            total = sum(self.calls.values())
        if self.quota_after is not None and total > self.quota_after:
            raise RuntimeError("HttpError 403: Quota exceeded for quota metric 'Total Query Cost'")
        time.sleep(self.delay)

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        def run():
            self._count("list")
            return {"messages": [{"id": f"m{i}"} for i in range(min(maxResults, self.n))]}
        return _Req(run)

    def get(self, userId, id, format):
        def run():
            self._count("get")
            return _msg(int(id[1:]))
        return _Req(run)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("GMAIL_QUOTA_BACKOFF", "0")
    monkeypatch.delenv("GMAIL_MAX_RESULTS", raising=False)
    f = FakeGmail(delay=0.01)
    gmail.set_service(f)
    gmail.clear_cache()
    yield f
    gmail.set_service(None)
    gmail.clear_cache()


def test_fetches_ten_by_default(fake):
    assert len(gmail._fetch()) == 10
    assert fake.calls == {"list": 1, "get": 10}


def test_max_results_setting(fake, monkeypatch):
    monkeypatch.setenv("GMAIL_MAX_RESULTS", "5")
    assert len(gmail._fetch()) == 5


def test_two_parallel_lanes_share_one_read(fake):
    results = []
    threads = [threading.Thread(target=lambda: results.append(gmail._fetch())) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [len(r) for r in results] == [10, 10]
    assert fake.calls == {"list": 1, "get": 10}  # not 2 x (1 + 10)


def test_listing_reused_and_messages_downloaded_once(fake):
    gmail._fetch()
    gmail._fetch()                       # same listing within the TTL: no API call
    gmail.read_email(type("Inbox", (), {"sent": []})(), "m3")  # already downloaded: no API call
    gmail.snapshot(ttl=0)                # UI panel: listing cached, messages cached
    assert fake.calls == {"list": 1, "get": 10}


def test_new_listing_only_downloads_new_messages(fake, monkeypatch):
    gmail._fetch()
    monkeypatch.setattr(gmail, "LIST_TTL", 0.0)
    time.sleep(0.01)
    gmail._fetch()
    assert fake.calls == {"list": 2, "get": 10}


def test_quota_error_serves_last_good_listing(fake, monkeypatch):
    first = gmail._fetch()
    monkeypatch.setattr(gmail, "LIST_TTL", 0.0)
    fake.quota_after = 0  # every further call is over quota
    time.sleep(0.01)
    again = gmail._fetch()
    assert [e.id for e in again] == [e.id for e in first]


def test_quota_error_without_cache_is_raised(fake):
    fake.quota_after = 0
    with pytest.raises(RuntimeError, match="Quota exceeded"):
        gmail._fetch()

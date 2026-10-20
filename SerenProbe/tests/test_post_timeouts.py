"""live_eval.post gives each kind of request its own budget.

A /search fan-out can take minutes on a cold cross-store corpus; a draft submit or
review lands up to 48 long-term operations in one call (both Vellmarch slices died
in seeding on the nuc at the 30 s every other write gets, 20 Oct 2026); a plain
write stays tight.
"""
import pytest

from seren_probe.runtime import live_eval
from seren_probe.runtime.write_guard import allow_targets, clear_targets

URL = "http://127.0.0.1:7620"


@pytest.fixture
def owned():
    allow_targets([URL])
    yield
    clear_targets()


class _Reply:
    content = b"{}"

    def raise_for_status(self):
        pass

    def json(self):
        return {}


@pytest.mark.parametrize("path,kind", [
    ("/search", "search"),
    ("/drafts", "draft"),
    ("/drafts/0123abcd/review", "draft"),
    ("/fact", "write"),
    ("/short", "write"),
])
def test_each_kind_of_request_gets_its_own_timeout(owned, monkeypatch, path, kind):
    seen = {}

    def fake_post(url, json, timeout):
        seen["timeout"] = timeout
        return _Reply()
    monkeypatch.setattr(live_eval.httpx, "post", fake_post)
    live_eval.post(URL, path, {})
    want = {"search": live_eval._SEARCH_TIMEOUT, "draft": live_eval._DRAFT_TIMEOUT, "write": 30.0}[kind]
    assert seen["timeout"] == want


def test_the_slow_budgets_are_slower_than_a_plain_write():
    assert live_eval._DRAFT_TIMEOUT > 30.0
    assert live_eval._SEARCH_TIMEOUT > 30.0

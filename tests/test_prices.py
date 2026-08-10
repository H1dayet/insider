"""Runnable checks for prices.py's earnings-date parsing - the only branchy
logic in this module (lookup()/average_daily_dollar_volume() are exercised
live elsewhere in the project, not unit-tested against fixtures)."""
from datetime import date, datetime, timezone

from insider import prices


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _raw(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


def test_next_earnings_date_parses_first_upcoming_date(monkeypatch):
    target = date(2026, 12, 31)
    payload = {
        "quoteSummary": {
            "result": [{"calendarEvents": {"earnings": {"earningsDate": [{"raw": _raw(target)}]}}}],
            "error": None,
        }
    }
    monkeypatch.setattr(prices.requests, "get", lambda *a, **k: _FakeResp(200, payload))
    assert prices.next_earnings_date("ACME") == target


def test_next_earnings_date_returns_none_when_no_date_on_file(monkeypatch):
    payload = {"quoteSummary": {"result": [{"calendarEvents": {}}], "error": None}}
    monkeypatch.setattr(prices.requests, "get", lambda *a, **k: _FakeResp(200, payload))
    assert prices.next_earnings_date("ACME") is None


def test_next_earnings_date_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(prices.requests, "get", lambda *a, **k: _FakeResp(500, {}))
    try:
        prices.next_earnings_date("ACME")
        assert False, "expected PriceUnavailable"
    except prices.PriceUnavailable:
        pass


if __name__ == "__main__":
    print("run via pytest for monkeypatch-based tests")

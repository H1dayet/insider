import json

import pandas as pd

from northstar.pipeline import feed_freshness, publish_delayed_feed


def test_missing_previous_close_is_delayed_after_midnight():
    result = feed_freshness(pd.Timestamp("2026-09-21"), "2026-09-23T00:54Z")
    assert result["delayed"]
    assert result["expected_session"] == "2026-09-22"


def test_weekend_and_finalization_buffer_do_not_raise_false_delays():
    assert not feed_freshness(pd.Timestamp("2026-09-18"), "2026-09-20T12:00Z")["delayed"]
    assert not feed_freshness(pd.Timestamp("2026-09-21"), "2026-09-22T20:15Z")["delayed"]
    assert not feed_freshness(pd.Timestamp("2026-09-22"), "2026-09-23T06:00Z")["delayed"]


def test_delay_preserves_record_and_successful_data_timestamp(tmp_path, monkeypatch):
    import northstar.pipeline as pipeline
    monkeypatch.setattr(pipeline, "DOCS", tmp_path)
    payload = {"meta": {"generated_at": "original", "latest_completed_session": "2026-09-21"},
               "track_record": {"profit": 0, "daily_records": [{"date": "2026-09-21"}]}}
    (tmp_path / "data.json").write_text(json.dumps(payload))
    freshness = feed_freshness(pd.Timestamp("2026-09-21"), "2026-09-23T06:00Z")
    result = publish_delayed_feed(freshness)
    assert result["track_record"] == payload["track_record"]
    assert result["meta"]["generated_at"] == "original"
    assert result["meta"]["freshness"]["delayed"]


def test_delayed_preflight_does_not_download_universe_or_mutate_portfolios(tmp_path, monkeypatch):
    import northstar.pipeline as pipeline
    monkeypatch.setattr(pipeline, "DOCS", tmp_path)
    (tmp_path / "data.json").write_text(json.dumps({"meta": {}, "track_record": {"profit": 12}}))
    monkeypatch.setattr(pipeline, "download_history", lambda ticker: pd.DataFrame())
    monkeypatch.setattr(pipeline, "common_session", lambda frames: pd.Timestamp("2026-09-21"))
    monkeypatch.setattr(pipeline, "feed_freshness", lambda latest: {"delayed": True, "message": "Waiting"})
    def unexpected():
        raise AssertionError("Must not refresh universe while benchmark is delayed")
    monkeypatch.setattr(pipeline, "load_universe", unexpected)
    assert pipeline.run()["track_record"]["profit"] == 12

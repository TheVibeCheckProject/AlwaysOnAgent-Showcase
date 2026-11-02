"""The TikTok numbers once a day at 14:00 (core/tiktok_videos.py), for the quote writer's "what resonates"."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import tiktok_videos  # noqa: E402
from core.database import DatabaseManager  # noqa: E402

TZ = datetime.timezone(datetime.timedelta(hours=-5))


def test_once_a_day_after_two_and_a_failure_counts_as_the_days_try(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "s.db")
    morning = datetime.datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
    afternoon = morning.replace(hour=15)
    assert not tiktok_videos.stats_due(db, morning)
    assert tiktok_videos.stats_due(db, afternoon)
    calls = []

    def fail(args, **k):
        calls.append(args)
        raise tiktok_videos.TiktokVideosError("signed out")
    monkeypatch.setattr(tiktok_videos, "run", fail)
    tiktok_videos.run_daily_stats(db)
    assert calls == [["stats", "--days", "7"]]
    today = datetime.datetime.now().astimezone().replace(hour=15)
    assert not tiktok_videos.stats_due(db, today)              # not again today, even though it failed

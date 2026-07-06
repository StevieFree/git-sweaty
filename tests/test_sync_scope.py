import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import sync_scope  # noqa: E402


class SyncScopeTests(unittest.TestCase):
    def test_start_after_ts_prefers_start_date(self) -> None:
        ts = sync_scope.start_after_ts({"sync": {"start_date": "2024-01-15", "lookback_years": 2}})
        expected = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp())
        self.assertEqual(ts, expected)

    def test_start_after_ts_uses_lookback_when_start_date_missing(self) -> None:
        frozen_now = datetime(2026, 2, 17, tzinfo=timezone.utc)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz else frozen_now.replace(tzinfo=None)

        with mock.patch("sync_scope.datetime", FrozenDateTime):
            ts = sync_scope.start_after_ts({"sync": {"lookback_years": 2}})

        expected = int(datetime(2024, 2, 17, tzinfo=timezone.utc).timestamp())
        self.assertEqual(ts, expected)

    def test_start_after_ts_defaults_to_zero_with_no_limits(self) -> None:
        self.assertEqual(sync_scope.start_after_ts({"sync": {}}), 0)

    def _write_state(self, data_dir: str, filename: str, newest_seen_ts: int) -> None:
        import json

        with open(os.path.join(data_dir, filename), "w", encoding="utf-8") as f:
            json.dump({"newest_seen_ts": newest_seen_ts, "completed": True}, f)

    def test_cross_source_floor_uses_other_source_newest_when_merging(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            self._write_state(data_dir, "backfill_state_strava.json", 1751385600)
            config = {"sync": {"merge_sources": True}}
            # Switching to Garmin floors at the newest Strava activity.
            self.assertEqual(
                sync_scope.resolve_after_ts("garmin", config, data_dir), 1751385600
            )
            # A source never floors against its own state.
            self.assertEqual(sync_scope.resolve_after_ts("strava", config, data_dir), 0)

    def test_cross_source_floor_disabled_without_merge(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            self._write_state(data_dir, "backfill_state_strava.json", 1751385600)
            self.assertEqual(
                sync_scope.resolve_after_ts("garmin", {"sync": {"merge_sources": False}}, data_dir),
                0,
            )

    def test_cross_source_floor_from_normalized_when_state_file_absent(self) -> None:
        import json
        import tempfile

        # Mirrors CI/dashboard-data: normalized store present, no backfill state.
        activities = [
            {"id": "1", "source": "strava", "start_date_local": "2026-07-01T06:45:19Z"},
            {"id": "2", "start_date_local": "2026-06-01T00:00:00Z"},  # legacy -> strava
            {"id": "3", "source": "garmin", "start_date_local": "2026-07-06T10:00:00Z"},
        ]
        with tempfile.TemporaryDirectory() as data_dir:
            with open(os.path.join(data_dir, "activities_normalized.json"), "w", encoding="utf-8") as f:
                json.dump(activities, f)
            config = {"sync": {"merge_sources": True}}
            expected = int(datetime(2026, 7, 1, 6, 45, 19, tzinfo=timezone.utc).timestamp())
            self.assertEqual(
                sync_scope.resolve_after_ts("garmin", config, data_dir), expected
            )

    def test_explicit_start_date_overrides_cross_source_floor(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as data_dir:
            self._write_state(data_dir, "backfill_state_strava.json", 1751385600)
            config = {"sync": {"merge_sources": True, "start_date": "2020-01-01"}}
            expected = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
            self.assertEqual(
                sync_scope.resolve_after_ts("garmin", config, data_dir), expected
            )

    def test_activity_scope_from_config(self) -> None:
        scope = sync_scope.activity_scope_from_config(
            {
                "activities": {
                    "include_all_types": False,
                    "exclude_types": ["Walk"],
                    "types": ["Run", "Ride"],
                    "group_other_types": True,
                    "other_bucket": "OtherSports",
                    "type_aliases": {"Jog": "Run"},
                    "group_aliases": {"TrailRun": "Run"},
                }
            }
        )
        self.assertFalse(scope["include_all_types"])
        self.assertEqual(scope["exclude_types"], ["Walk"])
        self.assertEqual(scope["featured_types"], ["Ride", "Run"])
        self.assertEqual(scope["type_aliases"], {"Jog": "Run"})
        self.assertEqual(scope["group_aliases"], {"TrailRun": "Run"})

    def test_activity_start_ts_handles_iso_and_invalid(self) -> None:
        ts = sync_scope.activity_start_ts({"start_date": "2026-02-17T09:30:00Z"})
        self.assertEqual(ts, int(datetime(2026, 2, 17, 9, 30, tzinfo=timezone.utc).timestamp()))
        self.assertIsNone(sync_scope.activity_start_ts({"start_date": "bad"}))


if __name__ == "__main__":
    unittest.main()

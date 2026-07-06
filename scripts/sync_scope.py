import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from activity_types import featured_types_from_config

# Backfill state files per source (newest first for legacy fallbacks).
SOURCE_STATE_FILES = {
    "strava": ["backfill_state_strava.json", "backfill_state.json"],
    "garmin": ["backfill_state_garmin.json"],
}
SUPPORTED_SYNC_SOURCES = ("strava", "garmin")
NORMALIZED_PATH = "activities_normalized.json"
LEGACY_SOURCE = "strava"


def lookback_after_ts(years: int) -> int:
    now = datetime.now(timezone.utc)
    try:
        start = now.replace(year=now.year - years)
    except ValueError:
        # handle Feb 29
        start = now.replace(month=2, day=28, year=now.year - years)
    return int(start.timestamp())


def start_after_ts(config: Dict[str, Any]) -> int:
    sync_cfg = config.get("sync", {})
    start_date = sync_cfg.get("start_date")
    if start_date:
        dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    lookback_years = sync_cfg.get("lookback_years")
    if lookback_years in (None, ""):
        return 0
    return lookback_after_ts(int(lookback_years))


def _newest_seen_ts_for_source(source: str, data_dir: str = "data") -> int:
    for name in SOURCE_STATE_FILES.get(source, []):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            continue
        if not isinstance(state, dict):
            continue
        ts = state.get("newest_seen_ts")
        if isinstance(ts, (int, float)) and ts > 0:
            return int(ts)
    return 0


def _newest_activity_ts_for_source(source: str, data_dir: str = "data") -> int:
    """Newest activity start timestamp for a source in the persisted normalized
    store. Robust to a missing backfill-state file (always available in CI where
    activities_normalized.json is restored but state files may not be)."""
    path = os.path.join(data_dir, NORMALIZED_PATH)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return 0
    if not isinstance(items, list):
        return 0
    newest = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_source = str(item.get("source") or "").strip().lower()
        if item_source not in SUPPORTED_SYNC_SOURCES:
            item_source = LEGACY_SOURCE
        if item_source != source:
            continue
        ts = activity_start_ts(item)
        if ts is not None and ts > newest:
            newest = ts
    return newest


def cross_source_after_floor(current_source: str, config: Dict[str, Any], data_dir: str = "data") -> int:
    """Newest activity timestamp already captured from *other* sources.

    When merge_sources is enabled and you switch providers (e.g. Strava ->
    Garmin), the new source should only fetch activities newer than the last
    entry the previous source recorded, rather than backfilling its full
    history. Returns 0 when merging is disabled or no other source has data.
    """
    sync_cfg = config.get("sync", {}) or {}
    if not bool(sync_cfg.get("merge_sources", False)):
        return 0
    floor = 0
    for source in SUPPORTED_SYNC_SOURCES:
        if source == current_source:
            continue
        floor = max(floor, _newest_seen_ts_for_source(source, data_dir))
        floor = max(floor, _newest_activity_ts_for_source(source, data_dir))
    return floor


def resolve_after_ts(current_source: str, config: Dict[str, Any], data_dir: str = "data") -> int:
    """Lower bound for a sync. An explicit config start_date/lookback wins;
    otherwise fall back to the cross-source merge floor (0 = fetch all)."""
    configured = start_after_ts(config)
    if configured:
        return configured
    return cross_source_after_floor(current_source, config, data_dir)


def activity_scope_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    activities_cfg = config.get("activities", {}) or {}
    include_all_types = bool(activities_cfg.get("include_all_types", True))
    exclude_types = sorted({str(item) for item in (activities_cfg.get("exclude_types", []) or [])})
    scope: Dict[str, Any] = {
        "include_all_types": include_all_types,
        "exclude_types": exclude_types,
    }
    if include_all_types:
        return scope

    featured_types = sorted({str(item) for item in featured_types_from_config(activities_cfg)})
    type_aliases = {
        str(source): str(target)
        for source, target in (activities_cfg.get("type_aliases", {}) or {}).items()
    }
    group_aliases = {
        str(source): str(target)
        for source, target in (activities_cfg.get("group_aliases", {}) or {}).items()
    }
    scope.update(
        {
            "featured_types": featured_types,
            "group_other_types": bool(activities_cfg.get("group_other_types", True)),
            "other_bucket": str(activities_cfg.get("other_bucket", "OtherSports")),
            "type_aliases": dict(sorted(type_aliases.items())),
            "group_aliases": dict(sorted(group_aliases.items())),
        }
    )
    return scope


def activity_start_ts(activity: Dict[str, Any]) -> Optional[int]:
    value = activity.get("start_date") or activity.get("start_date_local")
    if not value:
        return None
    value_str = str(value)
    if value_str.endswith("Z"):
        value_str = value_str[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(value_str).timestamp())
    except ValueError:
        return None

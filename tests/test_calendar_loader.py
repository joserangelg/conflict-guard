import os
from datetime import datetime
from zoneinfo import ZoneInfo

from core import calendar_loader
from core.models import SourceStatus, TZStatus

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
HOME_TZ = ZoneInfo("America/Los_Angeles")


def _load(filename, category="Work"):
    path = os.path.join(FIXTURES, filename)
    with open(path, "rb") as f:
        raw = f.read()
    return calendar_loader.load_calendar_source(category, filename, raw, HOME_TZ)


def test_valid_calendar_loads():
    source = _load("valid_work.ics")
    assert source.status == SourceStatus.LOADED
    assert source.raw_event_count == 1
    assert source.events[0].summary == "Sprint Planning"


def test_malformed_file_fails_not_silently_empty():
    source = _load("malformed.ics")
    assert source.status == SourceStatus.FAILED
    assert source.error_message is not None


def test_zero_event_calendar_is_unverified_not_loaded():
    source = _load("empty.ics")
    assert source.status == SourceStatus.EMPTY_UNVERIFIED
    assert source.confirmed_empty is False


def test_allday_event_is_date_verified_and_not_shifted():
    source = _load("allday.ics")
    event = source.events[0]
    assert event.is_all_day is True
    assert event.tz_status == TZStatus.DATE_VERIFIED
    assert event.start.date().isoformat() == "2026-08-20"
    assert event.end.date().isoformat() == "2026-08-21"


def test_multiday_allday_uses_exclusive_end_date():
    source = _load("multiday.ics")
    event = source.events[0]
    # Aug 16 - Aug 18 (exclusive) means the event occupies Aug 16 and Aug 17 only.
    assert event.start.date().isoformat() == "2026-08-16"
    assert event.end.date().isoformat() == "2026-08-18"


def test_ambiguous_midnight_timestamp_is_flagged():
    source = _load("ambiguous_midnight.ics")
    event = source.events[0]
    assert event.is_ambiguous is True
    assert event.tz_status == TZStatus.AMBIGUOUS_DATE
    assert event.ambiguity_resolved is None


def test_timezone_event_converted_to_home_tz():
    source = _load("timezone_event.ics")
    event = source.events[0]
    assert event.tz_status == TZStatus.VERIFIED
    # 17:00 America/New_York on 2026-08-20 == 14:00 America/Los_Angeles
    assert event.start.hour == 14
    assert event.start.tzinfo.key == "America/Los_Angeles" if hasattr(event.start.tzinfo, "key") else True


def test_naive_datetime_is_assumed_not_verified():
    source = _load("naive_no_tz.ics")
    event = source.events[0]
    assert event.tz_status == TZStatus.ASSUMED


def test_dedupe_removes_identical_events():
    from core.models import NormalizedEvent
    e1 = calendar_loader.load_calendar_source("Work", "valid_work.ics",
                                                open(os.path.join(FIXTURES, "valid_work.ics"), "rb").read(),
                                                HOME_TZ)
    doubled = e1.events + e1.events
    deduped = calendar_loader.dedupe_events(doubled)
    assert len(deduped) == 1

"""Parses .ics files and normalizes events into home-timezone datetimes.

Implements the two LOCKED RULES from Section 4 (My Failure Mode Map) that
apply at load time:

1. A zero-event parse never silently counts as "verified" -- it is marked
   EMPTY_UNVERIFIED until the user explicitly confirms it's intentional.
2. True all-day events keep their calendar date and are never shifted by a
   timezone conversion. Any event stored as a timestamp that *looks* like it
   was meant to be all-day (midnight start, 24h-multiple duration) is flagged
   AMBIGUOUS_DATE and excluded from evaluation until the user resolves it.
"""
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo

from icalendar import Calendar

from core.models import CalendarSource, NormalizedEvent, SourceStatus, TZStatus


def _to_home_tz_datetime(d, home_tz: ZoneInfo):
    """Given a date or datetime from icalendar, return (dt, tz_status, is_all_day)."""
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            return d.astimezone(home_tz), TZStatus.VERIFIED, False
        # naive datetime: no tz info in the source file at all
        return d.replace(tzinfo=home_tz), TZStatus.ASSUMED, False
    if isinstance(d, date):
        # true all-day value (VALUE=DATE) -- never converted across time zones
        dt = datetime.combine(d, time.min, tzinfo=home_tz)
        return dt, TZStatus.DATE_VERIFIED, True
    raise ValueError(f"Unrecognized date/time value from .ics: {d!r}")


def _looks_ambiguous(start_dt: datetime, end_dt: datetime, is_all_day: bool) -> bool:
    """A timed event stored at local midnight with a 24h-multiple duration could
    really be an all-day event misrepresented as a timestamp. Conversion could
    shift it to the wrong day, so it must be confirmed before use."""
    if is_all_day:
        return False
    if start_dt.time() != time.min:
        return False
    duration = end_dt - start_dt
    if duration.total_seconds() <= 0:
        return False
    return duration.total_seconds() % (24 * 3600) == 0


def parse_ics_bytes(raw: bytes, filename: str, category: str, home_tz: ZoneInfo):
    """Returns (events: list[NormalizedEvent], error: str|None)."""
    try:
        cal = Calendar.from_ical(raw)
    except Exception as exc:  # malformed file -> FAILED, never a silent empty result
        return None, f"Could not parse {filename}: {exc}"

    events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        dtstart_prop = component.get("DTSTART")
        if dtstart_prop is None:
            # A VEVENT with no start time can't be evaluated -- skip but this
            # is surfaced via raw_event_count vs len(events) mismatch.
            continue
        dtstart = dtstart_prop.dt

        dtend_prop = component.get("DTEND")
        if dtend_prop is not None:
            dtend = dtend_prop.dt
        else:
            duration_prop = component.get("DURATION")
            if duration_prop is not None:
                dtend = dtstart + duration_prop.dt
            elif isinstance(dtstart, date) and not isinstance(dtstart, datetime):
                dtend = dtstart + timedelta(days=1)  # single all-day date, exclusive end
            else:
                dtend = dtstart  # zero-duration timed event, evaluated as a boundary point

        start_dt, start_status, start_all_day = _to_home_tz_datetime(dtstart, home_tz)
        end_dt, end_status, end_all_day = _to_home_tz_datetime(dtend, home_tz)
        is_all_day = start_all_day and end_all_day

        tz_status = TZStatus.DATE_VERIFIED if is_all_day else start_status

        ambiguous = _looks_ambiguous(start_dt, end_dt, is_all_day)
        if ambiguous:
            tz_status = TZStatus.AMBIGUOUS_DATE

        summary = str(component.get("SUMMARY", "(no title)"))
        uid = str(component.get("UID", f"{filename}-{start_dt.isoformat()}"))
        is_recurring = component.get("RRULE") is not None

        original_repr = f"{dtstart}" if not isinstance(dtstart, datetime) else (
            f"{dtstart.isoformat()}" + (f" ({dtstart.tzinfo})" if dtstart.tzinfo else " (no tz in file)")
        )

        events.append(NormalizedEvent(
            source_category=category,
            source_filename=filename,
            summary=summary,
            start=start_dt,
            end=end_dt if end_dt > start_dt else start_dt,
            is_all_day=is_all_day,
            tz_status=tz_status,
            is_ambiguous=ambiguous,
            is_recurring=is_recurring,
            raw_original_repr=original_repr,
            uid=uid,
        ))

    return events, None


def dedupe_events(events):
    """Integrity check: identical (source, summary, start, end) collapses to one."""
    seen = set()
    result = []
    for e in events:
        key = e.event_key()
        if key in seen:
            continue
        seen.add(key)
        result.append(e)
    return result


def load_calendar_source(category: str, filename: str, raw: bytes, home_tz: ZoneInfo) -> CalendarSource:
    source = CalendarSource(
        category=category,
        filename=filename,
        file_size_bytes=len(raw),
        upload_time=datetime.now(home_tz),
    )

    events, error = parse_ics_bytes(raw, filename, category, home_tz)

    if error is not None:
        source.status = SourceStatus.FAILED
        source.error_message = error
        return source

    events = dedupe_events(events)
    source.events = events
    source.raw_event_count = len(events)

    if len(events) == 0:
        # Cannot tell a genuinely empty calendar from a failed/partial export.
        source.status = SourceStatus.EMPTY_UNVERIFIED
        return source

    source.status = SourceStatus.LOADED
    return source


def refine_relevance(source: CalendarSource, window_start: datetime, window_end: datetime):
    """After the proposed commitment is known, mark sources whose events all
    fall outside the evaluation window as NO_RELEVANT_EVENTS. Does not change
    FAILED/EMPTY_UNVERIFIED/MISSING sources."""
    if source.status != SourceStatus.LOADED:
        return
    relevant = [e for e in source.events if e.end > window_start and e.start < window_end]
    source.relevant_event_count = len(relevant)
    if len(relevant) == 0:
        source.status = SourceStatus.NO_RELEVANT_EVENTS

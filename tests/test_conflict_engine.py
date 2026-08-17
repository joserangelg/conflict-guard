from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import conflict_engine
from core.models import (
    CalendarSource, Classification, FinalResult, NormalizedEvent,
    ProposedCommitment, SourceStatus, TZStatus,
)

TZ = ZoneInfo("America/Los_Angeles")


def commitment(start_h, start_m, end_h, end_m, day=20, category="Personal"):
    d = datetime(2026, 8, day, tzinfo=TZ)
    return ProposedCommitment(
        title="Test commitment",
        category=category,
        start=d.replace(hour=start_h, minute=start_m),
        end=d.replace(hour=end_h, minute=end_m),
    )


def event(start_h, start_m, end_h, end_m, day=20, category="Work", summary="Existing event", **kw):
    d = datetime(2026, 8, day, tzinfo=TZ)
    return NormalizedEvent(
        source_category=category,
        source_filename="test.ics",
        summary=summary,
        start=d.replace(hour=start_h, minute=start_m),
        end=d.replace(hour=end_h, minute=end_m),
        is_all_day=False,
        tz_status=TZStatus.VERIFIED,
        uid=summary,
        **kw,
    )


def loaded_source(category, events):
    s = CalendarSource(category=category, filename="test.ics", status=SourceStatus.LOADED)
    s.events = events
    s.raw_event_count = len(events)
    return s


def all_sources(work_events=None, school_events=None, personal_events=None):
    return [
        loaded_source("Work", work_events or []),
        loaded_source("School", school_events or []),
        loaded_source("Personal", personal_events or []),
    ]


# --------------------------------------------------------- classification

def test_hard_conflict_direct_overlap():
    c = commitment(18, 0, 19, 0)
    e = event(18, 30, 19, 30)
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]))
    assert result.final_result == FinalResult.CONFLICT
    assert result.evidence[0].classification == Classification.HARD_CONFLICT
    assert result.evidence[0].overlap_minutes == 30


def test_soft_conflict_within_buffer():
    c = commitment(18, 0, 19, 0)
    e = event(19, 15, 20, 0)  # 15 min gap, under default 30 min buffer
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]), buffer_minutes=30)
    assert result.final_result == FinalResult.SOFT_CONFLICT
    assert result.evidence[0].classification == Classification.SOFT_CONFLICT
    assert result.evidence[0].buffer_gap_minutes == 15


def test_boundary_back_to_back():
    c = commitment(18, 0, 19, 0)
    e = event(19, 0, 20, 0)  # zero gap
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]))
    assert result.evidence[0].classification == Classification.BOUNDARY
    assert result.final_result == FinalResult.SOFT_CONFLICT


def test_no_conflict_clears_buffer():
    c = commitment(18, 0, 19, 0)
    e = event(20, 0, 21, 0)  # 60 min gap, clears default 30 min buffer
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]))
    assert result.final_result == FinalResult.AVAILABLE
    assert result.evidence == []


def test_buffer_disabled_ignores_near_miss():
    c = commitment(18, 0, 19, 0)
    e = event(19, 10, 20, 0)  # would be soft conflict at 30 min buffer
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]), buffer_minutes=None)
    assert result.final_result == FinalResult.AVAILABLE


def test_multiple_calendars_all_checked():
    c = commitment(18, 0, 19, 0)
    work_e = event(18, 30, 19, 30, category="Work", summary="work-overlap")
    school_e = event(20, 0, 21, 0, category="School", summary="school-clear")
    result = conflict_engine.evaluate(c, all_sources(work_events=[work_e], school_events=[school_e]))
    assert result.final_result == FinalResult.CONFLICT
    assert len(result.evidence) == 1  # school event cleared the buffer, not evidence-worthy


# --------------------------------------------------------- integrity / locked rules

def test_missing_required_source_blocks_available():
    c = commitment(18, 0, 19, 0)
    sources = [
        loaded_source("Work", []),
        CalendarSource(category="School", status=SourceStatus.MISSING),
        loaded_source("Personal", []),
    ]
    sources[0].status = SourceStatus.NO_RELEVANT_EVENTS
    sources[2].status = SourceStatus.NO_RELEVANT_EVENTS
    result = conflict_engine.evaluate(c, sources)
    assert result.final_result == FinalResult.REQUIRES_REVIEW
    assert any("School" in r for r in result.blocking_reasons)


def test_failed_source_blocks_available():
    c = commitment(18, 0, 19, 0)
    sources = all_sources()
    sources[0].status = SourceStatus.FAILED
    result = conflict_engine.evaluate(c, sources)
    assert result.final_result == FinalResult.REQUIRES_REVIEW


def test_unconfirmed_empty_source_blocks_available():
    c = commitment(18, 0, 19, 0)
    sources = all_sources()
    sources[0].status = SourceStatus.EMPTY_UNVERIFIED
    sources[0].confirmed_empty = False
    result = conflict_engine.evaluate(c, sources)
    assert result.final_result == FinalResult.REQUIRES_REVIEW


def test_confirmed_empty_source_does_not_block():
    c = commitment(18, 0, 19, 0)
    sources = all_sources()
    sources[0].status = SourceStatus.EMPTY_UNVERIFIED
    sources[0].confirmed_empty = True
    result = conflict_engine.evaluate(c, sources)
    assert result.final_result == FinalResult.AVAILABLE


def test_unresolved_ambiguous_event_blocks_available():
    c = commitment(18, 0, 19, 0)
    e = event(0, 0, 0, 0, category="Work", summary="ambiguous")
    e.is_ambiguous = True
    e.tz_status = TZStatus.AMBIGUOUS_DATE
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]))
    assert result.final_result == FinalResult.REQUIRES_REVIEW
    assert result.blocking_reasons


def test_resolved_ambiguous_event_is_evaluated_normally():
    c = commitment(18, 0, 19, 0)
    e = event(18, 30, 19, 30, category="Work", summary="resolved")
    e.is_ambiguous = True
    e.ambiguity_resolved = "timed"
    result = conflict_engine.evaluate(c, all_sources(work_events=[e]))
    assert result.final_result == FinalResult.CONFLICT


def test_no_relevant_event_never_shown_as_evidence_noise():
    c = commitment(18, 0, 19, 0)
    far_event = event(6, 0, 7, 0, day=25, category="Work", summary="far-away")
    result = conflict_engine.evaluate(c, all_sources(work_events=[far_event]))
    assert result.final_result == FinalResult.AVAILABLE
    assert result.evidence == []

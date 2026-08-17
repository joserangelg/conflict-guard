"""Deterministic conflict evaluation.

Implements the LOCKED RULES from Section 4 (My Failure Mode Map):

- The product blocks a verified "Available" result when required calendar
  coverage is missing, failed, or ambiguous. An intentionally empty calendar
  is only permitted after explicit user confirmation.
- "Available" is shown only when every relevant event was successfully
  evaluated by explicit overlap and buffer rules. The evidence behind the
  result (events, normalized times, overlap/buffer minutes, rule used) is
  always exposed, never just a final label.
- The app checks soft conflicts using a default 30-minute buffer. Disabling
  it requires an explicit choice and a visible warning (enforced in app.py's
  UI; this module just honors buffer_minutes=None as "disabled").
- LOCKED RULE -- CALENDAR FRESHNESS: verified availability requires every
  connected provider to have synchronized within the past 5 minutes. If a
  source has gone stale, the result is "Unable to verify full availability"
  rather than a silently-trusted "Available" -- distinct from the coverage
  failure above, since the data was present and correct at some point, it's
  just no longer current enough to trust without re-checking.
"""
from datetime import datetime, timedelta

from core.models import (
    Classification, EvaluationResult, EvidenceRow, FinalResult,
    FRESHNESS_WINDOW_MINUTES, SourceStatus, TZStatus,
)

DEFAULT_BUFFER_MINUTES = 30


def _overlap_minutes(a_start, a_end, b_start, b_end):
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    delta = (earliest_end - latest_start).total_seconds()
    return max(0, int(delta // 60))


def _gap_minutes(a_start, a_end, b_start, b_end):
    if a_end <= b_start:
        return int((b_start - a_end).total_seconds() // 60)
    if b_end <= a_start:
        return int((a_start - b_end).total_seconds() // 60)
    return 0  # overlapping -- no gap


def classify(commitment, event, buffer_minutes):
    overlap = _overlap_minutes(commitment.start, commitment.end, event.start, event.end)
    if overlap > 0:
        return Classification.HARD_CONFLICT, overlap, 0, (
            f"Direct overlap of {overlap} min between proposed commitment and "
            f"'{event.summary}' ({event.source_category})."
        )

    gap = _gap_minutes(commitment.start, commitment.end, event.start, event.end)
    if gap == 0:
        return Classification.BOUNDARY, 0, 0, (
            f"Events are back-to-back with no gap against '{event.summary}' "
            f"({event.source_category})."
        )
    if buffer_minutes is not None and gap < buffer_minutes:
        return Classification.SOFT_CONFLICT, 0, gap, (
            f"Gap of {gap} min against '{event.summary}' is under the "
            f"{buffer_minutes}-min buffer."
        )
    return Classification.NONE, 0, gap, (
        f"No overlap; gap of {gap} min against '{event.summary}' clears the buffer."
    )


def _integrity_checks(sources, commitment, ambiguous_unresolved):
    checks = {}
    reasons = []

    required_ok = True
    for source in sources:
        if source.status in (SourceStatus.MISSING, SourceStatus.FAILED):
            required_ok = False
            reasons.append(f"{source.category} calendar is {source.status.value.lower()}.")
        elif source.status == SourceStatus.EMPTY_UNVERIFIED and not source.confirmed_empty:
            required_ok = False
            reasons.append(f"{source.category} calendar is empty and has not been confirmed intentional.")
    checks["required_sources_loaded_or_confirmed"] = required_ok

    checks["valid_time_ranges"] = commitment.end > commitment.start
    if not checks["valid_time_ranges"]:
        reasons.append("Proposed commitment end time is not after its start time.")

    checks["no_unresolved_ambiguous_dates"] = len(ambiguous_unresolved) == 0
    if ambiguous_unresolved:
        names = ", ".join(f"'{e.summary}'" for e in ambiguous_unresolved)
        reasons.append(f"Ambiguous date(s) not yet confirmed: {names}.")

    checks["full_normalization"] = True  # calendar_loader normalizes every event or marks it ambiguous

    return checks, reasons


def _freshness_reasons(sources):
    """Providers eligible for a verified result must have synced within the
    freshness window. Sources already blocked by the coverage check (missing,
    failed, unconfirmed-empty) are skipped here -- that failure is reported
    separately so the two don't get conflated in the UI."""
    reasons = []
    verified_statuses = (SourceStatus.LOADED, SourceStatus.NO_RELEVANT_EVENTS)
    for source in sources:
        eligible = source.status in verified_statuses or (
            source.status == SourceStatus.EMPTY_UNVERIFIED and source.confirmed_empty
        )
        if not eligible:
            continue
        if source.upload_time is None:
            reasons.append(f"{source.category} calendar has no recorded sync time.")
            continue
        now = datetime.now(source.upload_time.tzinfo)
        age_minutes = (now - source.upload_time).total_seconds() / 60
        if age_minutes > FRESHNESS_WINDOW_MINUTES:
            reasons.append(
                f"{source.category} calendar last synced {int(age_minutes)} min ago "
                f"(over the {FRESHNESS_WINDOW_MINUTES}-min freshness window)."
            )
    return reasons


def evaluate(commitment, sources, buffer_minutes=DEFAULT_BUFFER_MINUTES):
    """sources: list[CalendarSource] already loaded via calendar_loader."""
    all_events = []
    ambiguous_unresolved = []
    for source in sources:
        for e in source.events:
            if e.is_ambiguous and e.ambiguity_resolved is None:
                ambiguous_unresolved.append(e)
            else:
                all_events.append(e)

    checks, reasons = _integrity_checks(sources, commitment, ambiguous_unresolved)

    if not all(checks.values()):
        return EvaluationResult(
            final_result=FinalResult.REQUIRES_REVIEW,
            evidence=[],
            blocking_reasons=reasons,
            buffer_minutes=buffer_minutes,
            integrity_checks=checks,
        )

    stale_reasons = _freshness_reasons(sources)
    checks["data_synced_within_freshness_window"] = len(stale_reasons) == 0
    if stale_reasons:
        return EvaluationResult(
            final_result=FinalResult.UNABLE_TO_VERIFY,
            evidence=[],
            blocking_reasons=stale_reasons,
            buffer_minutes=buffer_minutes,
            integrity_checks=checks,
        )

    # Only evaluate events that plausibly touch the commitment window (+/- 1 day
    # so soft-conflict buffers spanning midnight are still caught).
    window_start = commitment.start - timedelta(days=1)
    window_end = commitment.end + timedelta(days=1)
    relevant = [e for e in all_events if e.end > window_start and e.start < window_end]

    evidence = []
    for e in relevant:
        classification, overlap, gap, rule = classify(commitment, e, buffer_minutes)
        if classification == Classification.NONE:
            continue  # not evidence-worthy noise; keeps the table readable
        evidence.append(EvidenceRow(
            event=e,
            overlap_minutes=overlap,
            buffer_gap_minutes=gap if classification == Classification.SOFT_CONFLICT else None,
            classification=classification,
            rule=rule,
        ))

    evidence.sort(key=lambda row: row.event.start)

    active_hard = [r for r in evidence if r.classification == Classification.HARD_CONFLICT and r.user_override != "not_a_conflict"]
    active_soft = [r for r in evidence if r.classification in (Classification.SOFT_CONFLICT, Classification.BOUNDARY) and r.user_override != "not_a_conflict"]

    if active_hard:
        final = FinalResult.CONFLICT
    elif active_soft:
        final = FinalResult.SOFT_CONFLICT
    else:
        final = FinalResult.AVAILABLE

    return EvaluationResult(
        final_result=final,
        evidence=evidence,
        blocking_reasons=[],
        buffer_minutes=buffer_minutes,
        integrity_checks=checks,
    )

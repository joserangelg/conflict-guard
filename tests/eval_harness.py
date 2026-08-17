"""A runnable, scaled-down version of the eval plan from Section 4 (A3) of
the IMPACT living document. The full plan calls for 80 labeled scenarios
across four measures; this harness implements the same methodology (labels
set before running the app, scored against a target and a minimum bar) with
a smaller representative set so it's practical to hand-build and review by
hand. Extend SCENARIOS to grow toward the full 80-case suite.

Run directly:  python -m tests.eval_harness
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from core import conflict_engine
from core.models import CalendarSource, FinalResult, NormalizedEvent, ProposedCommitment, SourceStatus, TZStatus

TZ = ZoneInfo("America/Los_Angeles")


def _dt(day, h, m):
    return datetime(2026, 8, day, h, m, tzinfo=TZ)


def _commit(day, sh, sm, eh, em):
    return ProposedCommitment("Scenario", "Personal", _dt(day, sh, sm), _dt(day, eh, em))


def _event(day, sh, sm, eh, em, category="Work", uid="e"):
    return NormalizedEvent(
        source_category=category, source_filename="eval.ics", summary=uid,
        start=_dt(day, sh, sm), end=_dt(day, eh, em),
        is_all_day=False, tz_status=TZStatus.VERIFIED, uid=uid,
    )


def _sources(events_by_cat, statuses=None):
    statuses = statuses or {}
    out = []
    for cat in ("Work", "School", "Personal"):
        s = CalendarSource(category=cat, filename="eval.ics",
                            status=statuses.get(cat, SourceStatus.LOADED))
        s.events = events_by_cat.get(cat, [])
        s.confirmed_empty = statuses.get(f"{cat}_confirmed", False)
        out.append(s)
    return out


# Each scenario: (name, commitment, sources, expected_final_result)
HARD_CONFLICT_SCENARIOS = [
    ("partial_overlap", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 18, 30, 19, 30)]}), FinalResult.CONFLICT),
    ("complete_overlap", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 17, 0, 20, 0)]}), FinalResult.CONFLICT),
    ("exact_boundary_match", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 18, 0, 19, 0)]}), FinalResult.CONFLICT),
    ("proposed_inside_existing", _commit(20, 18, 15, 18, 45), _sources({"School": [_event(20, 18, 0, 19, 0, category="School")]}), FinalResult.CONFLICT),
    ("overnight_span", _commit(20, 23, 0, 23, 59), _sources({"Personal": [_event(20, 22, 30, 23, 30, category="Personal")]}), FinalResult.CONFLICT),
]

SOFT_CONFLICT_SCENARIOS = [
    ("15min_gap_default_buffer", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 19, 15, 20, 0)]}), FinalResult.SOFT_CONFLICT),
    ("29min_gap_default_buffer", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 19, 29, 20, 0)]}), FinalResult.SOFT_CONFLICT),
    ("0min_gap_boundary", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 19, 0, 20, 0)]}), FinalResult.SOFT_CONFLICT),
    ("gap_before_commitment", _commit(20, 18, 0, 19, 0), _sources({"School": [_event(20, 17, 0, 17, 45, category="School")]}), FinalResult.SOFT_CONFLICT),
    ("31min_gap_clears_buffer", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 19, 31, 20, 0)]}), FinalResult.AVAILABLE),
]

FALSE_CLEAR_SCENARIOS = [  # required-source coverage issues -- must never show Available
    ("missing_school", _commit(20, 18, 0, 19, 0), _sources({}, statuses={"School": SourceStatus.MISSING}), FinalResult.REQUIRES_REVIEW),
    ("failed_work", _commit(20, 18, 0, 19, 0), _sources({}, statuses={"Work": SourceStatus.FAILED}), FinalResult.REQUIRES_REVIEW),
    ("unconfirmed_empty_personal", _commit(20, 18, 0, 19, 0), _sources({}, statuses={"Personal": SourceStatus.EMPTY_UNVERIFIED}), FinalResult.REQUIRES_REVIEW),
    ("confirmed_empty_is_fine", _commit(20, 18, 0, 19, 0), _sources({}, statuses={"Personal": SourceStatus.EMPTY_UNVERIFIED, "Personal_confirmed": True}), FinalResult.AVAILABLE),
]

FALSE_POSITIVE_SCENARIOS = [  # genuine no-conflict cases -- must never warn
    ("far_apart_same_day", _commit(20, 18, 0, 19, 0), _sources({"Work": [_event(20, 8, 0, 9, 0)]}), FinalResult.AVAILABLE),
    ("different_day", _commit(20, 18, 0, 19, 0), _sources({"School": [_event(21, 18, 0, 19, 0, category="School")]}), FinalResult.AVAILABLE),
    ("no_events_at_all", _commit(20, 18, 0, 19, 0), _sources({}), FinalResult.AVAILABLE),
]


def run_group(name, scenarios):
    passed = 0
    failures = []
    for label, commitment, sources, expected in scenarios:
        result = conflict_engine.evaluate(commitment, sources)
        ok = result.final_result == expected
        passed += ok
        if not ok:
            failures.append((label, expected, result.final_result))
    total = len(scenarios)
    print(f"{name}: {passed}/{total} correct" + (f" -- FAILURES: {failures}" if failures else ""))
    return passed, total


def main():
    print("ConflictGuard eval harness (scaled-down version of Section 4 A3)\n")
    hard_p, hard_t = run_group("Hard-conflict recall", HARD_CONFLICT_SCENARIOS)
    soft_p, soft_t = run_group("Soft-conflict accuracy", SOFT_CONFLICT_SCENARIOS)
    fc_p, fc_t = run_group("False-clear rate (coverage gating)", FALSE_CLEAR_SCENARIOS)
    fp_p, fp_t = run_group("False-positive rate", FALSE_POSITIVE_SCENARIOS)
    print(f"\nHard-conflict recall:  {hard_p}/{hard_t} (target 100%, minimum bar 95%)")
    print(f"Soft-conflict accuracy: {soft_p}/{soft_t} (target 100%, minimum bar 90%)")
    print(f"False-clear rate:      {fc_p}/{fc_t} correct (target 0% false-clears)")
    print(f"False positives:       {fp_p}/{fp_t} correct (target 0 false warnings)")


if __name__ == "__main__":
    main()

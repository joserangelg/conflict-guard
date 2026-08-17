"""ConflictGuard -- a local Streamlit prototype that catches hidden and soft
scheduling conflicts across Work / School / Personal calendars before the
user commits to something new.

Three-screen structure (see Section 2 of the IMPACT living document):
  1. Entry Point        -- upload calendars, enter the proposed commitment
  2. AI-Powered Moment   -- transparent conflict evidence, integrity checks
  3. Decision + Action   -- human decides; app drafts a reply and records
                            the outcome, but never sends or auto-modifies
                            anything (Section 5, tradeoff #2)
"""
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from core import calendar_loader, conflict_engine, storage
from core.models import (
    CATEGORIES, CalendarSource, Classification, FinalResult, ProposedCommitment,
    SourceStatus, TZStatus,
)

COMMON_TIMEZONES = [
    "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York",
    "America/Anchorage", "Pacific/Honolulu", "UTC", "Europe/London", "Europe/Paris",
    "Asia/Tokyo", "Australia/Sydney",
]

STATUS_COLOR = {
    SourceStatus.LOADED: "\U0001F7E2",
    SourceStatus.NO_RELEVANT_EVENTS: "\U0001F7E2",
    SourceStatus.EMPTY_UNVERIFIED: "\U0001F7E1",
    SourceStatus.FAILED: "\U0001F534",
    SourceStatus.MISSING: "⚪",
}

RESULT_STYLE = {
    FinalResult.AVAILABLE: ("success", "Available"),
    FinalResult.CONFLICT: ("error", "Conflict"),
    FinalResult.SOFT_CONFLICT: ("warning", "Soft conflict warning"),
    FinalResult.REQUIRES_REVIEW: ("info", "Availability requires review"),
}


def init_state():
    defaults = {
        "step": 1,
        "home_tz_name": "America/Los_Angeles",
        "required": {c: True for c in CATEGORIES},
        "sources": {c: CalendarSource(category=c) for c in CATEGORIES},
        "buffer_choice": "30 min (default)",
        "buffer_disabled_confirmed": False,
        "commitment_title": "",
        "commitment_category": CATEGORIES[2],
        "commitment_date": date.today(),
        "commitment_start": time(18, 0),
        "commitment_end": time(19, 0),
        "commitment": None,
        "eval_result": None,
        "overridden_uids": set(),
        "disputed_result": False,
        "followup_shown": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def home_tz() -> ZoneInfo:
    return ZoneInfo(st.session_state.home_tz_name)


def buffer_minutes():
    mapping = {
        "15 min": 15,
        "30 min (default)": 30,
        "60 min": 60,
        "Off (not recommended)": None,
    }
    return mapping[st.session_state.buffer_choice]


def render_passive_followup():
    pending = storage.get_pending_followup()
    if pending is None or st.session_state.followup_shown:
        return
    with st.sidebar:
        st.markdown("### Quick check-in")
        st.write(
            f"Last time, the app showed **{pending['final_result']}** "
            f"based on {pending['evidence_count']} piece(s) of evidence. "
            "Was that result correct?"
        )
        c1, c2 = st.columns(2)
        if c1.button("Yes, correct"):
            storage.answer_pending_followup(True)
            st.session_state.followup_shown = True
            st.rerun()
        if c2.button("No, it was wrong"):
            storage.answer_pending_followup(False)
            st.session_state.followup_shown = True
            st.rerun()


def render_sidebar_progress():
    labels = {1: "1. Entry Point", 2: "2. AI-Powered Moment", 3: "3. Decision + Action"}
    st.sidebar.markdown("### ConflictGuard")
    for i in (1, 2, 3):
        prefix = "**▶ " if i == st.session_state.step else "   "
        suffix = "**" if i == st.session_state.step else ""
        st.sidebar.markdown(f"{prefix}{labels[i]}{suffix}")
    st.sidebar.divider()


# ---------------------------------------------------------------- SCREEN 1
def screen_entry_point():
    st.title("1. Entry Point")
    st.caption("Upload the calendars you want checked, then describe the commitment you're considering.")

    st.subheader("Home time zone")
    st.selectbox(
        "All events are normalized into this time zone for comparison. Original times are always preserved.",
        COMMON_TIMEZONES,
        key="home_tz_name",
    )

    st.subheader("Calendar sources")
    st.caption(
        "Check the sources that are required for this decision. A required source must load "
        "successfully (or be confirmed empty) before the app will show a verified result."
    )

    for cat in CATEGORIES:
        with st.container(border=True):
            c1, c2 = st.columns([1, 3])
            with c1:
                st.checkbox(f"{cat} required", value=st.session_state.required[cat], key=f"required_{cat}")
                st.session_state.required[cat] = st.session_state[f"required_{cat}"]
            with c2:
                uploaded = st.file_uploader(f"{cat} calendar (.ics)", type=["ics"], key=f"upload_{cat}")
                if uploaded is not None:
                    raw = uploaded.getvalue()
                    if (st.session_state.sources[cat].filename != uploaded.name
                            or st.session_state.sources[cat].file_size_bytes != len(raw)):
                        st.session_state.sources[cat] = calendar_loader.load_calendar_source(
                            cat, uploaded.name, raw, home_tz()
                        )

            source = st.session_state.sources[cat]
            if not st.session_state.required[cat] and source.status == SourceStatus.MISSING:
                st.caption("Not required -- skipped.")
                continue

            badge = STATUS_COLOR[source.status]
            st.markdown(f"{badge} **Status: {source.status.value}**")

            if source.status == SourceStatus.FAILED:
                st.error(f"This file could not be parsed: {source.error_message}")

            if source.status == SourceStatus.EMPTY_UNVERIFIED:
                st.warning(
                    f"'{source.filename}' parsed successfully but contains **zero events**. "
                    "This could mean the calendar is genuinely empty, or that the export failed "
                    "or was truncated. The app cannot tell the difference on its own."
                )
                st.caption(
                    f"File: {source.filename} · {source.file_size_bytes} bytes · "
                    f"uploaded {source.upload_time.strftime('%Y-%m-%d %H:%M') if source.upload_time else ''}"
                )
                st.caption(
                    "⚠️ If you confirm this is intentional and it's actually wrong, the app "
                    "may show you as available when you are not."
                )
                confirmed = st.checkbox(
                    "I confirm this calendar is intentionally empty for this period.",
                    key=f"confirm_empty_{cat}",
                    value=source.confirmed_empty,
                )
                st.session_state.sources[cat].confirmed_empty = confirmed

            if source.status in (SourceStatus.LOADED, SourceStatus.NO_RELEVANT_EVENTS):
                st.caption(f"{source.raw_event_count} event(s) loaded from {source.filename}.")

    st.subheader("Soft-conflict buffer")
    st.radio(
        "Flag events that are close together even if they don't overlap.",
        ["15 min", "30 min (default)", "60 min", "Off (not recommended)"],
        key="buffer_choice",
    )
    if st.session_state.buffer_choice == "Off (not recommended)":
        st.warning("Soft-conflict protection is off. Only direct overlaps will be checked.")
        st.session_state.buffer_disabled_confirmed = st.checkbox(
            "I understand and want to disable soft-conflict checking.",
            value=st.session_state.buffer_disabled_confirmed,
        )

    st.subheader("Proposed commitment")
    st.text_input("What is it? (e.g. 'World Cup watch party')", key="commitment_title")
    st.selectbox("Category", CATEGORIES, key="commitment_category")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.date_input("Date", key="commitment_date")
    with c2:
        st.time_input("Start time", key="commitment_start")
    with c3:
        st.time_input("End time", key="commitment_end")

    st.divider()
    can_continue = True
    if st.session_state.buffer_choice == "Off (not recommended)" and not st.session_state.buffer_disabled_confirmed:
        can_continue = False
        st.info("Confirm disabling soft-conflict checking above, or choose a buffer, to continue.")

    if st.button("Continue to conflict check →", type="primary", disabled=not can_continue):
        start_dt = datetime.combine(st.session_state.commitment_date, st.session_state.commitment_start, tzinfo=home_tz())
        end_dt = datetime.combine(st.session_state.commitment_date, st.session_state.commitment_end, tzinfo=home_tz())
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)  # crosses midnight
        st.session_state.commitment = ProposedCommitment(
            title=st.session_state.commitment_title or "(untitled commitment)",
            category=st.session_state.commitment_category,
            start=start_dt,
            end=end_dt,
        )
        st.session_state.eval_result = None
        st.session_state.overridden_uids = set()
        st.session_state.disputed_result = False
        st.session_state.step = 2
        st.rerun()


# ---------------------------------------------------------------- SCREEN 2
def active_sources():
    return [s for cat, s in st.session_state.sources.items()
            if st.session_state.required[cat] or s.status != SourceStatus.MISSING]


def screen_ai_moment():
    st.title("2. AI-Powered Moment")
    commitment = st.session_state.commitment
    if commitment is None:
        st.warning("No commitment entered yet.")
        if st.button("← Back to Entry Point"):
            st.session_state.step = 1
            st.rerun()
        return

    st.caption(
        f"Checking **{commitment.title}** ({commitment.category}) — "
        f"{commitment.start.strftime('%a %b %d, %I:%M %p')} to {commitment.end.strftime('%I:%M %p %Z')}"
    )

    sources = active_sources()

    # Set relevance status now that we know the commitment window.
    window_start = commitment.start - timedelta(days=1)
    window_end = commitment.end + timedelta(days=1)
    for s in sources:
        calendar_loader.refine_relevance(s, window_start, window_end)

    with st.expander("Calendar source status", expanded=True):
        for s in sources:
            st.markdown(f"{STATUS_COLOR[s.status]} **{s.category}** — {s.status.value}"
                        + (f" ({s.filename})" if s.filename else ""))

    # Ambiguous-date resolution gate
    ambiguous = [e for s in sources for e in s.events if e.is_ambiguous and e.ambiguity_resolved is None]
    if ambiguous:
        st.subheader("Resolve ambiguous dates before continuing")
        st.caption(
            "These events are stored as an exact timestamp at midnight with a duration that's a "
            "multiple of 24 hours -- they could be all-day events misrepresented as timed ones. "
            "Converting them across time zones could shift them to the wrong day, so they're held "
            "back from evaluation until you confirm which they are."
        )
        for e in ambiguous:
            st.write(f"**{e.summary}** ({e.source_category}) — original: {e.raw_original_repr}")
            choice = st.radio(
                "Treat as:", ["All-day event", "Timed event at this exact time"],
                key=f"ambig_{e.uid}", horizontal=True, index=None,
            )
            if choice == "All-day event":
                e.ambiguity_resolved = "all_day"
                e.is_all_day = True
                e.tz_status = TZStatus.DATE_VERIFIED
            elif choice == "Timed event at this exact time":
                e.ambiguity_resolved = "timed"
                e.tz_status = TZStatus.ASSUMED

    result = conflict_engine.evaluate(commitment, sources, buffer_minutes())
    st.session_state.eval_result = result

    style, label = RESULT_STYLE[result.final_result]
    banner = st.error if style == "error" else st.warning if style == "warning" else st.info if style == "info" else st.success
    if st.session_state.disputed_result:
        st.warning("⚠️ User disputed this result -- shown for reference, not treated as ground truth.")
    banner(f"### {label}")

    if result.final_result == FinalResult.REQUIRES_REVIEW:
        st.write("This is blocking a verified result:")
        for reason in result.blocking_reasons:
            st.write(f"- {reason}")
        if st.button("← Back to Entry Point to fix"):
            st.session_state.step = 1
            st.rerun()
        return

    with st.expander("Integrity checks passed"):
        for name, passed in result.integrity_checks.items():
            st.write(f"{'✅' if passed else '❌'} {name.replace('_', ' ')}")

    st.subheader("Evidence")
    if not result.evidence:
        st.write("No nearby events were found within the evaluation window.")
    for i, row in enumerate(result.evidence):
        overridden = row.event.uid in st.session_state.overridden_uids
        with st.container(border=True):
            label = row.classification.value if not overridden else "Overridden — not a conflict"
            st.markdown(f"**{label}**" + (" ~~" if overridden else ""))
            st.write(f"Event: {row.event.summary} ({row.event.source_category})")
            st.write(
                f"Proposed: {commitment.start.strftime('%I:%M %p')}–{commitment.end.strftime('%I:%M %p')} · "
                f"Existing: {row.event.start.strftime('%a %I:%M %p')}–{row.event.end.strftime('%I:%M %p')} "
                f"({row.event.tz_status.value})"
            )
            st.write(f"Overlap: {row.overlap_minutes} min · Buffer gap: "
                     f"{row.buffer_gap_minutes if row.buffer_gap_minutes is not None else 'n/a'} min")
            st.caption(row.rule)
            if row.event.is_recurring:
                st.caption("⚠️ Recurring event: only the base occurrence was evaluated. "
                           "Daylight-saving transitions are not supported in v1.")
            if not overridden and st.button("This is not a conflict", key=f"override_{i}_{row.event.uid}"):
                st.session_state.overridden_uids.add(row.event.uid)
                storage.log_feedback(
                    kind="not_a_conflict_override",
                    classification_before=row.classification.value,
                    rule_used=row.rule,
                    overlap_minutes=row.overlap_minutes,
                    buffer_gap_minutes=row.buffer_gap_minutes,
                    source_category=row.event.source_category,
                    buffer_minutes=result.buffer_minutes,
                )
                st.rerun()

    # Re-derive final result after any per-row overrides
    active_hard = [r for r in result.evidence
                   if r.classification == Classification.HARD_CONFLICT
                   and r.event.uid not in st.session_state.overridden_uids]
    active_soft = [r for r in result.evidence
                   if r.classification in (Classification.SOFT_CONFLICT, Classification.BOUNDARY)
                   and r.event.uid not in st.session_state.overridden_uids]
    if active_hard:
        effective = FinalResult.CONFLICT
    elif active_soft:
        effective = FinalResult.SOFT_CONFLICT
    else:
        effective = FinalResult.AVAILABLE
    st.session_state.effective_result = effective

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("\U0001F6A9 This looks wrong", disabled=st.session_state.disputed_result):
            st.session_state.disputed_result = True
            storage.log_feedback(
                kind="disputed_result",
                classification_before=result.final_result.value,
                rule_used="overall_result",
                overlap_minutes=None,
                buffer_gap_minutes=None,
                source_category=None,
                buffer_minutes=result.buffer_minutes,
            )
            st.rerun()
    with c2:
        if st.button("Continue to decision →", type="primary"):
            storage.set_pending_followup(effective.value, len(result.evidence))
            st.session_state.step = 3
            st.rerun()

    if st.button("← Back to Entry Point"):
        st.session_state.step = 1
        st.rerun()


# ---------------------------------------------------------------- SCREEN 3
def draft_reply(decision, commitment, effective_result):
    if decision == "Accept":
        return (
            f"Yes, I'm in for {commitment.title.lower()}"
            f" on {commitment.start.strftime('%A, %b %d at %I:%M %p')}. See you there!"
        )
    if decision == "Decline":
        reason = "I have a scheduling conflict" if effective_result == FinalResult.CONFLICT else "I don't think I can make it work"
        return f"Thanks for the invite to {commitment.title.lower()} -- unfortunately {reason}, so I'll have to pass this time."
    return (
        f"I'd love to do {commitment.title.lower()}, but {commitment.start.strftime('%A, %b %d')} is tight for me. "
        "Could we look at another time?"
    )


def screen_decision():
    st.title("3. Decision + Action")
    commitment = st.session_state.commitment
    result = st.session_state.eval_result
    effective = st.session_state.get("effective_result", result.final_result if result else None)

    if commitment is None or result is None:
        st.warning("Run a conflict check first.")
        if st.button("← Back to Entry Point"):
            st.session_state.step = 1
            st.rerun()
        return

    style, label = RESULT_STYLE.get(effective, ("info", "Unknown"))
    st.write(f"**{commitment.title}** ({commitment.category}) — result: **{label}**")
    if st.session_state.disputed_result:
        st.caption("Note: you flagged the automated result as potentially wrong.")

    st.subheader("Your decision")
    decision = st.radio("What do you want to do?", ["Accept", "Decline", "Reschedule"], horizontal=True)

    st.subheader("Suggested reply (draft only -- nothing is sent automatically)")
    default_reply = draft_reply(decision, commitment, effective)
    reply_text = st.text_area("Edit before you send it yourself:", value=default_reply, height=100)
    st.caption("ConflictGuard never sends messages or modifies your calendars on its own -- you stay in control.")

    st.divider()
    if decision == "Accept":
        if st.button("Record this commitment", type="primary"):
            receipt = storage.record_commitment(
                commitment.title, commitment.category, commitment.start, commitment.end, decision
            )
            st.success("Recorded.")
            st.json(receipt)
    else:
        if st.button("Log this decision (not recorded as a calendar commitment)"):
            storage.record_commitment(
                commitment.title, commitment.category, commitment.start, commitment.end, decision
            )
            st.success(f"Logged as: {decision}")

    st.divider()
    if st.button("Start a new check"):
        st.session_state.step = 1
        st.session_state.commitment = None
        st.session_state.eval_result = None
        st.session_state.overridden_uids = set()
        st.session_state.disputed_result = False
        st.session_state.commitment_title = ""
        st.rerun()
    if st.button("← Back to AI-Powered Moment"):
        st.session_state.step = 2
        st.rerun()


def main():
    st.set_page_config(page_title="ConflictGuard", page_icon="\U0001F4C5", layout="centered")
    init_state()
    render_passive_followup()
    render_sidebar_progress()

    if st.session_state.step == 1:
        screen_entry_point()
    elif st.session_state.step == 2:
        screen_ai_moment()
    else:
        screen_decision()


if __name__ == "__main__":
    main()

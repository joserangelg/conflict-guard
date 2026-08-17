# ConflictGuard

A local Streamlit prototype that catches hidden and soft scheduling
conflicts across Work, School, and Personal calendars *before* they turn
into a double-booking — built for TMMBA 522 (AI Builder for Product
Managers) from the IMPACT living document (Sections 1–5).

## What it does

The target design (Section 5, updated) connects live to Google Calendar and
Microsoft Outlook via OAuth and Apple iCloud Calendar via CalDAV. **This
build simulates that connection** as an `.ics` upload per source (Work /
School / Personal) — same status model, same freshness rule, same UI
language ("Connected" / "Sync failed" / "Not connected") the live version
would use, just without an actual OAuth/CalDAV transport behind it yet. See
"Known v1 limitations" below for the honest line between what's real and
what's simulated.

You upload/"connect" up to three calendars, describe a commitment you're
considering, and ConflictGuard tells you whether you're actually free —
showing the evidence behind the answer instead of just a label, and always
leaving the accept/decline/reschedule decision to you.

Three screens, matching the mental model in Section 2:

1. **Entry Point** — upload calendars, pick a home time zone and
   soft-conflict buffer, describe the proposed commitment.
2. **AI-Powered Moment** — calendar coverage status, ambiguous-date
   resolution, and a transparent conflict evidence table (hard conflict /
   soft conflict / boundary / clear, with the overlap or buffer minutes and
   the rule that produced the classification).
3. **Decision + Action** — you decide accept / decline / reschedule; the
   app drafts a reply for you to review and send yourself, and records the
   outcome locally. It never sends messages or edits your calendars.

## Why it's built this way (Section 4 locked rules)

- **No silent false-clears.** A calendar source that's missing, failed to
  parse, or parsed with zero events blocks a verified "Available" result
  until you either fix it or explicitly confirm the calendar is
  intentionally empty.
- **No silent date shifts.** True all-day events keep their calendar date
  and are never converted across time zones. An event stored as a midnight
  timestamp with a 24-hour-multiple duration is flagged ambiguous and held
  out of evaluation until you confirm whether it's really all-day.
- **Every result shows its evidence.** "Available" only appears when every
  relevant event was evaluated by explicit overlap/buffer rules; the app
  always shows what it checked, not just the final label. You can flag "This
  looks wrong" or override a single event as "not a conflict" — both are
  logged as feedback for review, not treated as new ground truth.
- **Soft conflicts use a visible 30-minute default buffer** (15/60/off),
  and disabling it requires an explicit confirmation with a warning.
- **No stale-data false confidence.** Verified availability requires every
  connected source to have synced within the last 5 minutes (the upload
  moment stands in for "synced" in this build). If a source has gone stale,
  the result is "Unable to verify full availability," not a silently-trusted
  green check — with a "Re-verify calendars are current" action that makes
  the re-confirmation explicit rather than automatic.

See `core/calendar_loader.py` and `core/conflict_engine.py` for the LOCKED
RULE docstrings tying each behavior back to a specific failure mode.

## Known v1 limitations (by design — see Section 5)

- **Live OAuth/CalDAV is the target design, not yet built.** Real
  integrations (Google OAuth, Microsoft OAuth, Apple CalDAV +
  app-specific password, credentials in the OS Keychain) are the next
  concrete milestone — see "Live integration, next" below for what that
  would take. Today, an `.ics` upload stands in for "connecting" a source,
  and the upload timestamp stands in for "last synced."
- No informal-commitment detection from texts/DMs/email in v1.
- No real travel-time or fatigue estimation — just a user-selected buffer.
- Recurring events are evaluated at their base occurrence only; recurrence
  across daylight-saving transitions is unsupported and flagged, not
  silently assumed correct.
- "Don't warn me again" overrides are not remembered across checks — an
  override only applies to the current run, since a future edit to a
  recurring event could otherwise hide a real conflict.

## Live integration, next

Wiring up the real connections from Section 4/5 needs setup only the
project owner can do (registering OAuth apps), so it's scoped as follow-up
work rather than silently faked:

- **Google Calendar** — register an OAuth client in Google Cloud Console
  (Calendar API, read-only scope), use the installed-app / PKCE flow so no
  public redirect server is needed, store the refresh token in macOS
  Keychain via `keyring`.
- **Microsoft Outlook** — register an app in Microsoft Entra ID, use MSAL's
  public-client flow against Microsoft Graph, same local-only redirect
  approach.
- **Apple iCloud Calendar** — CalDAV over HTTPS using an app-specific
  password generated in Apple ID settings (no OAuth available); still
  iCalendar-formatted under the hood, so it keeps the same all-day/ambiguous-
  timestamp handling already in `core/calendar_loader.py`.
- Swap `calendar_loader.load_calendar_source` for provider-specific fetchers
  that return the same `CalendarSource`/`NormalizedEvent` shape — the
  freshness rule, status model, and conflict engine don't need to change.

## Running it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (typically `http://localhost:8501`).

Sample `.ics` files to try are in `tests/fixtures/` — e.g. upload
`valid_work.ics` as Work, `valid_school.ics` as School, and leave Personal
unconfirmed-empty to see the confirmation flow, or upload `malformed.ics`
to see a Failed source block the result.

## Tests

```bash
pip install -r requirements.txt
pytest
python -m tests.eval_harness   # scaled-down version of the Section 4 A3 eval plan
```

`tests/eval_harness.py` mirrors the methodology from the IMPACT document's
eval plan (labels set before running, scored against a target and a
minimum bar) with a smaller hand-reviewable scenario set. Growing it toward
the full 80-case suite described in Section 4 is the natural next step.

## Project layout

```
app.py                     Streamlit UI (3-screen wizard)
core/models.py              Shared data structures
core/calendar_loader.py     .ics parsing + timezone/all-day normalization
core/conflict_engine.py     Deterministic overlap/buffer classification + integrity checks
core/storage.py             Local JSON storage: recorded commitments, feedback, passive follow-up
tests/                       pytest suite + eval harness + fixture .ics files
data/                        Local JSON data (git-ignored)
```

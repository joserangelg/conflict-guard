"""Local JSON persistence: recorded commitments, dispute/override feedback,
and the passive "was the last result correct?" follow-up.

Feedback records are privacy-conscious by design: no event titles,
attendees, locations, or files are stored, only the structural facts needed
to review a disputed classification (per the LOCKED RULE in Section 4,
failure mode #3).
"""
import json
import os
from datetime import datetime, date

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
COMMITMENTS_FILE = os.path.join(DATA_DIR, "recorded_commitments.json")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback_log.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending_followup.json")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save(path, data):
    _ensure_dir()
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_commitments():
    return _load(COMMITMENTS_FILE, [])


def record_commitment(title, category, start, end, decision):
    commitments = load_commitments()
    commitments.append({
        "title": title,
        "category": category,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "decision": decision,
        "recorded_at": datetime.now().isoformat(),
    })
    _save(COMMITMENTS_FILE, commitments)
    return commitments[-1]


def load_feedback():
    return _load(FEEDBACK_FILE, [])


def log_feedback(kind, classification_before, rule_used, overlap_minutes,
                  buffer_gap_minutes, source_category, buffer_minutes):
    """kind: 'disputed_result' | 'not_a_conflict_override'"""
    entries = load_feedback()
    entries.append({
        "kind": kind,
        "classification_before": classification_before,
        "rule_used": rule_used,
        "overlap_minutes": overlap_minutes,
        "buffer_gap_minutes": buffer_gap_minutes,
        "source_category": source_category,
        "buffer_minutes": buffer_minutes,
        "logged_at": datetime.now().isoformat(),
    })
    _save(FEEDBACK_FILE, entries)


def set_pending_followup(final_result, evidence_count):
    _save(PENDING_FILE, {
        "final_result": final_result,
        "evidence_count": evidence_count,
        "set_at": datetime.now().isoformat(),
        "answered": False,
    })


def get_pending_followup():
    data = _load(PENDING_FILE, None)
    if data and not data.get("answered", False):
        return data
    return None


def answer_pending_followup(was_correct: bool):
    data = _load(PENDING_FILE, None)
    if data is None:
        return
    data["answered"] = True
    data["was_correct"] = was_correct
    data["answered_at"] = datetime.now().isoformat()
    _save(PENDING_FILE, data)
    if not was_correct:
        log_feedback(
            kind="passive_followup_disputed",
            classification_before=data.get("final_result"),
            rule_used="passive_followup",
            overlap_minutes=None,
            buffer_gap_minutes=None,
            source_category=None,
            buffer_minutes=None,
        )

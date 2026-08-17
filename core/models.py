"""Shared data structures for ConflictGuard.

No behavior lives here on purpose -- these are plain records passed between
core.calendar_loader and core.conflict_engine so both modules can be tested
independently.
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Optional


class SourceStatus(str, Enum):
    LOADED = "Loaded"
    NO_RELEVANT_EVENTS = "No relevant events"
    EMPTY_UNVERIFIED = "Empty or unverified"
    FAILED = "Failed"
    MISSING = "Missing"


class TZStatus(str, Enum):
    VERIFIED = "Verified"
    ASSUMED = "Assumed"
    DATE_VERIFIED = "Date verified"
    AMBIGUOUS_DATE = "Ambiguous date"


class Classification(str, Enum):
    HARD_CONFLICT = "Hard conflict"
    SOFT_CONFLICT = "Soft conflict"
    BOUNDARY = "Boundary"
    NONE = "No conflict"


class FinalResult(str, Enum):
    AVAILABLE = "Available"
    CONFLICT = "Conflict"
    SOFT_CONFLICT = "Soft conflict warning"
    REQUIRES_REVIEW = "Availability requires review"


CATEGORIES = ["Work", "School", "Personal"]


@dataclass
class NormalizedEvent:
    source_category: str          # Work / School / Personal
    source_filename: str
    summary: str
    start: datetime                # always tz-aware, normalized to home tz (or date-based for all-day)
    end: datetime                  # exclusive end, tz-aware, normalized to home tz
    is_all_day: bool
    tz_status: TZStatus
    is_ambiguous: bool = False     # unresolved ambiguous timestamp (looks like all-day, stored as time)
    ambiguity_resolved: Optional[str] = None  # "all_day" | "timed" | None
    is_recurring: bool = False
    raw_original_repr: str = ""    # human-readable original time/tz, kept for transparency
    uid: str = ""

    def event_key(self):
        return (self.source_category, self.summary, self.start, self.end)


@dataclass
class CalendarSource:
    category: str
    filename: Optional[str] = None
    status: SourceStatus = SourceStatus.MISSING
    raw_event_count: int = 0          # total VEVENTs found in the file, before date filtering
    relevant_event_count: int = 0     # events within the evaluation window
    error_message: Optional[str] = None
    file_size_bytes: Optional[int] = None
    upload_time: Optional[datetime] = None
    confirmed_empty: bool = False
    events: list = field(default_factory=list)  # list[NormalizedEvent]


@dataclass
class ProposedCommitment:
    title: str
    category: str
    start: datetime
    end: datetime


@dataclass
class EvidenceRow:
    event: NormalizedEvent
    overlap_minutes: int
    buffer_gap_minutes: Optional[int]
    classification: Classification
    rule: str
    user_override: Optional[str] = None  # "not_a_conflict" if user disputed this row


@dataclass
class EvaluationResult:
    final_result: FinalResult
    evidence: list = field(default_factory=list)   # list[EvidenceRow]
    blocking_reasons: list = field(default_factory=list)  # list[str] -- why review is required
    buffer_minutes: Optional[int] = None
    integrity_checks: dict = field(default_factory=dict)  # name -> bool

"""
approval.py -- binds a human operator's decision to a specific, hashed
schedule file, and records that decision in an append-only log.

Job of this file (see security/README.md for the ISO 27001 control
mapping; DECISIONS.md records the ADR): a login alone (auth.py) proves who
clicked ACCEPT. It says nothing about WHAT they accepted. ADR-005 already
established the schedule CSV as the single artefact that determines signal
behaviour, so an approval that is not bound to the exact bytes of that
file is an approval of nothing in particular - the file could be swapped
before or after the click and the record would not know. sha256_file binds
the two together; verify_still_valid lets anyone re-check that binding
later without trusting anything but the file on disk and the log entry.

Pure functions plus file IO only: no pygame, no import of traffic_sim.
Unit-testable in isolation (see security/test_security.py) the same way
crypto.py, channel.py and detection.py are.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

DEFAULT_APPROVAL_LOG_PATH = Path(__file__).parent / "approvals.jsonl"

# 1 MiB chunks: large enough to be fast, small enough that hashing a
# schedule file (tens of KB to low single-digit MB in this project) never
# holds more than one chunk in memory at a time.
_HASH_CHUNK_BYTES = 1024 * 1024


def sha256_file(path) -> str:
    """SHA-256 of a file's bytes, as lowercase hex. Reads in chunks rather
    than loading the whole file, so this stays correct (if unnecessary)
    for a schedule file much larger than the current one."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ApprovalRecord:
    """One line of security/approvals.jsonl. `decision` is a string
    ("approved") rather than a bool so a future rejection-logging path
    (not built here - only successful approvals ever reach
    append_approval, see traffic_sim.py) has somewhere to write without a
    schema change."""

    timestamp: str          # ISO 8601, UTC
    username: str
    schedule_path: str      # as given to append_approval - see load_latest_approval
    sha256: str
    decision: str = "approved"

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json_line(cls, line: str) -> "ApprovalRecord":
        return cls(**json.loads(line))


def append_approval(record: ApprovalRecord, log_path=DEFAULT_APPROVAL_LOG_PATH) -> None:
    """Appends one JSON line. Never rewrites or truncates the log - this
    is the append-only property the ADR relies on for "who approved what,
    and when" to stay a trustworthy history rather than an editable one."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(record.to_json_line() + "\n")


def load_latest_approval(schedule_path, log_path=DEFAULT_APPROVAL_LOG_PATH) -> Optional[ApprovalRecord]:
    """Most recent record whose schedule_path matches (compared as given -
    callers should pass the same string form consistently, e.g. always
    str(Path(...)), so a match is not missed on a path-formatting
    technicality). Returns None if the log does not exist or has no
    matching record, never raises for either of those - an unknown path
    is an expected outcome (first run, or a path never approved), not an
    exceptional one."""
    log_path = Path(log_path)
    if not log_path.exists():
        return None
    latest = None
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = ApprovalRecord.from_json_line(line)
            if record.schedule_path == str(schedule_path):
                latest = record
    return latest


def verify_still_valid(schedule_path, record: ApprovalRecord) -> bool:
    """Recomputes the hash of the file at schedule_path NOW and compares
    it against the hash stored in `record` - the check that turns "was
    approved once" into "is still the thing that was approved". Returns
    False (not an exception) if the file is missing, since "the approved
    file is gone" is exactly the kind of invalidity this function exists
    to report, not a caller error."""
    try:
        current_hash = sha256_file(schedule_path)
    except FileNotFoundError:
        return False
    return current_hash == record.sha256

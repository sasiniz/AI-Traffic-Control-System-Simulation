"""
auth.py -- bcrypt operator authentication for the manual-override dashboard.

Job of this file (see security/README.md for the ISO 27001 control mapping):
    Authenticate the HUMAN at the keyboard before they can use the manual
    override controls on the dashboard. This is a separate concern from
    crypto.py's job of protecting sensor readings in transit.

Design note (ADR-023, see DECISIONS.md):
    bcrypt is deliberately slow (it runs a tunable number of Blowfish
    rounds) so that offline brute-forcing a stolen password hash is
    expensive. That property is exactly wrong for authenticating a stream
    of sensor messages -- you cannot bcrypt-hash every reading without
    making the simulation loop artificially slow, and bcrypt alone gives
    no protection against replaying a previously valid message. AES-256-GCM
    in crypto.py is the right tool for that job. bcrypt is the right tool
    for "did this human type the correct operator password", which happens
    once per override attempt, not once per sensor tick.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import bcrypt

DEFAULT_CREDENTIALS_PATH = Path(__file__).parent / "operators.json"

# bcrypt's own maximum; passwords longer than this are silently truncated
# by the underlying algorithm, so we reject them explicitly instead.
BCRYPT_MAX_PASSWORD_BYTES = 72


class AuthError(Exception):
    """Raised on registration or verification misuse, e.g. duplicate
    username or a password bcrypt cannot handle. NOT raised for a wrong
    password during login -- verify() returns False for that, since a
    failed login is an expected, not exceptional, outcome."""


@dataclass
class OperatorAuth:
    """In-memory + JSON-file-backed operator credential store.

    Usage:
        auth = OperatorAuth.load_or_create(DEFAULT_CREDENTIALS_PATH)
        auth.register("operator1", "correct horse battery staple")
        auth.save()
        ...
        if auth.verify("operator1", entered_password):
            allow_override()
    """

    _hashes: Dict[str, str] = field(default_factory=dict)  # username -> bcrypt hash (utf-8 str)
    _path: Optional[Path] = None

    # -- construction ------------------------------------------------

    @classmethod
    def load_or_create(cls, path: Path = DEFAULT_CREDENTIALS_PATH) -> "OperatorAuth":
        inst = cls(_path=path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            inst._hashes = data.get("operators", {})
        return inst

    def save(self, path: Optional[Path] = None) -> None:
        target = path or self._path
        if target is None:
            raise AuthError("no path set; pass path= explicitly")
        target.write_text(
            json.dumps({"operators": self._hashes}, indent=2),
            encoding="utf-8",
        )

    # -- registration --------------------------------------------------

    def register(self, username: str, password: str, *, overwrite: bool = False) -> None:
        if not username:
            raise AuthError("username must not be empty")
        pw_bytes = password.encode("utf-8")
        if len(pw_bytes) == 0:
            raise AuthError("password must not be empty")
        if len(pw_bytes) > BCRYPT_MAX_PASSWORD_BYTES:
            raise AuthError(
                f"password exceeds bcrypt's {BCRYPT_MAX_PASSWORD_BYTES}-byte limit; "
                "hash a pre-digested form (e.g. SHA-256) if longer passwords are required"
            )
        if username in self._hashes and not overwrite:
            raise AuthError(f"operator {username!r} already registered; pass overwrite=True to replace")

        salt = bcrypt.gensalt()  # bcrypt generates and embeds a fresh random salt per call
        hashed = bcrypt.hashpw(pw_bytes, salt)
        self._hashes[username] = hashed.decode("utf-8")

    def remove(self, username: str) -> None:
        self._hashes.pop(username, None)

    # -- verification ----------------------------------------------------

    def verify(self, username: str, password: str) -> bool:
        """Returns True/False. Never raises for a wrong password or an
        unknown username -- both are just "not authenticated"."""
        stored = self._hashes.get(username)
        if stored is None:
            # Still run a bcrypt comparison against a dummy hash so that
            # "unknown username" and "wrong password" take a similar amount
            # of time. This is a lightweight defence against username
            # enumeration via timing; it is not a claim of full mitigation.
            bcrypt.checkpw(b"invalid", bcrypt.gensalt())
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except ValueError:
            # Malformed stored hash (e.g. file was hand-edited).
            return False

    def has_operator(self, username: str) -> bool:
        return username in self._hashes


if __name__ == "__main__":
    tmp_path = Path("/tmp/_auth_smoke_test.json")
    if tmp_path.exists():
        tmp_path.unlink()
    auth = OperatorAuth.load_or_create(tmp_path)
    auth.register("operator1", "correct horse battery staple")
    auth.save()

    reloaded = OperatorAuth.load_or_create(tmp_path)
    print("correct password:", reloaded.verify("operator1", "correct horse battery staple"))
    print("wrong password:", reloaded.verify("operator1", "wrong password"))
    print("unknown user:", reloaded.verify("nobody", "anything"))
    tmp_path.unlink()

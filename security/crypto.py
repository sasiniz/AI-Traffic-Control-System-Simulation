"""
crypto.py -- AES-256-GCM encryption and authentication for sensor readings.

Job of this file (see security/README.md for the ISO 27001 control mapping):
    Confidentiality AND integrity of data in transit between a simulated
    sensor and the dashboard. NOT at rest: sensor_log.csv is written as
    plaintext. The encrypt_log_line/decrypt_log_line helpers below exist
    and pass their unit tests, but have zero call sites outside
    test_security.py - see ADR-029.

Design note (ADR-023, see DECISIONS.md):
    AES-256-GCM is an AEAD cipher: encryption and authentication happen in
    one operation. The output includes a 16-byte authentication tag. If a
    single bit of the ciphertext or the associated data is changed after
    encryption, decrypt() raises InvalidTag and returns nothing. This is
    what lets attacks.py demonstrate "tampering fails to decrypt" rather
    than just "tampering is logged after the fact".

    This file does NOT use bcrypt. bcrypt is a slow, salted hash designed
    for storing human passwords (auth.py). Using a password hash to
    authenticate a message would not provide freshness or tamper-evidence
    the way an AEAD tag does, and would make sensor traffic trivially
    replayable. The two primitives solve different problems and are kept
    in separate files on purpose.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# AES-256 -> 32-byte key. AES-128-GCM would use 16; we use 256-bit per the
# project's stated design (section 8 of the handoff / ADR-023).
KEY_BYTES = 32
# 96-bit (12-byte) nonce is the size AESGCM is designed and optimised for.
# Do not reuse a nonce with the same key -- generate_nonce() uses os.urandom,
# which is cryptographically random and, at this message volume (a handful
# of sensor readings per simulated second, for a student project run over
# minutes to hours), the collision probability is negligible.
NONCE_BYTES = 12


class DecryptionError(Exception):
    """Raised when a ciphertext fails authentication (tampered, wrong key,
    corrupted, or truncated). Callers must treat this as "reject the
    message", not as a generic I/O error."""


def generate_key() -> bytes:
    """Generate a fresh random AES-256 key. In a real deployment this would
    come from a key management service; for the simulation it is generated
    once per run and shared out-of-band between the simulated sensor and
    the dashboard process (both run in the same process here, since the
    channel is in-process -- see channel.py and ADR-023)."""
    return AESGCM.generate_key(bit_length=256)


def generate_nonce() -> bytes:
    return os.urandom(NONCE_BYTES)


@dataclass(frozen=True)
class EncryptedMessage:
    """Wire format for an encrypted sensor reading. nonce and ciphertext
    are both required to decrypt; associated_data (if any) must be
    resupplied unchanged by the receiver."""

    nonce: bytes
    ciphertext: bytes  # includes the 16-byte GCM tag, appended by AESGCM

    def to_wire(self) -> bytes:
        """Serialise to a single bytes blob suitable for sending over
        channel.py or writing to disk: nonce || ciphertext."""
        return self.nonce + self.ciphertext

    @classmethod
    def from_wire(cls, blob: bytes) -> "EncryptedMessage":
        if len(blob) < NONCE_BYTES:
            raise DecryptionError("message shorter than one nonce; not a valid EncryptedMessage")
        return cls(nonce=blob[:NONCE_BYTES], ciphertext=blob[NONCE_BYTES:])

    def to_b64_dict(self) -> dict:
        """JSON/CSV-friendly form, used by encrypt_log_line()."""
        return {
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
        }

    @classmethod
    def from_b64_dict(cls, d: dict) -> "EncryptedMessage":
        return cls(
            nonce=base64.b64decode(d["nonce"]),
            ciphertext=base64.b64decode(d["ciphertext"]),
        )


class SensorCrypto:
    """One instance wraps one AES-256-GCM key. Create one instance and share
    it between the simulated sensor (encrypt) and the dashboard (decrypt) --
    in the in-process channel used by this project (ADR-023 "Option B"),
    that just means both ends hold a reference to the same key bytes.
    """

    def __init__(self, key: Optional[bytes] = None):
        self.key = key if key is not None else generate_key()
        if len(self.key) != KEY_BYTES:
            raise ValueError(f"AES-256 key must be {KEY_BYTES} bytes, got {len(self.key)}")
        self._aesgcm = AESGCM(self.key)

    def encrypt(self, plaintext: bytes, associated_data: Optional[bytes] = None) -> EncryptedMessage:
        """Encrypt and authenticate. associated_data is authenticated but
        NOT encrypted -- use it for fields that must be tamper-evident but
        are allowed to be read in the clear, e.g. a road/junction ID used
        for routing before decryption. Pass the same associated_data to
        decrypt() or it will fail."""
        nonce = generate_nonce()
        ct = self._aesgcm.encrypt(nonce, plaintext, associated_data)
        return EncryptedMessage(nonce=nonce, ciphertext=ct)

    def decrypt(self, message: EncryptedMessage, associated_data: Optional[bytes] = None) -> bytes:
        """Decrypt and verify. Raises DecryptionError if the ciphertext,
        nonce, or associated_data has been altered, or if the key is
        wrong. This is the call site that must reject a spoofed or
        tampered sensor reading -- see channel.py's receive()."""
        try:
            return self._aesgcm.decrypt(message.nonce, message.ciphertext, associated_data)
        except InvalidTag as exc:
            raise DecryptionError("GCM tag verification failed: message was tampered with, "
                                   "corrupted, or encrypted under a different key") from exc

    # -- convenience wrappers for JSON-shaped sensor readings ------------

    def encrypt_json(self, obj: dict, associated_data: Optional[bytes] = None) -> EncryptedMessage:
        return self.encrypt(json.dumps(obj, separators=(",", ":")).encode("utf-8"), associated_data)

    def decrypt_json(self, message: EncryptedMessage, associated_data: Optional[bytes] = None) -> dict:
        return json.loads(self.decrypt(message, associated_data).decode("utf-8"))

    # -- storage helpers: IMPLEMENTED BUT NOT WIRED (ADR-029) ------------

    def encrypt_log_line(self, line: str) -> str:
        """Encrypt one CSV log line into a single base64 text field safe to
        write as one CSV column. Not called from the simulation's write
        path -- sensor_log.csv is plaintext on disk, see ADR-029. Reuses
        the same AEAD primitive as the transport path -- 'nearly free once
        crypto.py exists', per the handoff notes."""
        msg = self.encrypt(line.encode("utf-8"))
        return base64.b64encode(msg.to_wire()).decode("ascii")

    def decrypt_log_line(self, blob_b64: str) -> str:
        blob = base64.b64decode(blob_b64)
        msg = EncryptedMessage.from_wire(blob)
        return self.decrypt(msg).decode("utf-8")


if __name__ == "__main__":
    # Minimal smoke test. test_security.py has the real assertions.
    sc = SensorCrypto()
    msg = sc.encrypt_json({"road": "North", "vehicles": 41})
    print("encrypted ok, wire length:", len(msg.to_wire()))
    print("decrypted:", sc.decrypt_json(msg))

    tampered = EncryptedMessage(nonce=msg.nonce, ciphertext=bytes([msg.ciphertext[0] ^ 0xFF]) + msg.ciphertext[1:])
    try:
        sc.decrypt_json(tampered)
        print("BUG: tampered message was accepted")
    except DecryptionError as e:
        print("tamper correctly rejected:", e)

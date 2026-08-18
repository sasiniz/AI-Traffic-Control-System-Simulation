"""
attacks.py -- the two in-scope attacks. Nothing else.

DoS/DDoS is explicitly OUT of scope (see the handoff notes, section 8): a
realistic denial-of-service simulation needs network infrastructure and
load generation this project does not have on one laptop. Do not add one
here. If you are reading this file wondering whether to add a flood/DoS
attack for "completeness", the answer that was already decided is no --
raise it with the supervisor first, don't just add it.

Both attacks are implemented as Interceptor callables (see channel.py)
that are inserted into SensorChannel.interceptors. This is what makes the
"succeeds with encryption off, fails with encryption on" demonstration
work with a single `E` keypress: the attack itself does not change, only
whether channel.encryption_enabled is True.

    False data injection (`1`): replaces a genuine reading's vehicle count
        with an attacker-chosen number, without needing to know the
        AES key. When encryption is off, this is a plain JSON string
        substitution and succeeds silently. When encryption is on, the
        interceptor still substitutes bytes, but those bytes are no longer
        a valid AES-256-GCM ciphertext for ANY key with a matching tag, so
        channel.receive() raises ChannelRejected via DecryptionError.

    Sensor spoofing (`2`): the attacker plays a genuine-looking reading
        from a road/sensor ID it does not own, WITHOUT intercepting real
        traffic -- e.g. injecting a plausible extra reading claiming to be
        from "North" every tick. With encryption off, this is
        indistinguishable from a real reading at the channel layer, because
        the channel has no source authentication of its own -- only shape
        validation. With encryption on, the attacker has no valid AES key,
        so it cannot produce a ciphertext that passes AEAD verification;
        its forged reading is rejected the same way a tampered one is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .channel import Interceptor


@dataclass
class FalseDataInjectionAttack:
    """Bound to dashboard key `1`.

    Rewrites the `vehicles` field of every message that passes through to
    a fixed, attacker-chosen value, regardless of what the real sensor
    reported. Operates on wire bytes, so it does not need to understand
    whether encryption is currently on -- that is exactly the point:
    the SAME interceptor code demonstrates both outcomes in the table
    in the handoff notes.
    """

    fake_vehicle_count: int = 999
    road: Optional[str] = None  # None = affect every road; set to target one approach

    def __call__(self, wire_bytes: bytes) -> Optional[bytes]:
        # Try to treat this as an unencrypted JSON reading we can edit
        # in place. If it's ciphertext (encryption on), this will fail to
        # parse as JSON and we substitute garbage instead -- either way
        # the attack tampers with the bytes actually on the wire, which is
        # the honest thing to simulate: an attacker who does not hold the
        # key cannot selectively edit a field inside a GCM ciphertext.
        try:
            reading = json.loads(wire_bytes.decode("utf-8"))
            if self.road is not None and reading.get("road") != self.road:
                return wire_bytes  # not our target road, pass through unchanged
            reading["vehicles"] = self.fake_vehicle_count
            return json.dumps(reading, separators=(",", ":")).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Ciphertext path: flip a byte in the middle of the blob. This
            # is the realistic capability of an attacker without the key --
            # they can corrupt/replace bytes on the wire, but cannot craft
            # a ciphertext that decrypts to a chosen plaintext AND passes
            # the GCM tag check.
            if len(wire_bytes) < 2:
                return wire_bytes
            mid = len(wire_bytes) // 2
            corrupted = bytearray(wire_bytes)
            corrupted[mid] ^= 0xFF
            return bytes(corrupted)


@dataclass
class SensorSpoofingAttack:
    """Bound to dashboard key `2`.

    Simulates a rogue sensor: instead of altering a real reading in
    flight, it fabricates one from scratch, claiming to be a legitimate
    road/junction sensor. As an Interceptor it is applied to whatever
    real message happens to be passing (so it can still run inside the
    same channel.receive() call), but it DISCARDS the real payload and
    substitutes its own forged reading -- modelling an attacker that
    injects traffic rather than only tampering with existing traffic.

    Without a shared AES key, the attacker cannot produce ciphertext that
    verifies, so when encryption is on this is rejected exactly like
    FalseDataInjectionAttack's corrupted-ciphertext path.
    """

    spoofed_road: str = "North"
    spoofed_vehicle_count: int = 250

    def __call__(self, wire_bytes: bytes) -> Optional[bytes]:
        forged = json.dumps(
            {"road": self.spoofed_road, "vehicles": self.spoofed_vehicle_count, "spoofed": True},
            separators=(",", ":"),
        ).encode("utf-8")
        # If the real traffic was plaintext (encryption off), the attacker
        # can simply substitute a well-formed forged reading and it will
        # parse and be accepted -- this is the "no valid key -> rejected"
        # contrast point only once encryption is switched on, at which
        # point this plaintext forgery fails EncryptedMessage.from_wire /
        # AEAD verification in channel.receive(), because it is not a
        # valid ciphertext at all.
        return forged


def make_interceptor(attack) -> Interceptor:
    """Type-narrowing helper so channel.add_interceptor(make_interceptor(x))
    reads clearly at call sites; attack instances are already callables."""
    return attack

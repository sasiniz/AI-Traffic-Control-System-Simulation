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

    False data injection: replaces a genuine reading's vehicle count with
        an attacker-chosen number, without needing to know the AES key.
        Two magnitude modes (see FalseDataInjectionAttack.mode):

          "crude"    - a fixed, physically impossible value (999 vehicles
                       in a 10-second window). Exists to show that naive
                       attacks are caught by plausibility checks alone,
                       with no cryptography required - see
                       security/detection.py's S2 IMPLAUSIBLE signal.
          "stealthy" - the TRUE value scaled by a small factor, so the
                       reported number stays inside the physically
                       plausible envelope and evades a plausibility check.
                       It is still an attack: the reported value differs
                       from what was actually measured, which is what
                       security/detection.py's S3 DIVERGENCE signal is
                       for.

        When encryption is on and the attacker does NOT hold the key, this
        attack still substitutes bytes, but those bytes are no longer a
        valid AES-256-GCM ciphertext for ANY key with a matching tag, so
        channel.receive() raises ChannelRejected via DecryptionError -
        regardless of mode. Cryptography does not care how plausible the
        attacker's fabricated number is.

    Sensor spoofing: the attacker plays a genuine-looking reading from a
        road/sensor ID it does not own, WITHOUT intercepting real traffic
        -- e.g. injecting a plausible extra reading claiming to be from
        "North" every tick. With encryption off, this is indistinguishable
        from a real reading at the channel layer, because the channel has
        no source authentication of its own -- only shape validation. With
        encryption on and no stolen key, the attacker cannot produce a
        ciphertext that passes AEAD verification; its forged reading is
        rejected the same way a tampered one is.

KEY-COMPROMISE VARIANT (both attacks, `crypto=` constructor argument)
----------------------------------------------------------------------
Both attacks accept an optional `crypto` argument. When supplied, it must
be the SAME SensorCrypto instance (same AES key) as the channel under
attack - modelling an insider or a stolen key, not a network eavesdropper.
With the key, the attacker re-encrypts its tampered/forged payload, so the
result passes AEAD verification even with encryption on. This is the
scenario that justifies having anomaly detection at all: once the key is
compromised, cryptography cannot help - S1 (integrity failure) can never
fire again for this attacker, and detection depends on the physically-
implausible (S2) or diverges-from-reality (S3) signals in
security/detection.py catching what the cipher no longer can.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .channel import Interceptor
from .crypto import DecryptionError, EncryptedMessage, SensorCrypto

# Internal sentinel: distinguishes "the key-compromise path does not apply
# to this message, fall through to the plaintext path" from a real `None`
# return, which the Interceptor contract (see channel.py) reserves for
# "drop the message".
_NOT_APPLICABLE = object()


@dataclass
class FalseDataInjectionAttack:
    """Bound to dashboard key `F`.

    Rewrites the `vehicles` field of every message that passes through.
    `mode` controls how the fake value is chosen (see module docstring):
    "crude" substitutes a fixed, implausible constant; "stealthy" scales
    the TRUE value instead of replacing it, so it must read the real
    number out of the message rather than invent one.
    """

    mode: str = "crude"                # "crude" or "stealthy"
    fake_vehicle_count: int = 999      # used by "crude"
    # 1.3x is the given attack parameter from the threat-classification spec
    # this module was built against (Phase 1a: "multiply the TRUE value by a
    # small factor (default 1.3...)") - not independently derived, and not a
    # detection threshold; recorded here so it is not a silent bare number.
    stealth_factor: float = 1.3        # used by "stealthy"
    road: Optional[str] = None         # None = affect every road
    crypto: Optional[SensorCrypto] = None  # None = no key (network attacker)

    def __call__(self, wire_bytes: bytes) -> Optional[bytes]:
        if self.crypto is not None:
            forged = self._try_key_compromise(wire_bytes)
            if forged is not _NOT_APPLICABLE:
                return forged
        return self._tamper_without_key(wire_bytes)

    # -- attacker holds the AES key (stolen key / insider) -------------
    def _try_key_compromise(self, wire_bytes: bytes):
        try:
            msg = EncryptedMessage.from_wire(wire_bytes)
            reading = self.crypto.decrypt_json(msg)
        except DecryptionError:
            # Not valid ciphertext under this key (e.g. encryption is
            # currently off, so wire_bytes is plaintext JSON) - the key
            # does not help here, fall through to the no-key path.
            return _NOT_APPLICABLE
        if self.road is not None and reading.get("road") != self.road:
            return wire_bytes  # not our target road, pass through unchanged
        reading["vehicles"] = self._fake_value(reading.get("vehicles", 0))
        return self.crypto.encrypt_json(reading).to_wire()

    # -- attacker does NOT hold the key (network attacker) --------------
    def _tamper_without_key(self, wire_bytes: bytes) -> Optional[bytes]:
        # Try to treat this as an unencrypted JSON reading we can edit in
        # place. If it's ciphertext (encryption on, no stolen key), this
        # will fail to parse as JSON and we substitute garbage instead --
        # either way the attack tampers with the bytes actually on the
        # wire, which is the honest thing to simulate: an attacker who
        # does not hold the key cannot selectively edit a field inside a
        # GCM ciphertext.
        try:
            reading = json.loads(wire_bytes.decode("utf-8"))
            if self.road is not None and reading.get("road") != self.road:
                return wire_bytes  # not our target road, pass through unchanged
            reading["vehicles"] = self._fake_value(reading.get("vehicles", 0))
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

    def _fake_value(self, true_value) -> int:
        if self.mode == "stealthy":
            faked = round(true_value * self.stealth_factor)
            if faked == true_value:
                # Minimum absolute change of 1 so a stealthy attack on a
                # small true value (including 0) still has an effect.
                faked += 1
            return faked
        return self.fake_vehicle_count


@dataclass
class SensorSpoofingAttack:
    """Bound to dashboard key `G`.

    Simulates a rogue sensor: instead of altering a real reading in
    flight, it fabricates one from scratch, claiming to be a legitimate
    road/junction sensor. As an Interceptor it is applied to whatever
    real message happens to be passing (so it can still run inside the
    same channel.receive() call), but it DISCARDS the real payload and
    substitutes its own forged reading -- modelling an attacker that
    injects traffic rather than only tampering with existing traffic.

    Without a shared AES key, the attacker cannot produce ciphertext that
    verifies, so when encryption is on this is rejected exactly like
    FalseDataInjectionAttack's corrupted-ciphertext path. WITH a stolen
    key (`crypto=`), the forged reading is encrypted under that key and
    passes AEAD verification - decrypting the real message first is
    unnecessary here (the forgery discards it regardless of content), so
    the only step needed is re-encrypting the forgery.
    """

    spoofed_road: str = "North"
    spoofed_vehicle_count: int = 250
    crypto: Optional[SensorCrypto] = None  # None = no key (network attacker)

    def __call__(self, wire_bytes: bytes) -> Optional[bytes]:
        forged = {
            "road": self.spoofed_road,
            "vehicles": self.spoofed_vehicle_count,
            "spoofed": True,
        }
        if self.crypto is not None:
            return self.crypto.encrypt_json(forged).to_wire()
        # If the real traffic was plaintext (encryption off), the attacker
        # can simply substitute a well-formed forged reading and it will
        # parse and be accepted -- this is the "no valid key -> rejected"
        # contrast point only once encryption is switched on, at which
        # point this plaintext forgery fails EncryptedMessage.from_wire /
        # AEAD verification in channel.receive(), because it is not a
        # valid ciphertext at all.
        return json.dumps(forged, separators=(",", ":")).encode("utf-8")


def make_interceptor(attack) -> Interceptor:
    """Type-narrowing helper so channel.add_interceptor(make_interceptor(x))
    reads clearly at call sites; attack instances are already callables."""
    return attack

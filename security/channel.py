"""
channel.py -- the sensor channel abstraction.

Everything in the security demo hangs off this one point (per the handoff
notes, section 8):

    sensor reading -> channel.send() -> [encryption] -> [attacker] -> channel.receive() -> dashboard

This is Option B from ADR-023: an IN-PROCESS channel, not real UDP sockets.
Scapy and real network I/O are out of scope; SensorChannel just passes
Python bytes through a list of interceptor callables, one of which may be
an AES-256-GCM encryption/decryption step and any others of which may be
attacks (see attacks.py). This keeps the whole demonstration runnable on
one laptop with no network stack, which is why Option B was chosen given
the timeline.

Encryption is a flag on the channel (`encryption_enabled`), not a code path
that gets deleted -- toggling it with the `E` key in the dashboard (see the
integration notes delivered alongside this file) must be able to demonstrate
the SAME attack succeeding with encryption off and failing with encryption
on, using one keypress, without restarting the simulation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .crypto import DecryptionError, EncryptedMessage, SensorCrypto

# An interceptor sits between sender and receiver and gets a chance to
# alter, drop, or pass through the wire-format bytes. Used by attacks.py.
# Signature: (wire_bytes: bytes) -> bytes | None   (None = drop the message)
Interceptor = Callable[[bytes], Optional[bytes]]


class ChannelRejected(Exception):
    """Raised by receive() when a message cannot be accepted -- either it
    failed AEAD authentication (tampered/spoofed while encryption was on)
    or an interceptor dropped it. The dashboard should catch this and show
    a rejection, not crash."""


@dataclass
class ChannelEvent:
    """One record of what happened to one message, for the dashboard log
    and for sensor_log.csv. `accepted` is the one field the dashboard
    needs to decide what to draw."""

    accepted: bool
    reason: str
    plaintext: Optional[dict] = None


@dataclass
class SensorChannel:
    """One instance per simulated sensor link (e.g. one per road/approach,
    or one shared channel carrying tagged readings -- caller's choice).

    encryption_enabled toggles AES-256-GCM on the send/receive path. This
    field is meant to be flipped live by the dashboard's `E` key handler:

        channel.encryption_enabled = not channel.encryption_enabled

    interceptors is an ordered list of Interceptor callables applied, in
    order, to the wire bytes after encryption (or after plain JSON
    serialisation, if encryption is off) and before the receiver sees them.
    Attacks register themselves here; `3` (clear attacks) should just do
    channel.interceptors.clear().
    """

    crypto: SensorCrypto
    encryption_enabled: bool = True
    interceptors: List[Interceptor] = field(default_factory=list)

    # -- send side ---------------------------------------------------

    def send(self, reading: dict) -> bytes:
        """Serialise a sensor reading (a plain dict, e.g.
        {"road": "North", "vehicles": 41, "ts": ...}) to wire bytes,
        encrypting it if encryption_enabled. Returns the wire bytes that
        would be put "on the network" -- callers normally pass this
        straight to receive(), but it is returned separately so tests and
        attacks can inspect or mutate it first.
        """
        if self.encryption_enabled:
            msg = self.crypto.encrypt_json(reading)
            return msg.to_wire()
        return json.dumps(reading, separators=(",", ":")).encode("utf-8")

    # -- receive side --------------------------------------------------

    def receive(self, wire_bytes: bytes) -> ChannelEvent:
        """Run wire_bytes through all registered interceptors, then decode.
        Raises ChannelRejected if any interceptor drops the message or if
        AEAD authentication fails. This is the single point where a
        tampered or spoofed reading is caught when encryption is on."""
        current = wire_bytes
        for interceptor in self.interceptors:
            result = interceptor(current)
            if result is None:
                raise ChannelRejected("message dropped by interceptor")
            current = result

        if self.encryption_enabled:
            try:
                msg = EncryptedMessage.from_wire(current)
                reading = self.crypto.decrypt_json(msg)
            except DecryptionError as exc:
                raise ChannelRejected(f"AEAD verification failed: {exc}") from exc
        else:
            try:
                reading = json.loads(current.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                # With encryption off there is no integrity check at all --
                # this is the "no protection" baseline the dissertation
                # compares against. A malformed injection still surfaces
                # as an error, but a well-formed FAKE reading (see
                # attacks.py) sails straight through, which is the point.
                raise ChannelRejected(f"could not parse plaintext reading: {exc}") from exc

        return ChannelEvent(accepted=True, reason="ok", plaintext=reading)

    def send_and_receive(self, reading: dict) -> ChannelEvent:
        """Convenience for the simulation loop and for tests: send a
        reading and immediately push it through receive(), so callers
        don't have to thread wire bytes through manually."""
        wire = self.send(reading)
        return self.receive(wire)

    # -- interceptor management ------------------------------------------

    def add_interceptor(self, interceptor: Interceptor) -> None:
        self.interceptors.append(interceptor)

    def clear_interceptors(self) -> None:
        """Bound to the `3` dashboard key (clear attacks)."""
        self.interceptors.clear()


if __name__ == "__main__":
    from .crypto import SensorCrypto

    channel = SensorChannel(crypto=SensorCrypto(), encryption_enabled=True)
    event = channel.send_and_receive({"road": "North", "vehicles": 41})
    print("plain round trip:", event)

"""
detection.py -- threat classification for sensor channel readings.

Pure functions only: plain values in, a frozen result object out. No
pygame, no import of traffic_sim. This keeps the logic unit-testable in
isolation (see security/test_security.py) and keeps the security
contribution visible inside security/, where it belongs alongside
crypto.py, channel.py, auth.py and attacks.py rather than buried inside
the simulation file.

WHY THIS EXISTS
----------------
crypto.py and channel.py answer "was this message tampered with in
transit". They cannot answer "is what I'm seeing on the dashboard right
now a cyberattack or a real traffic incident" - that requires combining
the channel's verdict with the physical traffic state (SensorSystem, in
traffic_sim.py) and the pattern of readings across arms and time. That
combination is what this module does.

THE FIVE SIGNALS
------------------
  S1 INTEGRITY_FAIL  channel.receive() raised ChannelRejected (AEAD tag
                      failure). Only meaningful when encryption was
                      enabled at the time the message was sent - with
                      encryption off there is no integrity check to fail,
                      so S1 cannot fire (compute_signals gates on this).
  S2 IMPLAUSIBLE      the reported vehicle count exceeds the physical
                      bound green_seconds / SATURATION_HEADWAY_S, i.e.
                      more vehicles were reported than could physically
                      have discharged in the green time available. The
                      1.9s saturation headway is the Highway Capacity
                      Manual value already used for MIN_GREEN_TO_START in
                      traffic_sim.py (see DECISIONS.md ADR-006 and
                      ADR-021) - derived from that existing citation, not
                      invented here. Does not evaluate at all below
                      S2_MIN_GREEN_S (6.0s) of green in the window - see
                      that constant's comment for why.
  S3 DIVERGENCE       the reported value differs from the true value that
                      was actually sent into the channel. See the
                      CRITICAL HONESTY REQUIREMENT below before trusting
                      this signal's realism.
  S4 SIMULTANEITY     SIMULTANEITY_MIN_ARMS (3) or more arms show a
                      channel-level flag (S1, S2 or S3) within the same
                      reporting interval. A real physical incident is
                      localised to one arm; synchronised onset across
                      most of a four-arm junction is not how incidents
                      behave, and is itself a pattern worth flagging even
                      for an arm whose own reading looks clean.
  S5 PHYSICAL         the existing SensorSystem rule from traffic_sim.py
                      (queue present, green time available, discharge
                      rate collapsed), passed in as a plain bool -
                      detection.py does not recompute it and does not
                      import SensorSystem.

S5 IS UNREACHABLE AT DEMAND_MULTIPLIER = 1.0 (STATED, NOT TUNED AWAY)
------------------------------------------------------------------------
At real demand (traffic_sim.py's HOURLY_DEMAND, verified against
data/traffic_final_cleaned.csv), S5 essentially cannot fire, and this was
confirmed empirically, not just argued: 90 sim-minutes headless (normal
operation at two hours of day, plus a real hour-long accident at peak
demand) never once reached ANOMALY_QUEUE_MIN=5; max queue observed
including during the accident was 4.

The arithmetic behind why: pinch_capacity_veh_per_h =
(CRAWL_SPEED * 60 / MIN_GAP) * 3600 = (0.10 * 60 / 48) * 3600 = 450 veh/h.
peak_arm_demand_veh_per_h = max(HOURLY_DEMAND) = 59 (North, hour 19).
450 >> 59: a lane crawling at CRAWL_SPEED can clear vehicles roughly 7.6x
faster than the busiest real arm ever sends them, so a blockage cannot
build a persistent queue at real demand - there is structurally not
enough arriving traffic for one to form, regardless of the ANOMALY_RATE_MIN
threshold used downstream.

traffic_sim.py's DEMAND_MULTIPLIER (default 1.0, a presentation-only
toggle - see its comment in Section 5) exists BECAUSE of this: it is the
only way to demonstrate PHYSICAL_INCIDENT/S5 behaviour without changing
CRAWL_SPEED, MIN_GAP, ANOMALY_QUEUE_MIN or HOURLY_DEMAND, none of which
were altered to "make a demo work". This module's classification LOGIC is
unchanged by DEMAND_MULTIPLIER - it still only ever sees whatever bool
SensorSystem computed; scaling demand changes whether that bool is ever
True, not how compute_channel_signals/classify interpret it.

CRITICAL HONESTY REQUIREMENT
------------------------------
S3 (DIVERGENCE) compares the channel's reported value against
`true_vehicles` - in this codebase, simulation ground truth, because the
same Simulation object that sends the reading also knows exactly how many
vehicles it sent. A real deployment has NO such oracle: the sensor is the
only source of the count, so there is nothing independent to diverge
from. In a real system, S3's role would have to be played by a redundant
sensing modality (e.g. an inductive loop count cross-checked against a
camera count) or by the residual between the reading and the Random
Forest's forecast for that arm/hour. This module's use of simulation
ground truth is a STATED SIMPLIFICATION for the simulation, not a claim
that a deployed system has an oracle - do not present S3 as anything more
than that when describing this module.

DIVERGENCE_THRESHOLD_VEHICLES = 0 (not an arbitrary tolerance): in this
architecture, `reported` and `true_vehicles` are computed from the exact
same integer discharge count in the same function call
(Simulation._send_channel_reading) and transmitted synchronously - there
is no independent measurement noise between them the way there would be
between two real, physically separate sensors. Absent tampering the two
values are therefore identical by construction, so ANY non-zero
difference is definitionally attributable to something altering the
message in flight, not sensor disagreement. A non-zero tolerance would be
inventing slack that has no source in this design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Highway Capacity Manual, 6th ed., Transportation Research Board,
# Washington, DC, 2016. Saturation headway 1.9 s/vehicle - the same
# citation and the same constant already used to derive MIN_GREEN_TO_START
# in traffic_sim.py (see DECISIONS.md ADR-006, ADR-021). Reused here, not
# re-derived, so the two files cannot silently disagree about what the HCM
# says.
SATURATION_HEADWAY_S = 1.9

# See "CRITICAL HONESTY REQUIREMENT" above: zero is the structurally
# correct value in this architecture, not a rounded-off guess.
DIVERGENCE_THRESHOLD_VEHICLES = 0

# S2 must not evaluate at all below this much green time in the window.
# Mirrors ANOMALY_MIN_GREEN_S = 6.0 in traffic_sim.py (Section 6, the
# existing SensorSystem physical rule) rather than inventing a second,
# independent number for the same underlying judgement - "is there enough
# green time in this window to judge fairly".
#
# Mechanism, not just a threshold: S2's bound is green_s / SATURATION_
# HEADWAY_S. Below ~1.9s of green the bound drops under 1 vehicle, so ANY
# discharge counted in the reading trips S2 - and that discharge is very
# often real, carried over from a green phase that started just before the
# 20s rolling window began. discharges and green_seconds_window are both
# rolling-windowed independently and are not phase-aligned to each other:
# a vehicle can be counted as "discharged in the last 20s" while almost
# none of the green time that let it through falls inside that same 20s
# slice. The root cause is that misalignment, not the choice of threshold
# value - raising S2_MIN_GREEN_S papers over it without fixing it, so this
# guard is deliberately set to the same figure already trusted elsewhere
# in the codebase for "enough green to judge fairly", not tuned separately
# to make false positives disappear.
S2_MIN_GREEN_S = 6.0

# Given directly by the classification design (see module docstring S4):
# "three or more arms flagged in the same interval". Not derived from
# data - it is a definition, the same way "S5 requires 3+ signals" would
# be, and is recorded as a named constant so it is not a bare number
# inside simultaneity_flag().
SIMULTANEITY_MIN_ARMS = 3

CLASSIFICATION_NORMAL = "NORMAL"
CLASSIFICATION_PHYSICAL_INCIDENT = "PHYSICAL_INCIDENT"
CLASSIFICATION_AMBIGUOUS = "AMBIGUOUS"
CLASSIFICATION_CYBER_LIKELY = "CYBER_LIKELY"
CLASSIFICATION_CYBER_CONFIRMED = "CYBER_CONFIRMED"

ACTION_NOTIFY_TRAFFIC_OPS = "notify traffic operations"
ACTION_NOTIFY_SECURITY = "notify security team, preserve channel logs"
ACTION_VERIFY_CAMERA = "operator: verify via camera feed before routing"
ACTION_NONE = "none"


@dataclass(frozen=True)
class SignalFlags:
    """The five raw signals for one (arm, interval) reading. Booleans
    only - classify() turns these into a verdict."""

    s1_integrity_fail: bool
    s2_implausible: bool
    s3_divergence: bool
    s4_simultaneity: bool
    s5_physical: bool

    def fired(self) -> List[str]:
        names = []
        if self.s1_integrity_fail:
            names.append("S1_INTEGRITY_FAIL")
        if self.s2_implausible:
            names.append("S2_IMPLAUSIBLE")
        if self.s3_divergence:
            names.append("S3_DIVERGENCE")
        if self.s4_simultaneity:
            names.append("S4_SIMULTANEITY")
        if self.s5_physical:
            names.append("S5_PHYSICAL")
        return names


@dataclass(frozen=True)
class ClassificationResult:
    classification: str
    signals: List[str] = field(default_factory=list)
    confidence: str = "n/a"
    action: str = ACTION_NONE


def compute_channel_signals(
    *,
    accepted: bool,
    encryption_enabled: bool,
    reported_vehicles: Optional[int],
    true_vehicles: int,
    green_seconds_window: float,
) -> "_ChannelSignals":
    """S1-S3 only: the signals derivable from ONE channel reading, without
    reference to other arms (S4) or the physical detector (S5). Kept
    separate from SignalFlags/classify() so S4's cross-arm aggregation
    (simultaneity_flag, below) can be computed from these first.
    """
    s1 = (not accepted) and encryption_enabled

    if accepted and reported_vehicles is not None and green_seconds_window >= S2_MIN_GREEN_S:
        physical_bound = green_seconds_window / SATURATION_HEADWAY_S
        s2 = reported_vehicles > physical_bound
    else:
        s2 = False

    if accepted and reported_vehicles is not None:
        s3 = abs(reported_vehicles - true_vehicles) > DIVERGENCE_THRESHOLD_VEHICLES
    else:
        s3 = False

    return _ChannelSignals(s1_integrity_fail=s1, s2_implausible=s2, s3_divergence=s3)


@dataclass(frozen=True)
class _ChannelSignals:
    s1_integrity_fail: bool
    s2_implausible: bool
    s3_divergence: bool

    def any_fired(self) -> bool:
        return self.s1_integrity_fail or self.s2_implausible or self.s3_divergence


def simultaneity_flag(channel_signals_by_arm: Dict[str, "_ChannelSignals"]) -> bool:
    """S4: True if SIMULTANEITY_MIN_ARMS or more arms show any channel-
    level flag (S1/S2/S3) within the same reporting interval. Applies
    uniformly to every arm's classification for that interval - including
    an arm whose own reading has no individual flag, because a
    synchronised pattern across most of the junction is itself the
    anomaly (see module docstring)."""
    flagged = sum(1 for s in channel_signals_by_arm.values() if s.any_fired())
    return flagged >= SIMULTANEITY_MIN_ARMS


def classify(
    *,
    channel: "_ChannelSignals",
    physical_anomaly: bool,
    simultaneity: bool,
    accident_active: bool,
) -> ClassificationResult:
    """Combine S1-S5 into one verdict, in the priority order specified by
    the classification design. Order matters: this is a priority chain,
    not independent votes."""
    flags = SignalFlags(
        s1_integrity_fail=channel.s1_integrity_fail,
        s2_implausible=channel.s2_implausible,
        s3_divergence=channel.s3_divergence,
        s4_simultaneity=simultaneity,
        s5_physical=physical_anomaly,
    )
    fired = flags.fired()

    if flags.s1_integrity_fail:
        return ClassificationResult(
            CLASSIFICATION_CYBER_CONFIRMED, fired,
            "certain (cryptographic proof)", ACTION_NOTIFY_SECURITY)

    if flags.s2_implausible:
        return ClassificationResult(
            CLASSIFICATION_CYBER_LIKELY, fired,
            "high (physically impossible data)", ACTION_NOTIFY_SECURITY)

    if flags.s3_divergence and not flags.s5_physical:
        return ClassificationResult(
            CLASSIFICATION_CYBER_LIKELY, fired,
            "high (reported diverges from reality, no physical cause)",
            ACTION_NOTIFY_SECURITY)

    if flags.s4_simultaneity and not accident_active:
        return ClassificationResult(
            CLASSIFICATION_CYBER_LIKELY, fired,
            "medium (simultaneous multi-arm onset)", ACTION_NOTIFY_SECURITY)

    if flags.s5_physical and not flags.s3_divergence:
        return ClassificationResult(
            CLASSIFICATION_PHYSICAL_INCIDENT, fired,
            "high (queue present, green available, throughput collapsed)",
            ACTION_NOTIFY_TRAFFIC_OPS)

    if flags.s5_physical and flags.s3_divergence:
        return ClassificationResult(
            CLASSIFICATION_AMBIGUOUS, fired,
            "low (both physical and divergence signatures present)",
            ACTION_VERIFY_CAMERA)

    return ClassificationResult(CLASSIFICATION_NORMAL, fired, "high (no signals fired)", ACTION_NONE)

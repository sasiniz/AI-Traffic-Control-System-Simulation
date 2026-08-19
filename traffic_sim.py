"""
===============================================================================
Secure AI-Driven Smart Traffic Control System - Junction Simulation
COM646 Final Year Project
Sasiru Nimsara (C24110002), Wrexham University
===============================================================================

DESIGN RULES THIS FILE ENFORCES
-------------------------------
1. Signal timing is PRE-PLANNED. It never reacts to a live incident.
   This is the core idea of the project: the AI builds a schedule in advance,
   it does not respond in real time. The accident feature must never change
   a light. It only changes the sensor data and the observer camera view.

2. Each of the four through routes is ONE continuous lane from edge to edge.
   Because of this, a queue behind an accident on the far side of the junction
   automatically blocks traffic on the near side. Spillback is not special
   cased. It falls out of the normal car following logic.

3. Vehicles turn left, right, or straight from spawn, using a per-arm split
   (see MOVEMENT_SPLIT). Turning is visual only: vehicles gap-keep along
   their own path and against traffic merging onto the same exit lane, but
   they do not yield to crossing traffic. Because one arm is green at a
   time, this only shows up as a straggler from one phase still clearing
   the box as the next phase's traffic arrives - a known, accepted edge
   case, not something to route around.

RUN
---
    pip install pygame
    python traffic_sim.py

CONTROLS
--------
    A            toggle accident placement mode, then click a road
    X            clear the current accident
    C            toggle the observer camera overlay
    SPACE        pause / resume
    T            cycle TIME (fast-forward) speed 1x -> 5x -> 20x -> 50x -> 1x
                 (dashboard buttons set a level directly; real demand,
                 nothing fabricated - see Section 5)
    E            toggle sensor channel encryption on/off
    F            inject false data attack on the sensor channel
    G            inject sensor spoofing attack on the sensor channel
    H            clear active sensor channel attacks
    S            toggle stealthy magnitude mode for the next F attack
    K            toggle key-compromise mode for the next F/G attack
    D            cycle DENSITY (demo) level 1x -> 10x -> 25x -> 50x -> 1x
                 (dashboard buttons set a level directly; NOT real demand -
                 see Section 5)
    ESC          quit
"""

import csv
import hashlib
import io
import json
import math
import os
import random
from datetime import datetime, timezone

import pygame

from security.crypto import SensorCrypto
from security.channel import SensorChannel, ChannelRejected
from security.attacks import FalseDataInjectionAttack, SensorSpoofingAttack
from security.auth import DEFAULT_CREDENTIALS_PATH, OperatorAuth
from security.approval import ApprovalRecord, append_approval
from security import detection as threat_detection

# =============================================================================
# SECTION 1 - LAYOUT CONSTANTS
# =============================================================================
# The window is split into three vertical zones. They must add up exactly to
# the window width, otherwise every position downstream drifts.

WIDTH, HEIGHT = 1280, 720

PANEL_W = 220                    # left control panel  : x 0    -> 220
DASH_W = 260                     # right dashboard     : x 1020 -> 1280
SIM_X0 = PANEL_W                 # 220
SIM_X1 = WIDTH - DASH_W          # 1020
SIM_W = SIM_X1 - SIM_X0          # 800

# The junction cross is centred inside the middle zone, not the whole window.
CX = SIM_X0 + SIM_W // 2         # 620
CY = HEIGHT // 2                 # 360

ROAD_HALF = 70                   # half the road width, so each road is 140 wide
LANE_OFFSET = 35                 # lane centre distance from the road centreline

# Sensor channel panel (right dashboard) and its four buttons. Defined ONCE
# here so the renderer and main()'s MOUSEBUTTONDOWN hit-testing read the same
# rects - unlike the left panel's TRIGGER ACCIDENT / CLEAR ACCIDENT / CAMERAS
# buttons, whose drawing y-values (300, 348, 396) and click hit-test ranges
# are two independent hardcoded copies that can silently drift apart. Do not
# replicate that here; do not "fix" the left panel in this same change.
SEC_PANEL_X = SIM_X1 + 16        # 1036
SEC_PANEL_Y = 380
SEC_PANEL_W = 212
# Grown 210->218 (bottom 590->598) to fit the new TIME/DENSITY row labels
# below - measured empirically via rendering, not assumed - while staying
# 2px clear of the THREAT STATUS box, which is unrelated to this constant
# and still starts at its own fixed y=600 in draw_dashboard.
SEC_PANEL_H = 218

# A 2-column grid of compact buttons, not full-width 38px buttons: fifteen
# full-width buttons, a title, TWO row-identifying labels ("TIME" /
# "DENSITY" - see draw_dashboard) and a reading list do not fit in ~210px
# of panel height even after shrinking SEC_BTN_H 30->24->20->18 and row
# gaps 6->4->3->2. RECENT READINGS is reduced accordingly (see
# draw_dashboard) - this phase does not require preserving its earlier
# >=3 row count, only that it does not overlap the THREAT STATUS box.
SEC_BTN_W = 92
SEC_BTN_H = 18
_sec_btn_col0 = SEC_PANEL_X + 10
_sec_btn_col1 = _sec_btn_col0 + SEC_BTN_W + 8
_sec_btn_row0 = SEC_PANEL_Y + 26
_sec_btn_row1 = _sec_btn_row0 + SEC_BTN_H + 2
_sec_btn_row2 = _sec_btn_row1 + SEC_BTN_H + 2
_LEVEL_LABEL_H = 14   # vertical room reserved for the "TIME"/"DENSITY" caption above each level row
_sec_btn_row3 = _sec_btn_row2 + SEC_BTN_H + 4 + _LEVEL_LABEL_H   # TIME level row (+ its caption)
_sec_btn_row4 = _sec_btn_row3 + SEC_BTN_H + 4 + _LEVEL_LABEL_H   # DENSITY level row (+ its caption)

SEC_BUTTONS = {
    "encryption":    pygame.Rect(_sec_btn_col0, _sec_btn_row0, SEC_BTN_W, SEC_BTN_H),
    "false_data":    pygame.Rect(_sec_btn_col1, _sec_btn_row0, SEC_BTN_W, SEC_BTN_H),
    "spoof":         pygame.Rect(_sec_btn_col0, _sec_btn_row1, SEC_BTN_W, SEC_BTN_H),
    "clear_attacks": pygame.Rect(_sec_btn_col1, _sec_btn_row1, SEC_BTN_W, SEC_BTN_H),
    # Mode toggles: change how the NEXT press of false_data/spoof builds its
    # attack object (see main()'s sec_actions), rather than firing an attack
    # themselves.
    "toggle_stealth":       pygame.Rect(_sec_btn_col0, _sec_btn_row2, SEC_BTN_W, SEC_BTN_H),
    "toggle_key_compromise": pygame.Rect(_sec_btn_col1, _sec_btn_row2, SEC_BTN_W, SEC_BTN_H),
}

# TIME and DENSITY level buttons: one row of four each. Labels are the
# single source of truth for both the button keys below and main()'s
# sec_actions mapping to SPEED_LEVELS / DEMAND_LEVELS (Section 5) - each
# must stay in the same index order as its tuple (1x -> levels[0], etc.).
# Shared width/gap for both rows, computed from the panel's usable row
# width (SEC_PANEL_W minus the same 10px margins used by the 2-column
# grid above), not guessed: measured via pygame font.size() at f_small,
# the widest label across both rows ("20x"/"25x"/"50x") renders at 17px,
# so LEVEL_BTN_W (43px) leaves ~26px of padding either side of the text -
# comfortably sufficient.
TIME_BUTTON_LABELS = ("1x", "5x", "20x", "50x")        # order matches SPEED_LEVELS
DENSITY_BUTTON_LABELS = ("1x", "10x", "25x", "50x")    # order matches DEMAND_LEVELS
LEVEL_BTN_GAP = 6
LEVEL_BTN_W = (SEC_PANEL_W - 20 - 3 * LEVEL_BTN_GAP) // 4  # 43

for _i, _label in enumerate(TIME_BUTTON_LABELS):
    SEC_BUTTONS[f"time_{_label}"] = pygame.Rect(
        _sec_btn_col0 + _i * (LEVEL_BTN_W + LEVEL_BTN_GAP),
        _sec_btn_row3, LEVEL_BTN_W, SEC_BTN_H)
for _i, _label in enumerate(DENSITY_BUTTON_LABELS):
    SEC_BUTTONS[f"density_{_label}"] = pygame.Rect(
        _sec_btn_col0 + _i * (LEVEL_BTN_W + LEVEL_BTN_GAP),
        _sec_btn_row4, LEVEL_BTN_W, SEC_BTN_H)
del _i, _label

# =============================================================================
# SECTION 2 - TRAFFIC SIDE AND LANE POSITIONS
# =============================================================================
# IMPORTANT ACADEMIC NOTE
# Sri Lanka drives on the LEFT. This project is motivated by Kesbewa junction
# in Sri Lanka, so "left" is arguably the correct setting for the report.
# The earlier design work used right hand traffic, so that is kept as the
# default here. Changing this one string flips every lane correctly.

DRIVE_SIDE = "right"             # "right" or "left"

if DRIVE_SIDE == "right":
    # Facing south, west is on your right, so southbound uses the west half.
    LANE_X_SOUTHBOUND = CX - LANE_OFFSET     # 585
    LANE_X_NORTHBOUND = CX + LANE_OFFSET     # 655
    LANE_Y_WESTBOUND = CY - LANE_OFFSET      # 325
    LANE_Y_EASTBOUND = CY + LANE_OFFSET      # 395
else:
    LANE_X_SOUTHBOUND = CX + LANE_OFFSET
    LANE_X_NORTHBOUND = CX - LANE_OFFSET
    LANE_Y_WESTBOUND = CY + LANE_OFFSET
    LANE_Y_EASTBOUND = CY - LANE_OFFSET

# =============================================================================
# SECTION 3 - VEHICLE AND MOVEMENT CONSTANTS
# =============================================================================

VEH_LEN = 30                     # length along the direction of travel
VEH_WID = 18                     # width across the lane

MAX_SPEED = 2.2                  # pixels per frame at 60 fps
CRAWL_SPEED = 0.10               # speed when easing past an accident
                                 # Tuned so pinch capacity (CRAWL/MIN_GAP) sits
                                 # well BELOW peak demand, otherwise a crawl
                                 # causes no queue and no anomaly to detect.
MIN_GAP = 48                     # minimum centre to centre spacing in a queue

# Minimum time headway this simulation's own car-following physics
# permits, in seconds: the time for one MIN_GAP of following distance to
# close at MAX_SPEED (px/frame, at 60 fps). Passed into
# security/detection.py's S2 IMPLAUSIBLE check as sim_saturation_headway_s
# - NOT the same as the Highway Capacity Manual's 1.9s real-world figure
# (detection.py's HCM_SATURATION_HEADWAY_S), which is far more permissive
# in the wrong direction here: this simulation can legitimately produce
# tighter headways than real traffic ever could, so judging simulated
# readings against the HCM figure flags genuine traffic as implausible.
# See detection.py's "WHICH HEADWAY S2 USES" for the measured comparison.
# Computed here, not duplicated as a second literal in detection.py, so
# the two files cannot silently drift apart if MIN_GAP or MAX_SPEED ever
# change - detection.py stays import-free of this module by design, so
# the value is passed as a parameter at the call site (Simulation._classify)
# instead.
SIM_SATURATION_HEADWAY_S = MIN_GAP / (MAX_SPEED * 60.0)  # 48/(2.2*60) = 0.3636s

GAP_RAMP = 60                    # distance over which speed ramps back up
STOP_MARGIN = 4                  # how close the front bumper gets to a stop line
STILL_BLOCKING_ZONE = VEH_LEN + MIN_GAP
                                 # how far past the stop line a turning
                                 # vehicle still counts as blocking the
                                 # approach lane behind it

ACCEL = 0.06                     # speeding up is gentle
DECEL = 0.30                     # slowing down is firmer

SLOW_ZONE = 70                   # distance either side of an accident to crawl
LATERAL_NUDGE = 16               # sideways shift so vehicles ease around debris

POLICE_DELAY_S = 8.0             # seconds after the accident before police arrive
POLICE_SPEED = 3.0

# =============================================================================
# SECTION 4 - ROUTE DEFINITIONS
# =============================================================================
# Each route is a straight line across the whole simulation zone.
#   axis  : which coordinate changes as the vehicle moves
#   dir   : +1 means the coordinate increases, -1 means it decreases
#   lane  : the fixed coordinate on the other axis (the lane centreline)
#   start : spawn coordinate, just outside the visible zone
#   end   : despawn coordinate, just outside the far side
#   stop  : the stop line coordinate at the edge of the junction box
#   arm   : which road arm the vehicle approaches on

ROUTES = {
    "NS": {  # from North, heading South
        "axis": "y", "dir": +1, "lane": LANE_X_SOUTHBOUND,
        "start": -60, "end": HEIGHT + 60, "stop": CY - ROAD_HALF,
        "arm": "North",
    },
    "SN": {  # from South, heading North
        "axis": "y", "dir": -1, "lane": LANE_X_NORTHBOUND,
        "start": HEIGHT + 60, "end": -60, "stop": CY + ROAD_HALF,
        "arm": "South",
    },
    "EW": {  # from East, heading West
        "axis": "x", "dir": -1, "lane": LANE_Y_WESTBOUND,
        "start": SIM_X1 + 60, "end": SIM_X0 - 60, "stop": CX + ROAD_HALF,
        "arm": "East",
    },
    "WE": {  # from West, heading East
        "axis": "x", "dir": +1, "lane": LANE_Y_EASTBOUND,
        "start": SIM_X0 - 60, "end": SIM_X1 + 60, "stop": CX - ROAD_HALF,
        "arm": "West",
    },
}

ARMS = ["North", "South", "East", "West"]
ARM_ROUTE = {"North": "NS", "South": "SN", "East": "EW", "West": "WE"}

# Turning. Movements are assigned per vehicle at spawn using its approach's
# split below. Percentages are relative weights, not required to sum to 100.
# Edit freely per arm.
MOVEMENT_SPLIT = {
    "North": {"straight": 55, "left": 25, "right": 20},
    "South": {"straight": 60, "left": 20, "right": 20},
    "East":  {"straight": 50, "left": 30, "right": 20},
    "West":  {"straight": 55, "left": 20, "right": 25},
}

# Which route's outbound lane a turn joins. A right turn is always the next
# compass direction clockwise from the approach heading, left is the next
# one counter-clockwise. This is pure geometry and does not depend on
# DRIVE_SIDE - only the lane coordinates looked up from these routes do.
TURN_EXIT = {
    "NS": {"left": "WE", "right": "EW"},
    "SN": {"left": "EW", "right": "WE"},
    "EW": {"left": "NS", "right": "SN"},
    "WE": {"left": "SN", "right": "NS"},
}


def _choose_movement(arm):
    split = MOVEMENT_SPLIT[arm]
    return random.choices(list(split.keys()), weights=list(split.values()), k=1)[0]


def _box_edge(axis, direction):
    """The junction box edge crossed when travelling this axis/direction."""
    if axis == "y":
        return CY + ROAD_HALF if direction > 0 else CY - ROAD_HALF
    return CX + ROAD_HALF if direction > 0 else CX - ROAD_HALF


def _bezier_point(p0, c, p2, t):
    mt = 1.0 - t
    x = mt * mt * p0[0] + 2 * mt * t * c[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * c[1] + t * t * p2[1]
    return x, y


def _bezier_tangent(p0, c, p2, t):
    mt = 1.0 - t
    dx = 2 * mt * (c[0] - p0[0]) + 2 * t * (p2[0] - c[0])
    dy = 2 * mt * (c[1] - p0[1]) + 2 * t * (p2[1] - c[1])
    return dx, dy


def _bezier_length(p0, c, p2, samples=16):
    """Approximate arc length by summing short straight segments."""
    prev = p0
    total = 0.0
    for i in range(1, samples + 1):
        t = i / samples
        pt = _bezier_point(p0, c, p2, t)
        total += math.hypot(pt[0] - prev[0], pt[1] - prev[1])
        prev = pt
    return total

# =============================================================================
# SECTION 5 - PRE-PLANNED SIGNAL SCHEDULE
# =============================================================================
# The signal program is not computed here. It is loaded, already fully
# spelled out phase by phase, from SIGNAL_TIMELINE_PATH - the CSV produced
# by generate_timeline.py's compile_timeline(). Each row is one phase: a
# road, its green seconds, and its amber seconds, laid out so every hour of
# real schedule sums to exactly 3600 seconds. When the Random Forest
# scheduler is ready, replacing the CSV at this path with its real weekly
# output is the only change needed - nothing in SignalController changes.
#
# Playback PACING is independent of the simulation's own clock (START_HOUR
# and elapsed sim_time): it starts at the first row and loops back to the
# start once the file is exhausted, stepping forward purely by elapsed dt,
# never by matching wall-clock time to the CSV's dates. The CSV's date and
# start_hour columns ARE read and surfaced for DISPLAY (the control panel's
# "SCHEDULE SOURCE" line - SECTION 15), so a viewer can see which real dated
# artefact is currently playing, but nothing about playback timing or
# sequencing depends on them.

SIGNAL_TIMELINE_PATH = os.path.join(os.path.dirname(__file__),
                                    "signal_timeline.csv")

# The file the operator approval gate (main(), Section 15) hashes and
# asks a human to accept - the PLAN (predicted counts + green seconds per
# hour/road), not SIGNAL_TIMELINE_PATH above. generate_timeline.py's
# compile_timeline() expands the plan into the phase-by-phase timeline
# deterministically; approving the expansion would be approving a
# derived artefact, not the authored one - see DECISIONS.md's approval
# ADR. THIS IS THE ONE LINE TO CHANGE when the annual plan
# (data/signal_schedule_plan_annual.csv, Phase B - not yet built) lands;
# nothing else about the approval gate needs to change.
APPROVAL_TARGET_PATH = os.path.join(os.path.dirname(__file__),
                                    "signal_schedule_plan.csv")

# Read for display only (provenance text in the approval modal) - never
# hashed, never part of what is approved. Missing or unreadable is
# handled explicitly (_read_model_provenance), not treated as fatal.
MODEL_CARD_PATH = os.path.join(os.path.dirname(__file__),
                               "models", "model_card.json")

# Approval modal review pane (Section 15) - rows shown at once from the
# pivoted (one row per hour) view of the plan. "~14" per the brief; the
# modal's box_h is sized around this exact value, so change both together.
APPROVAL_PANE_VISIBLE_ROWS = 14


def _load_signal_timeline(path):
    """Read a generate_timeline.py CSV into a list of phase dicts."""
    phases = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            phases.append({
                "road": row["road"],
                "green": float(row["green_seconds"]),
                "amber": float(row["amber_seconds"]),
                "date": row["date"],
                "start_hour": int(row["start_hour"]),
            })
    if not phases:
        raise ValueError(f"{path} has no phase rows")
    return phases


def _parse_plan_rows(raw_bytes):
    """Parses signal_schedule_plan.csv bytes (one row per hour/road) into
    row dicts. The approval modal's SHA-256, its plan-summary line and its
    review pane all derive from ONE read of these exact bytes (see
    _run_approval_gate) - a second, independent open() of the same file
    could return different content (e.g. a regenerate mid-view) and
    silently make the hash and the table describe two different files.
    See DECISIONS.md's review-pane ADR."""
    rows = []
    for row in csv.DictReader(io.StringIO(raw_bytes.decode("utf-8"))):
        rows.append({
            "hour": int(row["hour"]),
            "road": row["road"],
            "predicted_count": float(row["predicted_count"]),
            "green_seconds": int(row["green_seconds"]),
        })
    return rows


def _plan_summary_from_rows(rows):
    """Row count and hour range from already-parsed plan rows - NOT
    calendar dates: the plan file is an hour-of-week template (0-167, one
    row per road per hour; see ADR-012's weekly regeneration horizon), not
    a dated artefact - calendar dates only enter via SIGNAL_TIMELINE_PATH's
    playback, a deliberately separate, deterministic expansion. Returns
    None for an empty plan - the approval modal must show that plainly,
    not crash on a missing or malformed plan.
    """
    if not rows:
        return None
    hours = [r["hour"] for r in rows]
    return {"row_count": len(rows), "min_hour": min(hours), "max_hour": max(hours)}


def _read_plan_summary(path):
    """Convenience wrapper for callers that only need the summary line and
    do not need to share a read with a SHA-256 (e.g. a standalone display
    call). _run_approval_gate itself does NOT use this - it reads the plan
    file once and calls _parse_plan_rows/_plan_summary_from_rows directly
    on those same bytes, so the hash, the summary line and the review pane
    can never describe three different reads of the file."""
    try:
        with open(path, "rb") as fh:
            rows = _parse_plan_rows(fh.read())
    except (OSError, KeyError, ValueError):
        return None
    return _plan_summary_from_rows(rows)


def _pivot_plan_rows(rows, roads=ARMS):
    """One row per hour, one column per road (green seconds) - the review
    pane's PIVOTED view of the underlying one-row-per-(hour,road) plan.
    Built from the same already-parsed `rows` the caller read once; never
    re-reads or re-derives anything from disk itself. A road missing for
    some hour renders as None rather than raising, so a malformed plan is
    visible in the pane (a blank cell) instead of crashing the gate."""
    by_hour = {}
    for r in rows:
        by_hour.setdefault(r["hour"], {})[r["road"]] = r["green_seconds"]
    return [
        {"hour": hour, **{road: by_hour[hour].get(road) for road in roads}}
        for hour in sorted(by_hour)
    ]


def _read_model_provenance(path):
    """Short summary string built from model_card.json, for the approval
    modal's display only - never hashed, never part of what is approved.
    A missing or malformed model card must not block approval or crash
    the modal; it is exactly the kind of "provenance unavailable" state
    an operator should be able to see and still make a decision about."""
    try:
        with open(path) as fh:
            card = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return "model_card.json not found"
    n_estimators = card.get("n_estimators", "?")
    max_depth = card.get("max_depth", "?")
    target_mode = card.get("target_mode", "?")
    split_date = card.get("split_date", "?")
    return (f"RandomForest, {n_estimators} trees, depth {max_depth}, "
            f"target={target_mode}, trained before {split_date}")


# Arrivals per hour per arm, by hour of day. Real demand: mean Vehicles per
# (Road, hour-of-day) across the full data/traffic_final_cleaned.csv, rounded
# to the nearest vehicle. Verified independently against the dataset before
# use (grouped by road+hour, mean, round) - exact match, no discrepancy.
# Replaces the earlier fabricated placeholder (North peaked at 780 vs a real
# mean of ~57; see Phase 0 commit message for the full before/after).
HOURLY_DEMAND = {
    "North": [46, 39, 34, 29, 26, 24, 26, 30, 33, 39, 50, 56,
              57, 51, 55, 54, 52, 52, 55, 59, 57, 55, 53, 50],
    "South": [16, 14, 13, 11, 10,  9,  9, 10, 11, 12, 13, 15,
              16, 15, 16, 17, 16, 16, 17, 18, 18, 17, 17, 16],
    "East":  [14, 10,  8,  7,  6,  6,  6,  8,  9, 11, 15, 17,
              18, 16, 18, 17, 17, 17, 18, 19, 20, 19, 17, 16],
    "West":  [ 7,  6,  5,  4,  4,  4,  4,  5,  5,  6,  8,  9,
              11,  9,  9,  9,  9,  9,  8,  9,  9,  8,  9,  8],
}

START_HOUR = 8                   # simulation clock starts at 08:00

# Presentation parameter only. 1.0 is real dataset demand (HOURLY_DEMAND
# above, verified against data/traffic_final_cleaned.csv - see Phase 0).
# Any value != 1.0 must NEVER be used to produce a reported result - it
# exists solely so a demo can show physical-signal (S5) behaviour that
# real demand structurally cannot reach: pinch capacity at CRAWL_SPEED is
# 450 veh/h against a peak real arm demand of 59 veh/h, so a lane blockage
# cannot build a persistent queue at multiplier 1.0 (see DECISIONS.md /
# this session's Phase 0 report for the arithmetic). Toggled live via the
# D key; never edit this constant to "make a demo work".
#
# Per-arm saturation multiplier - the level at which demand first exceeds
# that arm's own capacity - measured from signal_timeline.csv at each
# arm's own peak hour, not assumed: capacity_veh_per_h = (mean_green_s *
# cycles_per_hour) / SATURATION_HEADWAY_S (1.9s, HCM, same citation as
# ADR-006/ADR-021), cycles_per_hour = 3600/120 = 30.
#   North (peak hr 19): mean_green=46.43s  capacity=733.1  demand=59   -> 12.43x
#   South (peak hr 19): mean_green=24.14s  capacity=381.2  demand=18   -> 21.18x
#   East  (peak hr 20): mean_green=20.86s  capacity=329.3  demand=20   -> 16.47x
#   West  (peak hr 12): mean_green=18.43s  capacity=291.0  demand=11   -> 26.45x
# So of DEMAND_LEVELS below: 1x is never oversaturated by definition; 10x
# is oversaturated on NO arm (10 < every multiplier above); 25x IS
# oversaturated on North, South and East (25 > 12.43/21.18/16.47) but not
# West (25 < 26.45); 50x is oversaturated on EVERY arm (50 > 26.45, the
# largest). This is why higher levels are expected to show blocked-at-
# entry counts climbing - see this session's R2 review evidence.
#
# WARNING - above roughly 12x (North's own measured saturation multiplier,
# the LOWEST of the four arms above, so the first to break) the junction
# is oversaturated by construction, not by any simulated incident: S5
# fires from raw capacity limits rather than a blockage, and S2 fires more
# often because bursts of genuinely-arriving vehicles routinely exceed the
# idealised continuous-flow HCM bound at these demand levels. 10x is
# therefore the highest DEMAND_LEVELS entry at which the detectors remain
# meaningful - i.e. still distinguishing an incident from ordinary
# (if dense) traffic. 25x and 50x exist ONLY to force queue formation for
# demonstration purposes; a PHYSICAL_INCIDENT or a false positive produced
# at those levels is not evidence about detector quality, it is evidence
# that the junction has been deliberately oversaturated.
DEMAND_MULTIPLIER = 1.0
DEMAND_LEVELS = (1.0, 10.0, 25.0, 50.0)  # order must match DENSITY_BUTTON_LABELS (Section 1)

# Honest fast-forward: scales the SIMULATION CLOCK only (how much sim-time
# elapses per rendered frame, via sub-stepping - see main()). Unlike
# DEMAND_MULTIPLIER, this fabricates nothing - HOURLY_DEMAND, CRAWL_SPEED,
# MIN_GAP and every other physical constant are untouched, so a result
# recorded at any SPEED_LEVELS value is exactly as real as one recorded at
# 1x, just observed faster. This is the preferred way to see more traffic
# in a shorter viewing session; DEMAND_LEVELS should only be reached for
# when TIME alone is not enough (e.g. forcing a queue for an S5 demo).
SPEED_LEVELS = (1, 5, 20, 50)  # order must match TIME_BUTTON_LABELS (Section 1)

# Sub-stepping cap (main()): at SPEED_LEVELS' maximum (50) with dt~=1/60,
# one rendered frame needs ~50 physics sub-steps of dt=1/60 each to cover
# 50/60s of simulated time without ever using a larger sub-step (see the
# bug this replaces, verified this session: at speed=50 with a single
# dt*speed step, MAX_SPEED*50=110px > MIN_GAP=48px, so a vehicle can pass
# through another or a stop line within one update). 120 gives a ~2.4x
# margin above that for clock jitter without ever growing sub-step size -
# if a frame still needs more, the excess simulated time carries over to
# the NEXT rendered frame instead, which is what reduces the achieved
# framerate rather than corrupting physics.
MAX_SUBSTEPS_PER_FRAME = 120

# =============================================================================
# SECTION 6 - SENSOR AND ANOMALY CONSTANTS
# =============================================================================

WINDOW_S = 20.0                  # rolling window used for anomaly checks
QUEUE_SPEED_THRESHOLD = 0.4      # below this a vehicle counts as queueing

# ANOMALY_QUEUE_MIN was tuned against the old, fabricated HOURLY_DEMAND
# (North peaking at 780/hr). At the corrected real demand (North peaking at
# ~59/hr) it essentially never fires: measured across 90 sim-minutes headless
# (30 min default-hour no accident, 30 min peak-hour no accident, 60 min
# peak-hour WITH a real accident on North from t=60s) - zero frames anywhere
# reached queue_len >= 5. Max queue observed, including during the hour-long
# accident, was 4. This is a dataset finding, not a bug: real demand runs far
# below saturation (busiest single hour across all four approaches is 353
# vehicles; degree of saturation ~0.25), so a 5-vehicle physical queue is a
# rare event this simulation's real-demand regime may not produce at all.
# Left unchanged - only ANOMALY_RATE_MIN was in scope to recalibrate (see
# below) - but recorded here so a future change is not made blind.
ANOMALY_QUEUE_MIN = 5            # a queue must exist before anything is odd
ANOMALY_MIN_GREEN_S = 6.0        # need enough green time to judge fairly

# Recalibrated against real demand (see HOURLY_DEMAND above), not the old
# fabricated levels 0.22 was tuned against. Measured headless, peak hour
# (North=59/hr), WINDOW_S=20s trailing rate = discharges / green_s:
#   "normal, any queue present, green available": 717 qualifying frames over
#     30 sim-min, rate = 0.0000 for EVERY one of them (min=p50=p90=max=0.0).
#     At the production gate (queue_len >= ANOMALY_QUEUE_MIN = 5) there were
#     ZERO qualifying frames at all - see the ANOMALY_QUEUE_MIN comment above.
#   "accident-induced blockage on North, any queue present, green available,
#     accident active": 31885 qualifying frames over 60 sim-min (accident
#     placed at t=60s), rate min=0.0, p10=0.0, median=0.0999, p90=0.15,
#     max=0.2198.
# HONEST LIMITATION: these two distributions are NOT cleanly separable by
# rate alone at real demand - the "normal" distribution is degenerate (always
# exactly 0.0, because a real queue is so rare and brief that the 20s window
# usually contains no discharges yet regardless of health), while blockage
# frames, being far more numerous and persistent, actually show a HIGHER
# typical rate than the rare normal frames. In other words: at real demand,
# ANOMALY_QUEUE_MIN=5 is doing all of the false-positive prevention, not this
# constant - "sits clearly below normal but above blockage" could not be
# satisfied because normal and blockage overlap at rate 0.0. Chosen value:
# the empirical CDF of the blockage distribution shows threshold 0.15 flags
# 88.7% of blockage frames as anomalous (0.10 flags 50.2%, 0.05 flags 29.5%,
# 0.02 flags 27.2%) while staying clearly under the observed blockage max of
# 0.2198, so a genuine sustained blockage is very likely to be flagged once
# the queue gate is ever satisfied, without flagging literally every frame.
ANOMALY_RATE_MIN = 0.15          # vehicles discharged per second OF GREEN

ENABLE_LOGGING = True
LOG_PATH = "sensor_log.csv"
LOG_INTERVAL_S = 10.0

CHANNEL_LOG_CAP = 20             # entries kept for the dashboard's channel log

# =============================================================================
# SECTION 7 - COLOURS
# =============================================================================

C_BG = (18, 20, 24)
C_PANEL = (28, 31, 38)
C_CARD = (38, 42, 51)
C_GRASS = (32, 40, 34)
C_ROAD = (58, 62, 70)
C_LINE = (150, 155, 165)
C_JUNC = (66, 70, 79)
C_TEXT = (232, 235, 240)
C_MUTED = (150, 156, 168)
C_GREEN = (64, 190, 110)
C_AMBER = (232, 170, 60)
C_RED = (222, 78, 78)
C_BLUE = (86, 150, 232)
C_WHITE = (245, 245, 250)

VEHICLE_COLOURS = [
    (196, 201, 212), (120, 168, 220), (208, 140, 110),
    (150, 190, 160), (200, 180, 120), (170, 150, 200),
]

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"


# =============================================================================
# SECTION 8 - SIGNAL CONTROLLER
# =============================================================================
class SignalController:
    """
    Plays back the pre-planned phase list loaded from SIGNAL_TIMELINE_PATH.

    Exactly one arm is green at a time - that invariant comes from how
    generate_timeline.py compiles the file, not from anything checked here.
    This class only ever steps forward through the list it was given; it
    does not compute timings itself.

    This class deliberately has NO reference to accidents or queues.
    That is the whole point of the project design. If a future version ever
    needs the lights to react, it would be a different controller class, not
    a change here.
    """

    def __init__(self, timeline_path=SIGNAL_TIMELINE_PATH):
        self.timeline = _load_signal_timeline(timeline_path)
        self.phase_pos = 0             # index into self.timeline
        self.phase = self.timeline[0]["road"]   # which arm currently has green
        self.in_amber = False
        self.timer = 0.0

    def update(self, dt):
        self.timer += dt
        current = self.timeline[self.phase_pos]
        if self.in_amber:
            if self.timer >= current["amber"]:
                self.timer = 0.0
                self.in_amber = False
                self.phase_pos = (self.phase_pos + 1) % len(self.timeline)
                self.phase = self.timeline[self.phase_pos]["road"]
        else:
            if self.timer >= current["green"]:
                self.timer = 0.0
                self.in_amber = True

    def state(self, arm):
        """Return GREEN, AMBER or RED for a road arm."""
        if arm != self.phase:
            return RED
        return AMBER if self.in_amber else GREEN

    def current_datetime_label(self):
        """(date, start_hour) of the phase currently playing, as loaded
        from the timeline file - for the dashboard only (SECTION 15). Not
        used anywhere in signal timing itself."""
        current = self.timeline[self.phase_pos]
        return current["date"], current["start_hour"]

    def remaining(self, arm):
        """Seconds left until this arm's state next changes, for the panel."""
        current = self.timeline[self.phase_pos]
        if arm == self.phase:
            if self.in_amber:
                return max(0.0, current["amber"] - self.timer)
            return max(0.0, current["green"] - self.timer)
        # Red: time until this arm's own turn comes around the timeline.
        if self.in_amber:
            t = max(0.0, current["amber"] - self.timer)
        else:
            t = max(0.0, current["green"] - self.timer) + current["amber"]
        idx = (self.phase_pos + 1) % len(self.timeline)
        for _ in range(len(self.timeline)):
            phase = self.timeline[idx]
            if phase["road"] == arm:
                break
            t += phase["green"] + phase["amber"]
            idx = (idx + 1) % len(self.timeline)
        return t


# =============================================================================
# SECTION 9 - ACCIDENT
# =============================================================================
class Accident:
    """
    A blockage placed by the operator.

    scope is either a route key ("NS", "SN", "EW", "WE") meaning the accident
    sits in that one lane, or "JUNCTION" meaning it sits in the shared centre
    and therefore affects every route.
    """

    def __init__(self, scope, x, y):
        self.scope = scope
        self.x = x
        self.y = y
        self.age = 0.0
        self.police_arrived = False

    # -- geometry helpers ---------------------------------------------------
    def affects(self, route):
        return self.scope == "JUNCTION" or self.scope == route

    def position_on(self, route):
        """The accident coordinate measured along the given route's axis."""
        return self.y if ROUTES[route]["axis"] == "y" else self.x

    # -- reporting helpers --------------------------------------------------
    def arm(self):
        """Which road arm the accident sits on, for the dashboard and cameras."""
        if self.scope == "JUNCTION":
            return "Centre"
        if ROUTES[self.scope]["axis"] == "y":
            return "North" if self.y < CY - ROAD_HALF else "South"
        return "West" if self.x < CX - ROAD_HALF else "East"

    def side(self):
        """Incoming means before the junction, outgoing means after it."""
        if self.scope == "JUNCTION":
            return "junction centre"
        r = ROUTES[self.scope]
        pos = self.position_on(self.scope)
        # Before the stop line along the direction of travel = incoming.
        return "incoming lane" if r["dir"] * (pos - r["stop"]) < 0 else "outgoing lane"

    def label(self):
        if self.scope == "JUNCTION":
            return "Junction centre"
        return f"{self.arm()} arm, {self.side()}"


# =============================================================================
# SECTION 10 - VEHICLE
# =============================================================================
class Vehicle:
    """
    A single car travelling one path: an approach segment in its entry
    lane, then either straight through or a turn arc through the junction,
    then an exit segment in the correct lane of the road it leaves by.

    `linear_progress` is a phase-aware position value, comparable only
    against other vehicles Simulation._leaders() has grouped with this one
    (same physical lane, or for the arc the exact same curve). It replaces
    the old single axis-based `progress` so that gap based car following
    never compares vehicles that are no longer sharing the same tarmac.
    """

    _next_id = 1

    def __init__(self, route, movement):
        self.route = route
        self.movement = movement            # "straight", "left" or "right"
        self.turning = movement != "straight"
        r = ROUTES[route]
        self.entry_axis = r["axis"]
        self.entry_dir = r["dir"]
        self.entry_lane = r["lane"]
        self.entry_stop = r["stop"]
        self.start = r["start"]

        self.exit_route = route if not self.turning else TURN_EXIT[route][movement]
        er = ROUTES[self.exit_route]
        self.exit_axis = er["axis"]
        self.exit_dir = er["dir"]
        self.exit_lane = er["lane"]
        self.exit_end = er["end"]

        if self.turning:
            self.p0 = ((self.entry_lane, self.entry_stop) if self.entry_axis == "y"
                      else (self.entry_stop, self.entry_lane))
            edge = _box_edge(self.exit_axis, self.exit_dir)
            self.p2 = ((self.exit_lane, edge) if self.exit_axis == "y"
                      else (edge, self.exit_lane))
            self.corner = ((self.entry_lane, self.exit_lane) if self.entry_axis == "y"
                          else (self.exit_lane, self.entry_lane))
            self.arc_length = _bezier_length(self.p0, self.corner, self.p2)

        self.pos = float(self.start)        # valid while phase == "approach"
        self.exit_pos = None                # valid while phase == "exit"
        self.arc_t = 0.0                    # valid while phase == "arc"
        self.phase = "approach"
        self.speed = MAX_SPEED
        self.colour = random.choice(VEHICLE_COLOURS)
        self.lateral = 0.0           # sideways offset used to ease past debris
        self.counted_out = False     # has it already been counted at the stop line
        self.id = Vehicle._next_id
        Vehicle._next_id += 1

    # -- derived geometry ---------------------------------------------------
    def screen_xy(self):
        if self.phase == "approach":
            if self.entry_axis == "y":
                return self.entry_lane + self.lateral, self.pos
            return self.pos, self.entry_lane + self.lateral
        if self.phase == "arc":
            return _bezier_point(self.p0, self.corner, self.p2, self.arc_t)
        if self.exit_axis == "y":
            return self.exit_lane + self.lateral, self.exit_pos
        return self.exit_pos, self.exit_lane + self.lateral

    def heading_deg(self):
        """Drawing rotation, only meaningful mid arc."""
        dx, dy = _bezier_tangent(self.p0, self.corner, self.p2, self.arc_t)
        return -math.degrees(math.atan2(dy, dx))

    def entry_axis_coord(self):
        """Current position projected onto the entry lane's own axis. Valid
        while approaching, while turning (via the live arc position), or
        for a straight vehicle just past the line (same axis as entry)."""
        if self.phase == "approach":
            return self.pos
        if self.turning:
            x, y = _bezier_point(self.p0, self.corner, self.p2, self.arc_t)
            return y if self.entry_axis == "y" else x
        return self.exit_pos

    @property
    def linear_progress(self):
        """A value that increases as the vehicle moves forward, valid for
        comparison only against vehicles in the same Simulation._leaders()
        group (same physical lane).

        While turning, this is the arc projected onto the exit lane's own
        coordinate scale rather than a bare 0..1 fraction, so a vehicle
        approaching the end of its arc is directly comparable to whatever
        is already on the exit lane - including traffic that turned in from
        a different approach. It is continuous across the arc-to-exit
        transition (equal to exit_dir*exit_pos exactly when arc_t reaches
        1.0), which is what stops two vehicles finishing a turn moments
        apart from being reset onto the same point and overlapping.
        """
        if self.phase == "approach":
            return self.entry_dir * self.pos
        if self.phase == "exit":
            return self.exit_dir * self.exit_pos
        edge = _box_edge(self.exit_axis, self.exit_dir)
        return self.exit_dir * edge + (self.arc_t - 1.0) * self.arc_length

    def past_stop_line(self):
        """True once the approach segment is complete and the vehicle has
        committed to the junction (arc or exit phase)."""
        return self.phase != "approach"

    def dist_to_stop_line(self):
        return self.entry_dir * (self.entry_stop - self.pos) - VEH_LEN / 2.0

    def finished(self):
        if self.phase != "exit":
            return False
        return self.exit_dir * (self.exit_pos - self.exit_end) >= 0

    # -- speed decisions ----------------------------------------------------
    @staticmethod
    def _ramp(distance, threshold):
        """Smoothly scale speed from 0 at `threshold` up to full at threshold+ramp."""
        if distance <= threshold:
            return 0.0
        if distance >= threshold + GAP_RAMP:
            return MAX_SPEED
        return MAX_SPEED * (distance - threshold) / GAP_RAMP

    def desired_speed(self, leader, near_leader, signal_state, accident):
        v = MAX_SPEED

        # 1. Car following. This single rule produces queues at red lights,
        #    queues behind accidents, and spillback across the junction.
        #    While still approaching, `leader` may be a vehicle that has
        #    already crossed the stop line (see Simulation._leaders), so
        #    the comparison must use entry_axis_coord - linear_progress on
        #    that leader would mean something else entirely (its position
        #    on the lane it is turning into, not the lane self is still in).
        if leader is not None:
            if self.phase == "approach":
                gap = self.entry_dir * (leader.entry_axis_coord() - self.entry_axis_coord())
            else:
                gap = leader.linear_progress - self.linear_progress
            v = min(v, self._ramp(gap, MIN_GAP))

        # 1b. A second, narrower check for traffic that shared this lane
        #     right up to the stop line and has only just diverged onto
        #     different paths (e.g. one going straight, one turning). Early
        #     in a turn the arc is still very close to the straight line it
        #     came from, so without this a vehicle that has just committed
        #     to a different movement could pass straight through one that
        #     diverged a moment earlier but is going slower.
        if near_leader is not None:
            gap = self.entry_dir * (near_leader.entry_axis_coord() - self.entry_axis_coord())
            v = min(v, self._ramp(gap, MIN_GAP))

        # 2. Traffic signal. Only applies before the stop line. Once a vehicle
        #    has entered the junction it commits and clears.
        if self.phase == "approach" and signal_state != GREEN:
            v = min(v, self._ramp(self.dist_to_stop_line(), STOP_MARGIN))

        # 3. Accident. Vehicles still get past, but slowly. Checked against
        #    whichever straight segment the vehicle currently occupies; mid
        #    arc only a junction-centre accident applies (2D proximity).
        if accident is not None:
            if self.phase == "approach" and accident.affects(self.route):
                a_pos = accident.position_on(self.route)
                along = self.entry_dir * (a_pos - self.pos)
                if -SLOW_ZONE <= along <= SLOW_ZONE:
                    v = min(v, CRAWL_SPEED)
            elif self.phase == "exit" and accident.affects(self.exit_route):
                a_pos = accident.position_on(self.exit_route)
                along = self.exit_dir * (a_pos - self.exit_pos)
                if -SLOW_ZONE <= along <= SLOW_ZONE:
                    v = min(v, CRAWL_SPEED)
            elif self.phase == "arc" and accident.scope == "JUNCTION":
                x, y = _bezier_point(self.p0, self.corner, self.p2, self.arc_t)
                if math.hypot(x - accident.x, y - accident.y) <= SLOW_ZONE:
                    v = min(v, CRAWL_SPEED)

        return max(0.0, v)

    def update(self, dt_frames, leader, near_leader, signal_state, accident):
        target = self.desired_speed(leader, near_leader, signal_state, accident)

        if target > self.speed:
            self.speed = min(target, self.speed + ACCEL * dt_frames)
        else:
            self.speed = max(target, self.speed - DECEL * dt_frames)

        step = self.speed * dt_frames

        if self.phase == "approach":
            self.pos += self.entry_dir * step
            if self.entry_dir * (self.pos - self.entry_stop) >= 0:
                if self.turning:
                    self.phase = "arc"
                    self.arc_t = 0.0
                else:
                    self.phase = "exit"
                    self.exit_pos = self.pos
        elif self.phase == "arc":
            dx, dy = _bezier_tangent(self.p0, self.corner, self.p2, self.arc_t)
            tangent_speed = math.hypot(dx, dy)
            if tangent_speed > 1e-6:
                self.arc_t += step / tangent_speed
            if self.arc_t >= 1.0:
                self.arc_t = 1.0
                self.phase = "exit"
                self.exit_pos = _box_edge(self.exit_axis, self.exit_dir)
        else:  # exit
            self.exit_pos += self.exit_dir * step

        # Sideways easing so the car visually goes around the debris.
        # Only while on a straight segment; the arc already curves around
        # the box so no extra nudge is needed there.
        want_lateral = 0.0
        if accident is not None and self.phase in ("approach", "exit") \
                and accident.scope != "JUNCTION":
            route = self.route if self.phase == "approach" else self.exit_route
            if accident.affects(route):
                pos = self.pos if self.phase == "approach" else self.exit_pos
                d = self.entry_dir if self.phase == "approach" else self.exit_dir
                along = d * (accident.position_on(route) - pos)
                if -SLOW_ZONE <= along <= SLOW_ZONE:
                    want_lateral = LATERAL_NUDGE
        self.lateral += (want_lateral - self.lateral) * 0.08 * dt_frames


# =============================================================================
# SECTION 11 - POLICE VEHICLE
# =============================================================================
class PoliceCar:
    """
    Drives along the accident's lane, parks beside the accident and stays.
    It ignores the signals and the slow zone because it is responding.
    """

    def __init__(self, accident):
        route = "NS" if accident.scope == "JUNCTION" else accident.scope
        self.route = route
        r = ROUTES[route]
        self.axis = r["axis"]
        self.dir = r["dir"]
        self.lane = r["lane"]
        self.pos = float(r["start"])
        a_pos = accident.position_on(route)
        self.target = a_pos - self.dir * 46.0     # park just short of the debris
        self.parked = False
        self.flash = 0.0

    def update(self, dt_frames):
        self.flash += dt_frames
        if self.parked:
            return
        remaining = self.dir * (self.target - self.pos)
        if remaining <= POLICE_SPEED * dt_frames:
            self.pos = self.target
            self.parked = True
        else:
            self.pos += self.dir * POLICE_SPEED * dt_frames

    def screen_xy(self):
        offset = -LATERAL_NUDGE
        if self.axis == "y":
            return self.lane + offset, self.pos
        return self.pos, self.lane + offset


# =============================================================================
# SECTION 12 - SENSOR SYSTEM
# =============================================================================
class SensorSystem:
    """
    Counts arrivals and discharges per arm and looks for a mismatch.

    WHY THE RULE IS SIGNAL AWARE
    ----------------------------
    An earlier version flagged an anomaly whenever a queue was long and few
    vehicles had crossed the stop line. Testing showed that fired on about
    45 percent of normal frames, because that is exactly what an ordinary red
    light looks like at rush hour. Training Isolation Forest on those labels
    would teach it noise.

    The corrected signature is: a queue is waiting, the light HAS been green
    for a reasonable share of the window, and vehicles still are not getting
    through. Demand is normal, green time was available, throughput collapsed.
    That is a blockage, not a red light.
    """

    def __init__(self):
        self.total_in = {a: 0 for a in ARMS}
        self.total_out = {a: 0 for a in ARMS}
        self.total_demand = {a: 0 for a in ARMS}     # vehicles that wanted to enter
        self.total_blocked = {a: 0 for a in ARMS}    # could not enter, road full
        self.arrivals = {a: [] for a in ARMS}        # timestamps
        self.discharges = {a: [] for a in ARMS}
        self.demand = {a: [] for a in ARMS}
        self.green_hist = {a: [] for a in ARMS}      # (timestamp, seconds green)
        self.queue_len = {a: 0 for a in ARMS}
        self.mean_speed = {a: MAX_SPEED for a in ARMS}
        self.anomaly = {a: False for a in ARMS}
        self.rate = {a: 0.0 for a in ARMS}
        self.green_s = {a: 0.0 for a in ARMS}

    # -- recording ----------------------------------------------------------
    def record_demand(self, arm, t, entered):
        self.total_demand[arm] += 1
        self.demand[arm].append(t)
        if entered:
            self.total_in[arm] += 1
            self.arrivals[arm].append(t)
        else:
            self.total_blocked[arm] += 1

    def record_discharge(self, arm, t):
        self.total_out[arm] += 1
        self.discharges[arm].append(t)

    def record_green(self, arm, t, dt):
        self.green_hist[arm].append((t, dt))

    # -- windowing ----------------------------------------------------------
    def _trim(self, t):
        cutoff = t - WINDOW_S
        for arm in ARMS:
            self.arrivals[arm] = [s for s in self.arrivals[arm] if s >= cutoff]
            self.discharges[arm] = [s for s in self.discharges[arm] if s >= cutoff]
            self.demand[arm] = [s for s in self.demand[arm] if s >= cutoff]
            self.green_hist[arm] = [(s, d) for s, d in self.green_hist[arm]
                                    if s >= cutoff]

    def update(self, t, vehicles):
        self._trim(t)

        for arm in ARMS:
            route = ARM_ROUTE[arm]
            approaching = [v for v in vehicles
                           if v.route == route and not v.past_stop_line()]
            self.queue_len[arm] = sum(
                1 for v in approaching if v.speed < QUEUE_SPEED_THRESHOLD)
            self.mean_speed[arm] = (
                sum(v.speed for v in approaching) / len(approaching)
                if approaching else MAX_SPEED)

            green_s = sum(d for _, d in self.green_hist[arm])
            self.green_s[arm] = green_s
            served = len(self.discharges[arm])
            self.rate[arm] = served / green_s if green_s > 0 else 0.0

            # All three conditions must hold before anything is called an anomaly.
            self.anomaly[arm] = (
                self.queue_len[arm] >= ANOMALY_QUEUE_MIN
                and green_s >= ANOMALY_MIN_GREEN_S
                and self.rate[arm] < ANOMALY_RATE_MIN
            )

    def any_anomaly(self):
        return any(self.anomaly.values())

    def anomaly_arms(self):
        return [a for a in ARMS if self.anomaly[a]]


# =============================================================================
# SECTION 13 - SIMULATION (no drawing, so it can be tested headless)
# =============================================================================
class Simulation:
    def __init__(self, seed=None):
        if seed is not None:
            random.seed(seed)
        self.signals = SignalController()
        self.sensors = SensorSystem()
        self.vehicles = []
        self.accident = None
        self.police = None
        self.sim_time = 0.0                # seconds since start
        self.log_rows = []
        self._next_log = LOG_INTERVAL_S

        # Security channel: encrypts/authenticates the periodic sensor
        # reading sent to the dashboard operator (see _maybe_log below). It
        # is a second, parallel "what does the operator see" path, built on
        # top of the same discharge counts SensorSystem already computes.
        # Nothing downstream of a sensor reading is allowed to change
        # simulation behaviour (DESIGN RULE 1 above), so this channel is
        # never read by SignalController, Vehicle, or SensorSystem - the
        # attack surface here is what the human operator is told, not what
        # the signals do. That is the correct scope, not a limitation.
        #
        # auth.py exists and is unit tested (security/test_security.py);
        # dashboard login UI is not yet wired in - see DECISIONS.md ADR-023
        # consequences.
        self.channel = SensorChannel(crypto=SensorCrypto(), encryption_enabled=True)
        self.channel_log = []              # most-recent-first, capped at CHANNEL_LOG_CAP

        # Attack magnitude/key mode toggles (dashboard STEALTH / KEY COMP
        # buttons, or S/K keys). These do not themselves attack anything -
        # they change how the NEXT false_data/spoof press builds its attack
        # object. See main()'s sec_actions.
        self.attack_stealthy = False
        self.attack_key_compromise = False

        # Threat classification (security/detection.py): combines the
        # channel's verdict on each reading with SensorSystem's existing
        # physical rule to label what the dashboard operator sees. Pure
        # functions in, plain dict out - see _classify() below. Read by
        # the dashboard only; nothing that reads self.classification feeds
        # back into signals, vehicles, or sensors (DESIGN RULE 1).
        self.classification = {
            a: threat_detection.ClassificationResult(threat_detection.CLASSIFICATION_NORMAL)
            for a in ARMS
        }

        # Presentation-only demand scaling (see DEMAND_MULTIPLIER above).
        # An instance attribute, not just the module constant, so the D key
        # can toggle it live without touching module state.
        self.demand_multiplier = DEMAND_MULTIPLIER

        # Display-only record of who approved the schedule and when, and
        # the SHA-256 prefix of the file they approved - set by main()
        # AFTER the approval modal succeeds (Section 15), never read or
        # written by anything else. Plain None defaults here do not touch
        # operators.json or require authentication, so constructing
        # Simulation directly - as every headless test does - stays
        # exactly as unauthenticated as before this gate existed.
        self.approved_by = None
        self.approved_at = None
        self.approval_sha256_prefix = None

    # -- clock --------------------------------------------------------------
    @property
    def hour(self):
        return int((START_HOUR + self.sim_time / 3600.0)) % 24

    def clock_string(self):
        total_min = int(START_HOUR * 60 + self.sim_time / 60.0)
        return f"{(total_min // 60) % 24:02d}:{total_min % 60:02d}"

    # -- spawning -----------------------------------------------------------
    def _spawn_probability(self, arm, dt):
        per_hour = HOURLY_DEMAND[arm][self.hour] * self.demand_multiplier
        return (per_hour / 3600.0) * dt

    def _lane_is_clear_at_entry(self, route):
        """Do not spawn a car on top of one that is still near the entry point."""
        r = ROUTES[route]
        for v in self.vehicles:
            if v.route != route or v.phase != "approach":
                continue
            if abs(v.pos - r["start"]) < MIN_GAP + VEH_LEN:
                return False
        return True

    def _try_spawn(self, dt):
        """
        Demand is recorded even when the vehicle cannot physically enter.

        If a jam reaches the entry point the road is full and no car can join.
        Recording only successful entries would quietly hide that demand, and
        the report needs to be able to say demand held steady while throughput
        fell. So demand and actual entries are counted separately.
        """
        for arm in ARMS:
            route = ARM_ROUTE[arm]
            if random.random() < self._spawn_probability(arm, dt):
                entered = self._lane_is_clear_at_entry(route)
                if entered:
                    self.vehicles.append(Vehicle(route, _choose_movement(arm)))
                self.sensors.record_demand(arm, self.sim_time, entered)

    # -- leader lookup ------------------------------------------------------
    def _leaders(self):
        """
        For each vehicle, find its leader(s) via two passes over two
        different notions of "ahead", returned as (leaders, near_leaders).

        1. The entry lane zone, grouped by entry arm - the shared lane
           before anyone has diverged, using entry_axis_coord() (see that
           method). A vehicle counts here as long as it is within
           STILL_BLOCKING_ZONE of the stop line: still queueing (approach),
           or having only just crossed it (early arc, or a straight vehicle
           just past the line) - all of these are still physically close
           to the stop line and to each other, regardless of which of the
           three movements each one is committed to.

           Approach-phase vehicles get their ONE AND ONLY leader from this
           pass (assigned into `leaders`). Vehicles that have already
           crossed the line get a SECOND, additional leader from this pass
           (assigned into `near_leaders`) on top of their real one from
           pass 2, precisely to catch the moment two vehicles that shared
           the lane right up to the line have only just diverged and are
           still nearly on top of each other - without this, a vehicle
           that just committed to a turn could pass straight through one
           that diverged a moment earlier but is moving slower.

        2. Exit lanes, grouped by exit route, covering both "arc" and
           "exit" phase vehicles via linear_progress (continuous across
           that transition - see the property's docstring), assigned into
           `leaders`. This is what makes an already-turned vehicle gap-keep
           against whatever is ahead of it on the road it is turning into -
           whether that traffic drove straight through or turned in from a
           different approach - rather than whatever it left behind.
        """
        leaders = {}
        near_leaders = {}

        zone_groups = {}
        for v in self.vehicles:
            if v.phase == "approach":
                zone_groups.setdefault(v.route, []).append((v.entry_dir * v.pos, v))
                continue
            if v.turning:
                if v.phase != "arc":
                    continue
            elif v.phase != "exit":
                continue
            coord = v.entry_axis_coord()
            depth = v.entry_dir * (coord - v.entry_stop)
            if 0 <= depth < STILL_BLOCKING_ZONE:
                zone_groups.setdefault(v.route, []).append((v.entry_dir * coord, v))

        for entries in zone_groups.values():
            entries.sort(key=lambda e: e[0])
            for i, (_, v) in enumerate(entries):
                nxt = entries[i + 1][1] if i + 1 < len(entries) else None
                if v.phase == "approach":
                    leaders[v.id] = nxt
                else:
                    near_leaders[v.id] = nxt

        exit_groups = {}
        for v in self.vehicles:
            if v.phase in ("arc", "exit"):
                exit_groups.setdefault(v.exit_route, []).append(v)

        for group in exit_groups.values():
            group.sort(key=lambda v: v.linear_progress)
            for i, v in enumerate(group):
                leaders[v.id] = group[i + 1] if i + 1 < len(group) else None

        return leaders, near_leaders

    # -- main step ----------------------------------------------------------
    def update(self, dt_seconds):
        dt_frames = dt_seconds * 60.0        # movement constants are per frame
        self.sim_time += dt_seconds

        self.signals.update(dt_seconds)
        self._try_spawn(dt_seconds)

        # Green time per arm feeds the anomaly rule. Without it the detector
        # cannot tell a blockage apart from an ordinary red light.
        for arm in ARMS:
            if self.signals.state(arm) == GREEN:
                self.sensors.record_green(arm, self.sim_time, dt_seconds)

        leaders, near_leaders = self._leaders()
        for v in self.vehicles:
            was_before = not v.past_stop_line()
            state = self.signals.state(ROUTES[v.route]["arm"])
            v.update(dt_frames, leaders[v.id], near_leaders.get(v.id), state, self.accident)

            # Discharge sensor: the moment a vehicle crosses the stop line.
            if was_before and v.past_stop_line() and not v.counted_out:
                v.counted_out = True
                self.sensors.record_discharge(ROUTES[v.route]["arm"], self.sim_time)

        self.vehicles = [v for v in self.vehicles if not v.finished()]

        # Accident ageing and police response.
        if self.accident is not None:
            self.accident.age += dt_seconds
            if self.police is None and self.accident.age >= POLICE_DELAY_S:
                self.police = PoliceCar(self.accident)
            if self.police is not None:
                self.police.update(dt_frames)
                if self.police.parked:
                    self.accident.police_arrived = True

        self.sensors.update(self.sim_time, self.vehicles)
        self._maybe_log()
        self._classify()

    # -- accident control ---------------------------------------------------
    def place_accident(self, mx, my):
        """
        Snap a click to the nearest lane centreline.

        Free placement in practice, but the logic only ever reasons about
        route plus distance along it, which is far easier to keep correct.
        """
        if not (SIM_X0 <= mx <= SIM_X1 and 0 <= my <= HEIGHT):
            return None

        in_vert = (CX - ROAD_HALF) <= mx <= (CX + ROAD_HALF)
        in_horz = (CY - ROAD_HALF) <= my <= (CY + ROAD_HALF)

        if in_vert and in_horz:
            self.accident = Accident("JUNCTION", CX, CY)
        elif in_vert:
            if abs(mx - LANE_X_SOUTHBOUND) <= abs(mx - LANE_X_NORTHBOUND):
                self.accident = Accident("NS", LANE_X_SOUTHBOUND, my)
            else:
                self.accident = Accident("SN", LANE_X_NORTHBOUND, my)
        elif in_horz:
            if abs(my - LANE_Y_WESTBOUND) <= abs(my - LANE_Y_EASTBOUND):
                self.accident = Accident("EW", mx, LANE_Y_WESTBOUND)
            else:
                self.accident = Accident("WE", mx, LANE_Y_EASTBOUND)
        else:
            return None                       # click was not on a road

        self.police = None
        return self.accident

    def clear_accident(self):
        self.accident = None
        self.police = None

    # -- logging ------------------------------------------------------------
    def _maybe_log(self):
        if not ENABLE_LOGGING or self.sim_time < self._next_log:
            return
        self._next_log += LOG_INTERVAL_S
        for arm in ARMS:
            discharged = len(self.sensors.discharges[arm])
            self.log_rows.append({
                "sim_time_s": round(self.sim_time, 1),
                "clock": self.clock_string(),
                "arm": arm,
                "demand_window": len(self.sensors.demand[arm]),
                "arrivals_window": len(self.sensors.arrivals[arm]),
                "blocked_entries": self.sensors.total_blocked[arm],
                "discharged_window": discharged,
                "green_seconds_window": round(self.sensors.green_s[arm], 2),
                "discharge_per_green_s": round(self.sensors.rate[arm], 4),
                "queue_length": self.sensors.queue_len[arm],
                "mean_speed": round(self.sensors.mean_speed[arm], 3),
                "accident_active": int(self.accident is not None),
                "accident_location": self.accident.label() if self.accident else "",
                "anomaly_flag": int(self.sensors.anomaly[arm]),
            })

            # Parallel operator-facing path: send this arm's discharge
            # count through the (possibly encrypted, possibly attacked)
            # sensor channel. This is entirely separate from the log_rows
            # entry above - a rejected or tampered reading only ever
            # updates channel_log for the dashboard; it is never merged
            # back into log_rows and never touches self.sensors, so the
            # anomaly detector and sensor_log.csv are unaffected by it.
            self._send_channel_reading(arm, discharged, self.sensors.green_s[arm])

    def _send_channel_reading(self, arm, vehicles, green_seconds_window):
        # sim_time groups readings from the same _maybe_log tick together -
        # security/detection.py's S4 simultaneity signal needs to know
        # which readings arrived in the same interval. encryption_enabled
        # is captured per-reading (not read live later) because it can be
        # toggled live by the E key between ticks.
        base = {
            "arm": arm,
            "sim_time": round(self.sim_time, 1),
            "encryption_enabled": self.channel.encryption_enabled,
            "true_vehicles": vehicles,
            "green_seconds_window": green_seconds_window,
        }
        try:
            event = self.channel.send_and_receive({"road": arm, "vehicles": vehicles})
            self.channel_log.insert(0, {
                **base,
                "accepted": True,
                "reason": event.reason,
                "reported_road": event.plaintext.get("road"),
                "reported_vehicles": event.plaintext.get("vehicles"),
            })
        except ChannelRejected as exc:
            self.channel_log.insert(0, {
                **base,
                "accepted": False,
                "reason": str(exc),
                "reported_road": None,
                "reported_vehicles": None,
            })
        del self.channel_log[CHANNEL_LOG_CAP:]

    # -- threat classification (security/detection.py) ----------------------
    def _classify(self):
        """Combine the channel's verdict (S1-S3, S4) with SensorSystem's
        existing physical rule (S5, self.sensors.anomaly) into a
        per-arm classification for the dashboard. Reads self.channel_log
        and self.sensors; writes only self.classification, which nothing
        in SignalController, Vehicle, or _leaders ever reads - see
        DESIGN RULE 1 at the top of this file.

        S5 is recomputed every call (self.sensors.anomaly is fresh every
        frame). S1-S4 reflect the most recent channel reading per arm,
        which only changes once every LOG_INTERVAL_S (10s) - between
        ticks they simply hold their last value, the same way a real
        dashboard would keep showing the last received reading.
        """
        current_tick = self.channel_log[0]["sim_time"] if self.channel_log else None
        this_tick_entries = [e for e in self.channel_log if e["sim_time"] == current_tick]
        channel_signals_by_arm = {
            e["arm"]: threat_detection.compute_channel_signals(
                accepted=e["accepted"],
                encryption_enabled=e["encryption_enabled"],
                reported_vehicles=e["reported_vehicles"],
                true_vehicles=e["true_vehicles"],
                green_seconds_window=e["green_seconds_window"],
                sim_saturation_headway_s=SIM_SATURATION_HEADWAY_S,
            )
            for e in this_tick_entries
        }
        simultaneity = threat_detection.simultaneity_flag(channel_signals_by_arm)

        results = {}
        for arm in ARMS:
            if arm in channel_signals_by_arm:
                channel_signals = channel_signals_by_arm[arm]
            else:
                # No channel reading has arrived for this arm yet (first
                # LOG_INTERVAL_S of a fresh run) - only S5 can evaluate.
                channel_signals = threat_detection.compute_channel_signals(
                    accepted=True, encryption_enabled=self.channel.encryption_enabled,
                    reported_vehicles=None, true_vehicles=0,
                    green_seconds_window=self.sensors.green_s[arm],
                    sim_saturation_headway_s=SIM_SATURATION_HEADWAY_S,
                )
            results[arm] = threat_detection.classify(
                channel=channel_signals,
                physical_anomaly=self.sensors.anomaly[arm],
                simultaneity=simultaneity,
                accident_active=self.accident is not None,
            )
        self.classification = results

    def write_log(self, path=LOG_PATH):
        if not self.log_rows:
            return
        fields = list(self.log_rows[0].keys())
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.log_rows)


# =============================================================================
# SECTION 14 - RENDERING
# =============================================================================
class Renderer:
    def __init__(self, screen):
        self.screen = screen
        self.f_small = pygame.font.SysFont("Segoe UI, Arial", 12)
        self.f_med = pygame.font.SysFont("Segoe UI, Arial", 14)
        self.f_big = pygame.font.SysFont("Segoe UI, Arial", 18)
        self.f_title = pygame.font.SysFont("Segoe UI, Arial", 15, bold=True)

    def text(self, s, x, y, font=None, colour=C_TEXT, centre=False):
        font = font or self.f_med
        surf = font.render(str(s), True, colour)
        rect = surf.get_rect()
        if centre:
            rect.center = (x, y)
        else:
            rect.topleft = (x, y)
        self.screen.blit(surf, rect)

    def wrap_text(self, s, font, max_width):
        """Greedy word-wrap: splits s into lines no wider than max_width
        under font. Used by the THREAT STATUS box, whose classification/
        signal/action strings are longer than the 212px dashboard column."""
        words = str(s).split(" ")
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if font.size(candidate)[0] <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    # -- junction -----------------------------------------------------------
    def draw_junction(self, sim, accident_mode):
        sc = self.screen
        sc.set_clip(pygame.Rect(SIM_X0, 0, SIM_W, HEIGHT))
        sc.fill(C_GRASS, pygame.Rect(SIM_X0, 0, SIM_W, HEIGHT))

        # Roads, clipped to the simulation zone.
        pygame.draw.rect(sc, C_ROAD, (SIM_X0, CY - ROAD_HALF, SIM_W, ROAD_HALF * 2))
        pygame.draw.rect(sc, C_ROAD, (CX - ROAD_HALF, 0, ROAD_HALF * 2, HEIGHT))

        # Lane divider dashes.
        for y in range(0, HEIGHT, 28):
            if not (CY - ROAD_HALF < y < CY + ROAD_HALF):
                pygame.draw.line(sc, C_LINE, (CX, y), (CX, y + 14), 2)
        for x in range(SIM_X0, SIM_X1, 28):
            if not (CX - ROAD_HALF < x < CX + ROAD_HALF):
                pygame.draw.line(sc, C_LINE, (x, CY), (x + 14, CY), 2)

        # Junction box and stop lines.
        pygame.draw.rect(sc, C_JUNC,
                         (CX - ROAD_HALF, CY - ROAD_HALF, ROAD_HALF * 2, ROAD_HALF * 2))
        for route, r in ROUTES.items():
            self._draw_stop_line(route, r)

        # Accident, police, vehicles.
        if sim.accident is not None:
            self._draw_accident(sim.accident)
        for v in sim.vehicles:
            self._draw_vehicle(v)
        if sim.police is not None:
            self._draw_police(sim.police)

        self._draw_signal_heads(sim)

        if accident_mode:
            pygame.draw.rect(sc, C_RED, (SIM_X0, 0, SIM_W, HEIGHT), 3)
            self.text("ACCIDENT MODE - click a road to place", CX, 22,
                      self.f_title, C_RED, centre=True)

        # Demo-only demand scaling (DEMAND_MULTIPLIER, Section 5). Drawn
        # LAST, on top of everything else in the simulation zone including
        # the accident-mode banner above, and as a solid filled bar (not
        # just a border) specifically so it cannot be cropped out of a
        # screenshot of the junction without also cropping out the traffic
        # the screenshot is presumably trying to show.
        if sim.demand_multiplier != 1.0:
            banner_h = 34
            pygame.draw.rect(sc, C_AMBER, (SIM_X0, 0, SIM_W, banner_h))
            self.text(f"DEMO DENSITY x{sim.demand_multiplier:g} - NOT REAL DEMAND",
                      CX, banner_h // 2, self.f_title, C_BG, centre=True)

        sc.set_clip(None)

    def _draw_stop_line(self, route, r):
        if r["axis"] == "y":
            y = r["stop"]
            x0 = r["lane"] - LANE_OFFSET
            pygame.draw.line(self.screen, C_WHITE, (x0, y), (x0 + 70, y), 3)
        else:
            x = r["stop"]
            y0 = r["lane"] - LANE_OFFSET
            pygame.draw.line(self.screen, C_WHITE, (x, y0), (x, y0 + 70), 3)

    def _draw_vehicle(self, v):
        x, y = v.screen_xy()
        if v.phase == "arc":
            base = pygame.Surface((VEH_LEN, VEH_WID), pygame.SRCALPHA)
            pygame.draw.rect(base, v.colour, base.get_rect(), border_radius=4)
            pygame.draw.rect(base, (20, 22, 26), base.get_rect(), 1, border_radius=4)
            rotated = pygame.transform.rotate(base, v.heading_deg())
            rect = rotated.get_rect(center=(int(x), int(y)))
            self.screen.blit(rotated, rect)
            return
        axis = v.entry_axis if v.phase == "approach" else v.exit_axis
        if axis == "y":
            rect = pygame.Rect(0, 0, VEH_WID, VEH_LEN)
        else:
            rect = pygame.Rect(0, 0, VEH_LEN, VEH_WID)
        rect.center = (int(x), int(y))
        pygame.draw.rect(self.screen, v.colour, rect, border_radius=4)
        pygame.draw.rect(self.screen, (20, 22, 26), rect, 1, border_radius=4)

    def _draw_police(self, p):
        x, y = p.screen_xy()
        if p.axis == "y":
            rect = pygame.Rect(0, 0, VEH_WID, VEH_LEN)
        else:
            rect = pygame.Rect(0, 0, VEH_LEN, VEH_WID)
        rect.center = (int(x), int(y))
        pygame.draw.rect(self.screen, C_WHITE, rect, border_radius=4)
        flash = C_BLUE if int(p.flash / 12) % 2 == 0 else C_RED
        pygame.draw.circle(self.screen, flash, rect.center, 5)

    def _draw_accident(self, a):
        pygame.draw.circle(self.screen, (90, 40, 40), (int(a.x), int(a.y)), SLOW_ZONE, 1)
        pygame.draw.circle(self.screen, C_RED, (int(a.x), int(a.y)), 13)
        self.text("!", int(a.x), int(a.y) - 1, self.f_big, C_WHITE, centre=True)

    def _draw_signal_heads(self, sim):
        colours = {GREEN: C_GREEN, AMBER: C_AMBER, RED: C_RED}
        heads = {
            "North": (CX - ROAD_HALF - 16, CY - ROAD_HALF - 16),
            "South": (CX + ROAD_HALF + 16, CY + ROAD_HALF + 16),
            "East":  (CX + ROAD_HALF + 16, CY - ROAD_HALF - 16),
            "West":  (CX - ROAD_HALF - 16, CY + ROAD_HALF + 16),
        }
        for arm, (x, y) in heads.items():
            state = sim.signals.state(arm)
            pygame.draw.circle(self.screen, (24, 26, 30), (x, y), 10)
            pygame.draw.circle(self.screen, colours[state], (x, y), 7)

    # -- panels -------------------------------------------------------------
    def draw_control_panel(self, sim, accident_mode, camera_on, paused, speed):
        sc = self.screen
        pygame.draw.rect(sc, C_PANEL, (0, 0, PANEL_W, HEIGHT))
        self.text("SIGNAL CONTROL", 16, 16, self.f_title, C_MUTED)

        colours = {GREEN: C_GREEN, AMBER: C_AMBER, RED: C_RED}
        y = 44
        for arm in ARMS:
            state = sim.signals.state(arm)
            pygame.draw.rect(sc, C_CARD, (16, y, 188, 50), border_radius=5)
            pygame.draw.circle(sc, colours[state], (36, y + 25), 9)
            self.text(arm, 54, y + 8, self.f_med, C_TEXT)
            self.text(f"{state.lower()} {sim.signals.remaining(arm):.0f}s",
                      54, y + 27, self.f_small, C_MUTED)
            y += 58

        # Buttons.
        self._button("TRIGGER ACCIDENT  [A]", 16, 300, C_RED if accident_mode else C_CARD)
        self._button("CLEAR ACCIDENT  [X]", 16, 348, C_CARD)
        self._button("CAMERAS  [C]", 16, 396, C_BLUE if camera_on else C_CARD)

        # Status. Date/hour come from the timeline file's currently playing
        # phase (SignalController.current_datetime_label), NOT from the
        # simulation's own clock (sim.hour / sim.clock_string(), used only
        # for the separate SIM CLOCK panel below and vehicle spawn rates) -
        # this is what shows a viewer the schedule is a dated artefact
        # rather than a constant.
        sched_date, sched_hour = sim.signals.current_datetime_label()
        self.text("SCHEDULE SOURCE", 16, 470, self.f_small, C_MUTED)
        self.text(f"pre-planned, {sched_date} {sched_hour:02d}:00", 16, 488, self.f_small, C_TEXT)
        self.text("signals ignore incidents", 16, 506, self.f_small, C_MUTED)
        if sim.approval_sha256_prefix:
            self.text(f"sha256 {sim.approval_sha256_prefix}...", 16, 524,
                      self.f_small, C_MUTED)

        # Persistent record of who approved the schedule and when
        # (security/approval.py's ApprovalRecord, appended by main()'s
        # approval modal before this Simulation ever started). Absent
        # only in a state that should not exist in the live app - the
        # modal blocks the loop from being reached at all otherwise -
        # but headless-constructed Simulations (every test) never set
        # this, hence the guard rather than assuming it is always set.
        if sim.approved_by:
            self.text(f"APPROVED BY {sim.approved_by}", 16, 546,
                      self.f_small, C_GREEN)
            self.text(sim.approved_at, 16, 562, self.f_small, C_MUTED)

        pygame.draw.rect(sc, C_CARD, (16, 620, 188, 62), border_radius=5)
        self.text("SIM CLOCK", 30, 630, self.f_small, C_MUTED)
        self.text(sim.clock_string(), 30, 648, self.f_big, C_TEXT)
        state_txt = "paused" if paused else f"{speed:.0f}x"
        self.text(state_txt, 160, 652, self.f_small, C_MUTED)

    def _button(self, label, x, y, colour):
        pygame.draw.rect(self.screen, colour, (x, y, 188, 38), border_radius=5)
        self.text(label, x + 14, y + 11, self.f_small, C_TEXT)

    def _sec_button(self, rect, label, colour):
        """Draws into one of the shared SEC_BUTTONS rects (Section 1) -
        the same rect main()'s MOUSEBUTTONDOWN handler hit-tests against."""
        pygame.draw.rect(self.screen, colour, rect, border_radius=5)
        self.text(label, rect.centerx, rect.centery, self.f_small, C_TEXT, centre=True)

    # Display-only severity order for picking ONE state to headline in the
    # THREAT STATUS box when different arms classify differently. Does not
    # affect security/detection.py's own per-arm classify() priority chain
    # - that ordering is unrelated and already fixed.
    _DASHBOARD_SEVERITY = [
        threat_detection.CLASSIFICATION_CYBER_CONFIRMED,
        threat_detection.CLASSIFICATION_CYBER_LIKELY,
        threat_detection.CLASSIFICATION_AMBIGUOUS,
        threat_detection.CLASSIFICATION_PHYSICAL_INCIDENT,
        threat_detection.CLASSIFICATION_NORMAL,
    ]

    def _worst_classification(self, classification_by_arm):
        """Picks the single most severe classification present across all
        four arms' results (_DASHBOARD_SEVERITY order), the arms that share
        it, and the union of signals those arms fired - so the small
        THREAT STATUS box has one coherent story to tell instead of trying
        to show four independent verdicts at once."""
        present = {r.classification for r in classification_by_arm.values()}
        worst = next(c for c in self._DASHBOARD_SEVERITY if c in present)
        arms = [a for a, r in classification_by_arm.items() if r.classification == worst]
        signals = []
        for a in arms:
            for s in classification_by_arm[a].signals:
                if s not in signals:
                    signals.append(s)
        example = classification_by_arm[arms[0]]
        return worst, arms, signals, example.confidence, example.action

    def draw_dashboard(self, sim, speed):
        sc = self.screen
        pygame.draw.rect(sc, C_PANEL, (SIM_X1, 0, DASH_W, HEIGHT))
        self.text("SENSOR DASHBOARD", SIM_X1 + 16, 16, self.f_title, C_MUTED)

        y = 44
        for arm in ARMS:
            flagged = sim.sensors.anomaly[arm]
            pygame.draw.rect(sc, C_CARD, (SIM_X1 + 16, y, 212, 74), border_radius=5)
            if flagged:
                pygame.draw.rect(sc, C_RED, (SIM_X1 + 16, y, 212, 74), 2, border_radius=5)
            self.text(f"{arm.upper()}", SIM_X1 + 30, y + 8, self.f_med, C_TEXT)
            self.text(f"in {sim.sensors.total_in[arm]}   out {sim.sensors.total_out[arm]}",
                      SIM_X1 + 30, y + 28, self.f_small, C_MUTED)
            self.text(f"queue {sim.sensors.queue_len[arm]}   "
                      f"flow {sim.sensors.rate[arm]:.2f}/green-s",
                      SIM_X1 + 30, y + 46, self.f_small,
                      C_RED if flagged else C_MUTED)
            y += 82

        # Security channel panel: what the operator sees, not what the
        # signals do. Encryption state and the last few accept/reject
        # outcomes from sim.channel_log (populated in Simulation._maybe_log,
        # Section 13) - the visible proof that toggling E changes whether
        # an active attack (F/G) succeeds, without restarting. The four
        # buttons below hit-test against the SAME SEC_BUTTONS rects main()
        # uses for clicks - see the Section 1 comment on why that matters.
        sec = pygame.Rect(SEC_PANEL_X, SEC_PANEL_Y, SEC_PANEL_W, SEC_PANEL_H)
        pygame.draw.rect(sc, C_CARD, sec, border_radius=5)
        self.text("SENSOR CHANNEL", SEC_PANEL_X + 14, SEC_PANEL_Y + 10,
                  self.f_small, C_MUTED)

        enc = sim.channel.encryption_enabled
        self._sec_button(SEC_BUTTONS["encryption"], "ENCRYPT [E]",
                          C_GREEN if enc else C_RED)
        self._sec_button(SEC_BUTTONS["false_data"], "FALSE DATA [F]", C_CARD)
        self._sec_button(SEC_BUTTONS["spoof"], "SPOOF [G]", C_CARD)
        self._sec_button(SEC_BUTTONS["clear_attacks"], "CLEAR [H]", C_CARD)
        # Mode toggles (security/attacks.py 1a/1b): change what the NEXT
        # false_data/spoof press builds, so they get their own on/off
        # colour rather than the plain C_CARD of the fire-once buttons.
        self._sec_button(SEC_BUTTONS["toggle_stealth"], "STEALTH [S]",
                          C_AMBER if sim.attack_stealthy else C_CARD)
        self._sec_button(SEC_BUTTONS["toggle_key_compromise"], "KEY COMP [K]",
                          C_RED if sim.attack_key_compromise else C_CARD)
        # TIME level buttons (Section 1): one row of four, highlighting the
        # active speed. C_BLUE, not C_AMBER - TIME scales the simulation
        # clock only (via sub-stepping in main()) and fabricates nothing;
        # HOURLY_DEMAND, CRAWL_SPEED, MIN_GAP etc. are untouched. Distinct
        # colour from DENSITY below so a viewer can tell at a glance which
        # control fabricates traffic and which does not, per this label's
        # own text.
        time_label_y = SEC_BUTTONS["time_1x"].top - 14
        self.text("TIME (real demand, fast-forward)", SEC_PANEL_X + 14,
                  time_label_y, self.f_small, C_MUTED)
        for _label, _level in zip(TIME_BUTTON_LABELS, SPEED_LEVELS):
            active = speed == _level
            self._sec_button(SEC_BUTTONS[f"time_{_label}"], _label,
                              C_BLUE if active else C_CARD)

        # Density level buttons (Section 1): one row of four, highlighting
        # whichever level is active. C_AMBER matches the banner colour in
        # draw_junction so "not real demand" reads consistently everywhere
        # a level other than 1x is showing - but active 1x itself must NOT
        # render amber, or the "honest" state would be shown in the same
        # colour as the fabrication warning it exists to avoid; it gets
        # C_GREEN instead, matching the ENCRYPT button's "safe state" use
        # of the same colour elsewhere on this panel.
        density_label_y = SEC_BUTTONS["density_1x"].top - 14
        self.text("DENSITY (simulated, NOT real)", SEC_PANEL_X + 14,
                  density_label_y, self.f_small, C_MUTED)
        for _label, _level in zip(DENSITY_BUTTON_LABELS, DEMAND_LEVELS):
            active = sim.demand_multiplier == _level
            if active:
                colour = C_GREEN if _level == 1.0 else C_AMBER
            else:
                colour = C_CARD
            self._sec_button(SEC_BUTTONS[f"density_{_label}"], _label, colour)

        readings_y = SEC_BUTTONS[f"density_{DENSITY_BUTTON_LABELS[-1]}"].bottom + 4
        self.text("RECENT READINGS", SEC_PANEL_X + 14, readings_y,
                  self.f_small, C_MUTED)
        row_y = readings_y + 11
        # Dynamic, not a hardcoded slice: adapts if panel dimensions ever
        # change, and is measured (not assumed) to clear the THREAT STATUS
        # box, which starts at a fixed y=600 - unrelated to SEC_PANEL_H.
        panel_bottom = SEC_PANEL_Y + SEC_PANEL_H
        row_step = 14
        max_rows = max(1, (panel_bottom - 3 - row_y) // row_step)
        for entry in sim.channel_log[:max_rows]:
            if not entry["accepted"]:
                self.text(f"{entry['arm']:<6}REJECTED", SEC_PANEL_X + 14, row_y,
                          self.f_small, C_RED)
                row_y += row_step
                continue

            # An accepted-but-forged reading is the whole point of the
            # demonstration: it must be visibly wrong here, not just
            # "accepted", or toggling E has nothing to show.
            arm = entry["arm"]
            reported_road = entry["reported_road"]
            reported_vehicles = entry["reported_vehicles"]
            true_vehicles = entry["true_vehicles"]
            road_spoofed = reported_road != arm
            count_faked = reported_vehicles != true_vehicles
            if road_spoofed or count_faked:
                label_road = f"{arm}->{reported_road}" if road_spoofed else arm
                label = f"{label_road} {reported_vehicles} (sent {true_vehicles})"
                colour = C_RED
            else:
                label = f"{arm:<6}{reported_vehicles}"
                colour = C_MUTED
            self.text(label, SEC_PANEL_X + 14, row_y, self.f_small, colour)
            row_y += row_step

        # Threat status: security/detection.py's classification, not the
        # raw SensorSystem rule any more. Picks the single most severe
        # verdict across the four arms (see _worst_classification) so this
        # box tells one coherent story. Colour-coded by class; AMBIGUOUS
        # explicitly names the camera check, since that is the designed
        # human-in-the-loop decision point (security/detection.py's
        # AMBIGUOUS action string already says so - just displayed here).
        box = pygame.Rect(SIM_X1 + 16, 600, 212, 110)
        worst, arms, signals, _confidence, action = self._worst_classification(sim.classification)
        box_colour = {
            threat_detection.CLASSIFICATION_CYBER_CONFIRMED: C_RED,
            threat_detection.CLASSIFICATION_CYBER_LIKELY: C_RED,
            threat_detection.CLASSIFICATION_AMBIGUOUS: C_AMBER,
            threat_detection.CLASSIFICATION_PHYSICAL_INCIDENT: C_BLUE,
            threat_detection.CLASSIFICATION_NORMAL: C_CARD,
        }[worst]
        pygame.draw.rect(sc, box_colour, box, border_radius=5)
        text_colour = C_MUTED if worst == threat_detection.CLASSIFICATION_NORMAL else C_WHITE

        self.text("THREAT STATUS", SIM_X1 + 30, 608, self.f_small, text_colour)
        self.text(worst, SIM_X1 + 30, 626, self.f_med, text_colour)

        line_y = 646
        if worst != threat_detection.CLASSIFICATION_NORMAL:
            self.text(", ".join(arms), SIM_X1 + 30, line_y, self.f_small, text_colour)
            line_y += 16
            if signals:
                # One wrapped line only - box height is fixed (bottom=710)
                # and the action string below needs guaranteed room too.
                first_line = self.wrap_text(", ".join(signals), self.f_small, 184)[0]
                self.text(first_line, SIM_X1 + 30, line_y, self.f_small, text_colour)
                line_y += 15
            for line in self.wrap_text(action, self.f_small, 184)[:2]:
                self.text(line, SIM_X1 + 30, line_y, self.f_small, text_colour)
                line_y += 15
        else:
            self.text("no signals fired", SIM_X1 + 30, line_y, self.f_small, text_colour)
            line_y += 16

        if sim.accident is not None and line_y <= 700:
            self.text(f"incident: {sim.accident.label()}",
                      SIM_X1 + 30, line_y, self.f_small, text_colour)

    # -- camera overlay -----------------------------------------------------
    def draw_cameras(self, sim):
        sc = self.screen
        shade = pygame.Surface((SIM_W, HEIGHT), pygame.SRCALPHA)
        shade.fill((10, 12, 15, 210))
        sc.blit(shade, (SIM_X0, 0))

        panel = pygame.Rect(SIM_X0 + 25, 90, SIM_W - 50, 340)
        pygame.draw.rect(sc, C_PANEL, panel, border_radius=8)
        pygame.draw.rect(sc, C_MUTED, panel, 1, border_radius=8)
        self.text("OBSERVER CAMERA FEEDS", panel.centerx, 112,
                  self.f_title, C_MUTED, centre=True)

        focus = sim.accident.arm() if sim.accident else None
        boxes = [
            ("CAM 1  NORTH", panel.x + 14, 134, 232, 120, "North"),
            ("CAM 2  EAST", panel.x + 258, 134, 232, 120, "East"),
            ("CAM 3  SOUTH", panel.x + 502, 134, 232, 120, "South"),
            ("CAM 4  WEST", panel.x + 14, 266, 232, 120, "West"),
            ("CAM 5  JUNCTION CENTRE", panel.x + 258, 266, 476, 120, "Centre"),
        ]
        for label, x, y, w, h, arm in boxes:
            rect = pygame.Rect(x, y, w, h)
            pygame.draw.rect(sc, (22, 24, 29), rect, border_radius=5)
            border = C_RED if (focus == arm) else C_MUTED
            pygame.draw.rect(sc, border, rect, 2 if focus == arm else 1, border_radius=5)
            self.text(label, rect.centerx, rect.centery - 12,
                      self.f_med, C_TEXT, centre=True)
            note = "INCIDENT IN VIEW" if focus == arm else "feed placeholder"
            self.text(note, rect.centerx, rect.centery + 10, self.f_small,
                      C_RED if focus == arm else C_MUTED, centre=True)

        self.text("press C to close", panel.centerx, 410,
                  self.f_small, C_MUTED, centre=True)

    # -- approval gate (Section 15) ------------------------------------------
    def draw_approval_modal(self, plan_summary, provenance, sha256_hex, fields,
                             active_field, attempt_count, error_message,
                             operators_missing, pivoted_rows, scroll_offset,
                             scrolled_to_end):
        """Runs BEFORE Simulation() is constructed - see _run_approval_gate.
        Every value here is read straight from disk (the plan CSV, the
        model card) or passed in from the modal's own event loop; nothing
        about a running simulation exists yet for this to touch.

        pivoted_rows is ALREADY the pivoted (one row per hour, one column
        per road) view built from the exact bytes sha256_hex was hashed
        over (see _run_approval_gate) - this method only slices a window
        of it for display and never re-reads or re-derives anything from
        disk itself, so the table on screen and the hash can never
        describe two different reads of the plan file.
        """
        sc = self.screen
        sc.fill(C_BG)

        box_w, box_h = 760, 660
        box = pygame.Rect((WIDTH - box_w) // 2, (HEIGHT - box_h) // 2, box_w, box_h)
        pygame.draw.rect(sc, C_PANEL, box, border_radius=8)
        pygame.draw.rect(sc, C_MUTED, box, 1, border_radius=8)

        x = box.x + 30
        field_w = box_w - 60
        y = box.y + 24
        self.text("SCHEDULE APPROVAL REQUIRED", box.centerx, y, self.f_title, C_TEXT, centre=True)
        y += 36

        filename = os.path.basename(APPROVAL_TARGET_PATH)
        self.text(f"File: {filename}", x, y, self.f_small, C_MUTED)
        y += 18
        if plan_summary:
            period_txt = (f"Period: hour {plan_summary['min_hour']}-{plan_summary['max_hour']} "
                          f"({plan_summary['row_count']} rows, hour-of-week template)")
        else:
            period_txt = "Period: UNREADABLE - plan file missing or malformed"
        self.text(period_txt, x, y, self.f_small, C_MUTED)
        y += 18
        self.text(f"Model: {provenance}", x, y, self.f_small, C_MUTED)
        y += 18
        sha_txt = (f"SHA-256: {sha256_hex[:16]}..." if sha256_hex
                   else "SHA-256: UNAVAILABLE - cannot hash plan file")
        self.text(sha_txt, x, y, self.f_small, C_MUTED)
        y += 26

        # -- review pane: PIVOTED (one row per hour) view of the SAME bytes
        # sha256_hex was hashed over - see _parse_plan_rows/_pivot_plan_rows
        # and DECISIONS.md's review-pane ADR. ---------------------------
        pane_rect = None
        total_rows = len(pivoted_rows)
        underlying = plan_summary["row_count"] if plan_summary else 0
        if total_rows:
            self.text(
                f"PIVOTED VIEW - {total_rows} hour-of-week rows "
                f"(from {underlying} underlying hour/road rows)",
                x, y, self.f_small, C_MUTED)
            y += 18

            hour_col_w = 70
            road_col_w = (field_w - hour_col_w) // len(ARMS)
            row_h = 16
            header_h = 18
            pane_h = header_h + APPROVAL_PANE_VISIBLE_ROWS * row_h
            pane_rect = pygame.Rect(x, y, field_w, pane_h)
            pygame.draw.rect(sc, C_CARD, pane_rect, border_radius=4)
            pygame.draw.rect(sc, C_MUTED, pane_rect, 1, border_radius=4)

            self.text("HOUR", x + 8, y + 3, self.f_small, C_MUTED)
            cx = x + hour_col_w
            for road in ARMS:
                self.text(road, cx + 8, y + 3, self.f_small, C_MUTED)
                cx += road_col_w
            row_y = y + header_h

            end = min(scroll_offset + APPROVAL_PANE_VISIBLE_ROWS, total_rows)
            for row in pivoted_rows[scroll_offset:end]:
                self.text(str(row["hour"]), x + 8, row_y + 1, self.f_small, C_TEXT)
                cx = x + hour_col_w
                for road in ARMS:
                    val = row.get(road)
                    self.text("-" if val is None else str(val), cx + 8, row_y + 1,
                              self.f_small, C_TEXT)
                    cx += road_col_w
                row_y += row_h
            y += pane_h + 8

            top_hour = pivoted_rows[scroll_offset]["hour"]
            bottom_hour = pivoted_rows[end - 1]["hour"]
            max_hour = pivoted_rows[-1]["hour"]
            status_colour = C_GREEN if scrolled_to_end else C_MUTED
            status_txt = (
                f"hour {top_hour}-{bottom_hour} of {max_hour}  "
                f"(rows {scroll_offset + 1}-{end} of {total_rows})"
                + ("  - reached end, ACCEPT enabled" if scrolled_to_end else "")
            )
            self.text(status_txt, x, y, self.f_small, status_colour)
            y += 20
        else:
            self.text("PIVOTED VIEW - plan file unreadable, nothing to review",
                      x, y, self.f_small, C_RED)
            y += 20

        u_rect = p_rect = accept_rect = None

        if operators_missing:
            self.text("No operators registered.", x, y, self.f_med, C_RED)
            y += 26
            self.text("Run: python -m security.setup_operator", x, y, self.f_small, C_TEXT)
            y += 40
        else:
            self.text("Username", x, y, self.f_small, C_MUTED)
            y += 14
            u_rect = pygame.Rect(x, y, field_w, 30)
            pygame.draw.rect(sc, C_CARD, u_rect, border_radius=4)
            if active_field == "username":
                pygame.draw.rect(sc, C_BLUE, u_rect, 2, border_radius=4)
            self.text(fields["username"], u_rect.x + 8, u_rect.y + 7, self.f_small, C_TEXT)
            y += 38

            self.text("Password", x, y, self.f_small, C_MUTED)
            y += 14
            p_rect = pygame.Rect(x, y, field_w, 30)
            pygame.draw.rect(sc, C_CARD, p_rect, border_radius=4)
            if active_field == "password":
                pygame.draw.rect(sc, C_BLUE, p_rect, 2, border_radius=4)
            self.text("*" * len(fields["password"]), p_rect.x + 8, p_rect.y + 7,
                      self.f_small, C_TEXT)
            y += 42

            accept_rect = pygame.Rect(x, y, 140, 36)
            if scrolled_to_end:
                pygame.draw.rect(sc, C_GREEN, accept_rect, border_radius=5)
                self.text("ACCEPT", accept_rect.centerx, accept_rect.centery,
                          self.f_med, C_BG, centre=True)
            else:
                pygame.draw.rect(sc, C_CARD, accept_rect, border_radius=5)
                pygame.draw.rect(sc, C_MUTED, accept_rect, 1, border_radius=5)
                self.text("ACCEPT", accept_rect.centerx, accept_rect.centery,
                          self.f_med, C_MUTED, centre=True)
                self.text("scroll to the last row to enable", x + 150,
                          accept_rect.centery, self.f_small, C_MUTED)
            y += 48

            if error_message:
                self.text(error_message, x, y, self.f_small, C_RED)
                y += 18
            if attempt_count > 0:
                self.text(f"attempts: {attempt_count}", x, y, self.f_small, C_MUTED)
                y += 18

        self.text(
            "TAB switch field · UP/DOWN/PGUP/PGDN/wheel scroll · ENTER submit · ESC quit",
            box.centerx, box.bottom - 20, self.f_small, C_MUTED, centre=True)

        # Returned so _run_approval_gate's mouse hit-testing and scroll
        # status use the exact rects just rendered, not a second hardcoded
        # copy that can drift - same reasoning as SEC_BUTTONS (Section 1).
        return u_rect, p_rect, accept_rect, pane_rect


# =============================================================================
# SECTION 15 - MAIN LOOP
# =============================================================================
def _run_approval_gate(clock, renderer):
    """Blocks until the operator approves the schedule PLAN
    (APPROVAL_TARGET_PATH) or quits. Returns an ApprovalRecord on
    success, None if the operator quit (ESC or the window close button)
    without approving.

    Runs entirely BEFORE Simulation() is constructed in main() below -
    nothing about the simulation exists yet, so there is nothing this
    gate could leak into even by accident. Approval gates whether
    playback STARTS; it has no way to influence schedule content or
    phase advancement because those objects do not exist while this
    function is running (see DESIGN RULE at the top of this file and
    the CONSTRAINT in DECISIONS.md's approval ADR).

    The plan file is read exactly ONCE, here, as raw bytes. sha256_hex,
    the plan-summary line and the review pane's pivoted rows are all
    derived from those same bytes - never a second, independent open() -
    so what the operator scrolls through and what gets hashed can never
    describe two different files (see DECISIONS.md's review-pane ADR).
    """
    try:
        with open(APPROVAL_TARGET_PATH, "rb") as fh:
            plan_bytes = fh.read()
    except OSError:
        plan_bytes = None

    if plan_bytes is not None:
        sha256_hex = hashlib.sha256(plan_bytes).hexdigest()
        try:
            plan_rows = _parse_plan_rows(plan_bytes)
        except (KeyError, ValueError):
            plan_rows = []
    else:
        sha256_hex = None
        plan_rows = []

    plan_summary = _plan_summary_from_rows(plan_rows)
    pivoted_rows = _pivot_plan_rows(plan_rows)
    provenance = _read_model_provenance(MODEL_CARD_PATH)

    fields = {"username": "", "password": ""}
    active_field = "username"
    attempt_count = 0
    error_message = None
    u_rect = p_rect = accept_rect = pane_rect = None

    scroll_offset = 0
    max_scroll = max(0, len(pivoted_rows) - APPROVAL_PANE_VISIBLE_ROWS)
    # Nothing to scroll through (unreadable/empty plan) does not block
    # ACCEPT on scrolling - the sha256-unavailable check in _attempt_submit
    # already refuses that case, for the real reason.
    scrolled_to_end = max_scroll == 0

    def _scroll_to(offset):
        nonlocal scroll_offset, scrolled_to_end
        scroll_offset = max(0, min(offset, max_scroll))
        if scroll_offset >= max_scroll:
            scrolled_to_end = True

    def _attempt_submit():
        # Single submit path - both ENTER and a click on accept_rect call
        # this, so there is exactly one place that can approve a schedule.
        nonlocal attempt_count, error_message
        if operators_missing:
            error_message = "No operators registered - see setup command below"
            return None
        if sha256_hex is None:
            error_message = "Cannot hash the plan file - check it exists"
            return None
        if not scrolled_to_end:
            error_message = f"Scroll through all {len(pivoted_rows)} rows before accepting"
            return None
        if not fields["username"] or not fields["password"]:
            error_message = "Enter a username and password"
            return None
        auth = OperatorAuth.load_or_create()
        if auth.verify(fields["username"], fields["password"]):
            timestamp = datetime.now(timezone.utc).isoformat()
            record = ApprovalRecord(
                timestamp=timestamp,
                username=fields["username"],
                schedule_path=str(APPROVAL_TARGET_PATH),
                sha256=sha256_hex,
            )
            append_approval(record)
            return record
        attempt_count += 1
        error_message = "authentication failed"
        fields["password"] = ""
        return None

    while True:
        # Re-checked every frame, not just once at entry, so registering
        # an operator in another terminal while this modal is open is
        # picked up on the next ENTER press without restarting.
        operators_missing = not DEFAULT_CREDENTIALS_PATH.exists()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return None
                elif event.key == pygame.K_TAB:
                    active_field = "password" if active_field == "username" else "username"
                elif event.key == pygame.K_BACKSPACE:
                    fields[active_field] = fields[active_field][:-1]
                elif event.key == pygame.K_UP:
                    _scroll_to(scroll_offset - 1)
                elif event.key == pygame.K_DOWN:
                    _scroll_to(scroll_offset + 1)
                elif event.key == pygame.K_PAGEUP:
                    _scroll_to(scroll_offset - APPROVAL_PANE_VISIBLE_ROWS)
                elif event.key == pygame.K_PAGEDOWN:
                    _scroll_to(scroll_offset + APPROVAL_PANE_VISIBLE_ROWS)
                elif event.key == pygame.K_RETURN:
                    record = _attempt_submit()
                    if record is not None:
                        return record
                elif event.unicode and event.unicode.isprintable():
                    fields[active_field] += event.unicode
            elif event.type == pygame.MOUSEWHEEL:
                _scroll_to(scroll_offset - event.y * 3)  # 3 rows per wheel notch
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # Hit-tests against the rects draw_approval_modal returned
                # last frame - the same rects currently on screen.
                if u_rect and u_rect.collidepoint(event.pos):
                    active_field = "username"
                elif p_rect and p_rect.collidepoint(event.pos):
                    active_field = "password"
                elif accept_rect and accept_rect.collidepoint(event.pos):
                    record = _attempt_submit()
                    if record is not None:
                        return record

        u_rect, p_rect, accept_rect, pane_rect = renderer.draw_approval_modal(
            plan_summary=plan_summary, provenance=provenance, sha256_hex=sha256_hex,
            fields=fields, active_field=active_field, attempt_count=attempt_count,
            error_message=error_message, operators_missing=operators_missing,
            pivoted_rows=pivoted_rows, scroll_offset=scroll_offset,
            scrolled_to_end=scrolled_to_end,
        )

        hovering_accept = accept_rect and accept_rect.collidepoint(pygame.mouse.get_pos())
        try:
            pygame.mouse.set_cursor(
                pygame.SYSTEM_CURSOR_HAND if hovering_accept else pygame.SYSTEM_CURSOR_ARROW)
        except pygame.error:
            pass  # no real cursor under a headless/dummy video driver - not fatal

        pygame.display.flip()
        clock.tick(60)


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(
        "Secure AI-Driven Smart Traffic Control System - Junction Simulation")
    clock = pygame.time.Clock()
    renderer = Renderer(screen)

    approval = _run_approval_gate(clock, renderer)
    if approval is None:
        pygame.quit()
        return

    sim = Simulation()
    sim.approved_by = approval.username
    sim.approved_at = approval.timestamp
    sim.approval_sha256_prefix = approval.sha256[:16]

    # Shared by the E/F/G/H/S/K key handlers and the SEC_BUTTONS mouse
    # handler below, so keyboard and mouse trigger the exact same action -
    # one source of truth, same reasoning as SEC_BUTTONS itself (Section 1).
    # false_data/spoof read sim.attack_stealthy / sim.attack_key_compromise
    # at PRESS time, so toggling STEALTH or KEY COMP changes what the next
    # press builds without altering any attack already in interceptors.
    def _make_false_data_attack():
        return FalseDataInjectionAttack(
            mode="stealthy" if sim.attack_stealthy else "crude",
            crypto=sim.channel.crypto if sim.attack_key_compromise else None,
        )

    def _make_spoof_attack():
        return SensorSpoofingAttack(
            crypto=sim.channel.crypto if sim.attack_key_compromise else None,
        )

    def _cycle_demand_level():
        # 1 -> 10 -> 25 -> 50 -> 1, wrapping. Finds the current level's
        # index rather than assuming it (the level may have been set
        # directly by a density_* button press, not by cycling).
        levels = DEMAND_LEVELS
        try:
            i = levels.index(sim.demand_multiplier)
        except ValueError:
            i = -1  # unknown value on sim.demand_multiplier -> restart at levels[0]
        sim.demand_multiplier = levels[(i + 1) % len(levels)]

    def _cycle_time_level():
        # 1 -> 5 -> 20 -> 50 -> 1, wrapping, same pattern as demand.
        # `speed` is a main()-local (not sim state - it paces sub-stepping,
        # not simulated demand), so this needs nonlocal, not a lambda.
        nonlocal speed
        levels = SPEED_LEVELS
        try:
            i = levels.index(speed)
        except ValueError:
            i = -1
        speed = levels[(i + 1) % len(levels)]

    def _make_time_setter(level):
        def _setter():
            nonlocal speed
            speed = level
        return _setter

    sec_actions = {
        "encryption": lambda: setattr(sim.channel, "encryption_enabled",
                                       not sim.channel.encryption_enabled),
        "false_data": lambda: sim.channel.add_interceptor(_make_false_data_attack()),
        "spoof": lambda: sim.channel.add_interceptor(_make_spoof_attack()),
        "clear_attacks": lambda: sim.channel.clear_interceptors(),
        "toggle_stealth": lambda: setattr(sim, "attack_stealthy", not sim.attack_stealthy),
        "toggle_key_compromise": lambda: setattr(sim, "attack_key_compromise",
                                                   not sim.attack_key_compromise),
        "cycle_demand_level": _cycle_demand_level,
        "cycle_time_level": _cycle_time_level,
    }
    # One sec_actions entry per density/time button, each setting its own
    # exact level directly - the *_BUTTON_LABELS and *_LEVELS tuples share
    # index order (see each constant's comment), so zip is the single
    # source of truth for the label -> value mapping, not a hardcoded list.
    sec_actions.update({
        f"density_{label}": (lambda level=level: setattr(sim, "demand_multiplier", level))
        for label, level in zip(DENSITY_BUTTON_LABELS, DEMAND_LEVELS)
    })
    sec_actions.update({
        f"time_{label}": _make_time_setter(level)
        for label, level in zip(TIME_BUTTON_LABELS, SPEED_LEVELS)
    })

    accident_mode = False
    camera_on = False
    paused = False
    speed = SPEED_LEVELS[0]
    pending_sim_time = 0.0   # sub-stepping backlog, see MAX_SUBSTEPS_PER_FRAME
    running = True

    while running:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_a:
                    accident_mode = not accident_mode
                elif event.key == pygame.K_x:
                    sim.clear_accident()
                    accident_mode = False
                elif event.key == pygame.K_c:
                    camera_on = not camera_on
                elif event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key == pygame.K_t:
                    sec_actions["cycle_time_level"]()
                elif event.key == pygame.K_e:
                    sec_actions["encryption"]()
                elif event.key == pygame.K_f:
                    sec_actions["false_data"]()
                elif event.key == pygame.K_g:
                    sec_actions["spoof"]()
                elif event.key == pygame.K_h:
                    sec_actions["clear_attacks"]()
                elif event.key == pygame.K_s:
                    sec_actions["toggle_stealth"]()
                elif event.key == pygame.K_k:
                    sec_actions["toggle_key_compromise"]()
                elif event.key == pygame.K_d:
                    sec_actions["cycle_demand_level"]()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                if camera_on:
                    camera_on = False
                elif accident_mode:
                    if sim.place_accident(mx, my) is not None:
                        accident_mode = False
                elif mx < PANEL_W:
                    if 300 <= my <= 338:
                        accident_mode = not accident_mode
                    elif 348 <= my <= 386:
                        sim.clear_accident()
                    elif 396 <= my <= 434:
                        camera_on = not camera_on
                elif mx >= SIM_X1:
                    for name, rect in SEC_BUTTONS.items():
                        if rect.collidepoint(mx, my):
                            sec_actions[name]()
                            break

        if not paused:
            # Sub-stepping, not sim.update(dt * speed): a single call with
            # a scaled dt corrupts physics at high speed (verified this
            # session - at speed=50, MAX_SPEED*50=110px > MIN_GAP=48px, so
            # a vehicle can pass through another or overshoot a stop line
            # within one update). Each sub-step below is capped at 1/60s,
            # matching physics resolution at 1x exactly, however large
            # `speed` is; only the NUMBER of sub-steps changes.
            pending_sim_time += dt * speed
            substeps = 0
            while pending_sim_time > 0 and substeps < MAX_SUBSTEPS_PER_FRAME:
                step_dt = min(1.0 / 60.0, pending_sim_time)
                sim.update(step_dt)
                pending_sim_time -= step_dt
                substeps += 1
            # If MAX_SUBSTEPS_PER_FRAME was hit, leftover pending_sim_time
            # carries into next frame's loop instead of growing step_dt -
            # the achieved framerate drops, physics never does.

        screen.fill(C_BG)
        renderer.draw_junction(sim, accident_mode)
        renderer.draw_control_panel(sim, accident_mode, camera_on, paused, speed)
        renderer.draw_dashboard(sim, speed)
        if camera_on:
            renderer.draw_cameras(sim)
        pygame.display.flip()

    sim.write_log()
    pygame.quit()


if __name__ == "__main__":
    main()

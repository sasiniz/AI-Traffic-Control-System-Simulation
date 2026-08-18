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
    1 2 3        simulation speed 1x / 2x / 4x
    E            toggle sensor channel encryption on/off
    F            inject false data attack on the sensor channel
    G            inject sensor spoofing attack on the sensor channel
    H            clear active sensor channel attacks
    ESC          quit
"""

import csv
import math
import os
import random

import pygame

from security.crypto import SensorCrypto
from security.channel import SensorChannel, ChannelRejected
from security.attacks import FalseDataInjectionAttack, SensorSpoofingAttack
from security.auth import OperatorAuth  # noqa: F401 - not wired in yet, see Section 13

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


# Approximate arrivals per hour per arm, by hour of day.
# This is a placeholder for traffic_final_cleaned.csv.
HOURLY_DEMAND = {
    "North": [90, 60, 40, 35, 45, 120, 300, 620, 780, 540, 400, 380,
              430, 420, 400, 450, 600, 760, 690, 500, 360, 260, 180, 120],
    "South": [80, 55, 38, 32, 42, 110, 280, 560, 700, 500, 380, 360,
              410, 400, 380, 430, 560, 700, 640, 470, 340, 250, 170, 110],
    "East":  [60, 40, 28, 25, 33, 85, 200, 400, 520, 380, 300, 290,
              320, 310, 300, 340, 430, 530, 480, 360, 260, 190, 130, 85],
    "West":  [65, 44, 30, 26, 35, 90, 210, 420, 540, 400, 310, 300,
              330, 320, 310, 350, 450, 550, 500, 375, 270, 195, 135, 90],
}

START_HOUR = 8                   # simulation clock starts at 08:00

# =============================================================================
# SECTION 6 - SENSOR AND ANOMALY CONSTANTS
# =============================================================================

WINDOW_S = 20.0                  # rolling window used for anomaly checks
QUEUE_SPEED_THRESHOLD = 0.4      # below this a vehicle counts as queueing
ANOMALY_QUEUE_MIN = 5            # a queue must exist before anything is odd
ANOMALY_MIN_GREEN_S = 6.0        # need enough green time to judge fairly
ANOMALY_RATE_MIN = 0.22          # vehicles discharged per second OF GREEN

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

    # -- clock --------------------------------------------------------------
    @property
    def hour(self):
        return int((START_HOUR + self.sim_time / 3600.0)) % 24

    def clock_string(self):
        total_min = int(START_HOUR * 60 + self.sim_time / 60.0)
        return f"{(total_min // 60) % 24:02d}:{total_min % 60:02d}"

    # -- spawning -----------------------------------------------------------
    def _spawn_probability(self, arm, dt):
        per_hour = HOURLY_DEMAND[arm][self.hour]
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
            self._send_channel_reading(arm, discharged)

    def _send_channel_reading(self, arm, vehicles):
        try:
            event = self.channel.send_and_receive({"road": arm, "vehicles": vehicles})
            self.channel_log.insert(0, {
                "arm": arm,
                "accepted": True,
                "reason": event.reason,
                "reported_road": event.plaintext.get("road"),
                "reported_vehicles": event.plaintext.get("vehicles"),
                "true_vehicles": vehicles,
            })
        except ChannelRejected as exc:
            self.channel_log.insert(0, {
                "arm": arm,
                "accepted": False,
                "reason": str(exc),
            })
        del self.channel_log[CHANNEL_LOG_CAP:]

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

        pygame.draw.rect(sc, C_CARD, (16, 620, 188, 62), border_radius=5)
        self.text("SIM CLOCK", 30, 630, self.f_small, C_MUTED)
        self.text(sim.clock_string(), 30, 648, self.f_big, C_TEXT)
        state_txt = "paused" if paused else f"{speed:.0f}x"
        self.text(state_txt, 160, 652, self.f_small, C_MUTED)

    def _button(self, label, x, y, colour):
        pygame.draw.rect(self.screen, colour, (x, y, 188, 38), border_radius=5)
        self.text(label, x + 14, y + 11, self.f_small, C_TEXT)

    def draw_dashboard(self, sim):
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
        # an active attack (F/G) succeeds, without restarting.
        sec = pygame.Rect(SIM_X1 + 16, 380, 212, 210)
        pygame.draw.rect(sc, C_CARD, sec, border_radius=5)
        self.text("SENSOR CHANNEL", SIM_X1 + 30, 390, self.f_small, C_MUTED)

        enc = sim.channel.encryption_enabled
        self.text("ENCRYPTION  [E]", SIM_X1 + 30, 410, self.f_small, C_MUTED)
        self.text("ON" if enc else "OFF", SIM_X1 + 30, 428,
                  self.f_med, C_GREEN if enc else C_RED)

        self.text("[F] false data  [G] spoof  [H] clear",
                  SIM_X1 + 30, 454, self.f_small, C_MUTED)

        self.text("RECENT READINGS", SIM_X1 + 30, 478, self.f_small, C_MUTED)
        row_y = 496
        for entry in sim.channel_log[:5]:
            if not entry["accepted"]:
                self.text(f"{entry['arm']:<6}REJECTED", SIM_X1 + 30, row_y,
                          self.f_small, C_RED)
                row_y += 17
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
            self.text(label, SIM_X1 + 30, row_y, self.f_small, colour)
            row_y += 17

        # Anomaly summary.
        box = pygame.Rect(SIM_X1 + 16, 600, 212, 82)
        active = sim.sensors.any_anomaly()
        pygame.draw.rect(sc, C_RED if active else C_CARD, box, border_radius=5)
        self.text("ANOMALY STATUS", SIM_X1 + 30, 610, self.f_small,
                  C_WHITE if active else C_MUTED)
        if active:
            arms = ", ".join(sim.sensors.anomaly_arms())
            self.text("flow collapse on green", SIM_X1 + 30, 630, self.f_med, C_WHITE)
            self.text(arms, SIM_X1 + 30, 650, self.f_small, C_WHITE)
        else:
            self.text("none detected", SIM_X1 + 30, 632, self.f_med, C_TEXT)

        if sim.accident is not None:
            self.text(f"incident: {sim.accident.label()}",
                      SIM_X1 + 30, 668, self.f_small,
                      C_WHITE if active else C_MUTED)

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


# =============================================================================
# SECTION 15 - MAIN LOOP
# =============================================================================
def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(
        "Secure AI-Driven Smart Traffic Control System - Junction Simulation")
    clock = pygame.time.Clock()

    sim = Simulation()
    renderer = Renderer(screen)

    accident_mode = False
    camera_on = False
    paused = False
    speed = 1.0
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
                elif event.key == pygame.K_1:
                    speed = 1.0
                elif event.key == pygame.K_2:
                    speed = 2.0
                elif event.key == pygame.K_3:
                    speed = 4.0
                elif event.key == pygame.K_e:
                    sim.channel.encryption_enabled = not sim.channel.encryption_enabled
                elif event.key == pygame.K_f:
                    sim.channel.add_interceptor(FalseDataInjectionAttack())
                elif event.key == pygame.K_g:
                    sim.channel.add_interceptor(SensorSpoofingAttack())
                elif event.key == pygame.K_h:
                    sim.channel.clear_interceptors()
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

        if not paused:
            sim.update(dt * speed)

        screen.fill(C_BG)
        renderer.draw_junction(sim, accident_mode)
        renderer.draw_control_panel(sim, accident_mode, camera_on, paused, speed)
        renderer.draw_dashboard(sim)
        if camera_on:
            renderer.draw_cameras(sim)
        pygame.display.flip()

    sim.write_log()
    pygame.quit()


if __name__ == "__main__":
    main()

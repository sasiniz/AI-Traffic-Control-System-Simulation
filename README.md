# Secure AI-Driven Smart Traffic Control System — Junction Simulation

A pygame-based simulation of a four-way road junction, built as the visual and
data foundation for a final year cyber security project (COM646, Wrexham
University). It models pre-planned signal scheduling, turning traffic,
accident/incident placement, and sensor-based anomaly detection, and logs the
resulting traffic data for later machine learning work.

## Project background

This repo is one piece of a wider project: a *Secure AI Driven Smart Traffic
Control System*, combining AI signal scheduling, anomaly detection, encrypted
sensor communication, attack simulation, and a governance framework aligned
with ISO 27001 and the NIST Cybersecurity Framework. This repo covers the
pygame simulation and the sensor data it produces (`sensor_log.csv`).

The design is motivated by Kesbewa junction in Sri Lanka, where traffic
drives on the left — see [Configuration](#configuration) for the
`DRIVE_SIDE` setting.

### The core academic idea: pre-planned, not reactive

Signal timing is generated **in advance** (annually, from historical and
current data) and never reacts to a live incident. This is deliberate: the
project demonstrates *annually adaptive pre-planned scheduling*, not a
real-time adaptive controller. Accidents in the simulation only feed the
anomaly detector and the observer camera view — they never touch the traffic
lights.

## Features

- **Pre-planned fixed-cycle signal control** — one road arm green at a time,
  cycling North → South → East → West, with hourly green-time durations
  standing in for a future Random Forest–generated schedule.
- **Turning traffic** — each arm sends vehicles straight, left, or right
  using a configurable per-arm split. Turns follow a smooth curved path
  (quadratic Bézier) through the junction and merge correctly into the
  destination lane, respecting the configured driving side.
- **Realistic car-following** — a single shared lane per approach (no
  dedicated turn lanes) with gap-based queueing, so a jam anywhere on a route
  naturally spills back through the junction onto the approach behind it.
- **Accident / incident placement** — click a road to place a blockage that
  slows nearby traffic and eventually draws a police response, without ever
  altering the signal schedule.
- **Sensor & anomaly system** — per-arm arrival/discharge counters, queue
  length, and a signal-aware anomaly rule (queue present + adequate green
  time + collapsed throughput = blockage, not just a red light).
- **Observer camera overlay** — a placeholder multi-feed camera view that
  highlights whichever arm currently has an incident.
- **CSV logging** — periodic snapshots of sensor state written to
  `sensor_log.csv`, intended as labelled training data for a future
  Isolation Forest anomaly model.

## Getting started

### Prerequisites

- Python 3.10+ (developed and tested on 3.12)

### Setup

```bash
# clone the repo
git clone https://github.com/sasiniz/AI-Traffic-Control-System-Simulation.git
cd AI-Traffic-Control-System-Simulation

# create and activate a virtual environment
python -m venv <environment name>
# Windows (PowerShell):
.\<environment name>\Scripts\Activate.ps1
# macOS / Linux:
source <environment name>/bin/activate

# install dependencies
pip install -r requirements.txt
```

### Run

```bash
python main.py
```

## Controls

| Key | Action |
|---|---|
| `A` | Toggle accident placement mode, then click a road |
| `X` | Clear the current accident |
| `C` | Toggle the observer camera overlay |
| `SPACE` | Pause / resume |
| `1` `2` `3` | Simulation speed 1x / 2x / 4x |
| `ESC` | Quit |

## Configuration

The simulation is intentionally driven by a handful of editable constants at
the top of `traffic_sim.py`, rather than command-line flags or a config file:

- **`DRIVE_SIDE`** — `"right"` or `"left"`. Flips every lane and turn
  geometry consistently. Currently set to `"right"`; the Sri Lankan context
  this project is motivated by drives on the left, so this is left as an
  explicit, unresolved choice for the author rather than a silent default.
- **`MOVEMENT_SPLIT`** — per-arm straight/left/right percentages for spawned
  traffic, e.g. `North: {"straight": 55, "left": 25, "right": 20}`.
- **`HOURLY_SCHEDULE`** — hour-of-day → green-time durations, a placeholder
  for the AI-generated annual schedule.
- **`HOURLY_DEMAND`** — hour-of-day → approximate arrivals per arm, a
  placeholder for `traffic_final_cleaned.csv`.

## Project structure

```
traffic_sim.py      # the simulation: signals, vehicles, sensors, rendering
main.py              # entry point (python main.py)
requirements.txt     # pinned dependencies
sensor_log.csv       # generated on exit - sensor snapshots for later ML work
claude.md            # working notes / conventions for AI-assisted development
```

## Feature table sample

`data/feature_table.csv` (the full Stage 1 feature frame, written by
`export_features.py`) is reproducible from committed code and committed data,
so it is gitignored rather than tracked. `data/feature_table_sample.csv` is
committed in its place: a contiguous three-week slice, 2016-12-19 00:00 to
2017-01-08 23:00 inclusive (504 hours per road), straddling the train/test
split so both `split` values appear. It lets a reader check individual rows
by eye without cloning the repo and running the pipeline. See ADR-019 in
`DECISIONS.md`.

Limitation: `outlier_trailing` cannot be verified inside the sample, because
that flag uses a 672-hour trailing window (ADR-009), which is wider than the
504-hour sample. The three lag features (`lag_168`, `lag_336`,
`roll_168_lag168`) CAN be verified within it - three weeks is the minimum
window that lets `lag_336`, which reaches 336 hours back, be checked against
a row that is itself present in the sample.

## Known limitations

- Turning is **visual only**: vehicles gap-keep along their own path and
  against traffic merging onto the same exit lane, but they do not yield to
  crossing traffic. Because only one arm is green at a time, this mostly
  doesn't matter — the one accepted edge case is a vehicle still clearing a
  wide turn through the junction box just as the next phase's traffic
  starts, since there is no all-red clearance interval between phases.
- The anomaly detector, hourly schedule, and hourly demand table are all
  heuristic placeholders for the Random Forest scheduler and Isolation
  Forest anomaly model planned for later phases.

## Roadmap

Later phases (outside this repo's current scope) are planned to add: a
Random Forest scheduler replacing the fixed schedule table, an Isolation
Forest anomaly model trained on `sensor_log.csv`, encrypted sensor
communication (AES-256, bcrypt), attack simulation scripts (Scapy), and a
possible move to SUMO with the TraCI API.

## Author

Sasiru Nimsara (C24110002) — Wrexham University, COM646 Final Year Project.
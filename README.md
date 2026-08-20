# Secure AI-Driven Smart Traffic Control System — Junction Simulation

A pygame-based simulation of a four-way road junction, built as the visual and
data foundation for a final year cyber security project (COM646, Wrexham
University). It plays back a pre-planned signal schedule, models turning
traffic, accident/incident placement, and sensor-based anomaly detection, and
logs the resulting traffic data for machine learning work.

## Project background

This repo is one piece of a wider project: a *Secure AI Driven Smart Traffic
Control System*, combining AI signal scheduling, anomaly detection, encrypted
sensor communication, attack simulation, and a governance framework aligned
with ISO 27001 and the NIST Cybersecurity Framework. This repo covers the
pygame simulation, the AI pipeline that predicts the schedule it plays back,
the security module, and the sensor data the simulation produces
(`sensor_log.csv`).

The design is motivated by Kesbewa junction in Sri Lanka, where traffic
drives on the left — see [Configuration](#configuration) for the
`DRIVE_SIDE` setting.

### The core academic idea: pre-planned, not reactive

Signal timing is predicted offline by a Random Forest, converted to green
seconds by a separate deterministic allocation layer, and compiled into a
timeline file that `SignalController` only ever plays back (ADR-005) — it
computes no timing decision itself and holds no reference to accidents,
queues, or sensors (ADR-023, ADR-037's confirming grep). This is deliberate:
the project demonstrates pre-planned scheduling, not a real-time controller.
Accidents in the simulation only feed the anomaly detector and the observer
camera view — they never touch the traffic lights.

### Schedule horizon

The deployable artefact is a **weekly hour-of-week template**
(`signal_schedule_plan.csv`, compiled into `signal_timeline.csv`),
regenerated from the most recent data (ADR-012). Two of the model's features
(`lag_168` and `lag_336`/`roll_168_lag168`) cannot be computed further than
168 hours ahead of generation time, so a schedule cannot honestly be forecast
a full year forward: dropping those features to reach further leaves
calendar features only, which was measured predicting North at 31 vehicles/h
against an actual of 65. An earlier version of this project's description
said the schedule was generated "annually" — that was found not achievable
and was corrected. See ADR-012 in `DECISIONS.md`.

`signal_schedule_annual.csv` is a separate, **disclosed demonstration
artefact** (ADR-034), not the deployment path: a 52-week recursive forecast
built by feeding the model's own predictions back in as lag features once
real data runs out. Its own measured error grows **2.51x from week 1 to
week 26** of the forecast horizon (see `results/RECURSIVE_DEGRADATION.md`),
which is the evidence for treating it as a demonstration rather than
something that could be deployed as-is.

## Built

Verified against the code currently in this repo, not aspirational:

- **Random Forest count predictor** — `models/count_model.joblib` +
  `models/model_card.json`, selected via a validation grid
  (`model_selection.py`, `results/MODEL_SELECTION.md`). `generate_timeline.py`
  loads it and predicts vehicle counts per road/hour for the weekly plan;
  `generate_dated_schedule.py` and `generate_annual_forecast.py` reuse the
  same trained model over historical and recursive-forecast windows
  respectively.
- **Deterministic allocation layer** — `generate_timeline.py`'s
  `allocate_green()` converts predicted counts to green seconds using
  Webster's method with UK Traffic Signs Manual Chapter 6 constraints
  (`CYCLE_SECONDS=120`, `AMBER_SECONDS=3`, `MIN_GREEN_SECONDS=12`,
  `AVAILABLE_GREEN=108`; ADR-021). Kept in a file separate from the
  predictor, and separate again from `SignalController`, which only plays
  back the compiled result.
- **Sensor channel encryption — in transit only** — `security/channel.py`'s
  `SensorChannel` and `security/crypto.py`'s `SensorCrypto` (AES-256-GCM),
  wired into the simulation at `traffic_sim.py:1250` and toggled live with
  the `E` key (`traffic_sim.py:2609-2611, 2664-2665`). **Not** at rest:
  `sensor_log.csv` is written as plaintext — see ADR-029.
- **Attack simulation** — `security/attacks.py`'s
  `FalseDataInjectionAttack` and `SensorSpoofingAttack`, inserted as channel
  interceptors via the `F` and `G` keys and cleared with `H`
  (`traffic_sim.py:2564-2578, 2666-2671`). Scapy and packet-level network
  attacks are explicitly out of scope (in-process channel, see
  `security/README.md`); DoS/DDoS is explicitly excluded (ADR-023).
- **Operator approval gate** — `security/approval.py` (SHA-256 file
  hash-binding, append-only `security/approvals.jsonl`) and `security/auth.py`
  (bcrypt-hashed operator credentials) behind `_run_approval_gate()`
  (`traffic_sim.py:2375-2536`), which runs to completion *before*
  `Simulation()` is constructed. The schedule file is read exactly once; the
  hash, the review pane's rows, and the logged approval all derive from that
  one read (ADR-028, ADR-031). The review pane's ACCEPT control only enables
  once every row has been scrolled or jumped to; `HOME` after reaching the
  end does not reopen it — see `test_approval_gate_home_after_end_keeps_scroll_ratchet`
  in `security/test_security.py` (ADR-033).

Not yet built: encryption of `sensor_log.csv` at rest (ADR-029), and the
Isolation Forest anomaly model (the current detector is a rule-based
placeholder — see [Known limitations](#known-limitations)).

## Features

- **Pre-planned fixed-cycle signal control** — one road arm green at a
  time, cycling North → South → East → West, playing back the Random
  Forest + allocation-layer schedule described above.
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
  time + collapsed throughput = blockage, not just a red light). Currently a
  heuristic placeholder pending the Isolation Forest model.
- **Observer camera overlay** — a placeholder multi-feed camera view that
  highlights whichever arm currently has an incident.
- **CSV logging** — periodic snapshots of sensor state written to
  `sensor_log.csv` with a provenance header (run time, git commit, schedule
  hash, approval, encryption/attack state — ADR-032), intended as labelled
  training data for a future Isolation Forest anomaly model.

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

### Registering an operator

The approval gate requires at least one registered operator credential
before a schedule can be accepted. `security/operators.json` is gitignored
and never shipped populated — create your own with:

```bash
python -m security.setup_operator
```

This prompts for a username and password (via `getpass`, not echoed to the
terminal) and stores a bcrypt hash in `security/operators.json`. Re-run with
`--force` to overwrite an existing username.

### Run

```bash
python main.py
```

This opens the approval gate first: it hashes the current
`signal_schedule_annual.csv`, shows a scrollable review pane of its rows, and
requires an operator to scroll (or jump) to the end and sign in before
accepting. Only after acceptance is the simulation window constructed and
play begins. Pressing `ESC` or closing the window at the gate exits without
starting a simulation.

## Controls

| Key | Action |
|---|---|
| `A` | Toggle accident placement mode, then click a road |
| `X` | Clear the current accident |
| `C` | Toggle the observer camera overlay |
| `SPACE` | Pause / resume |
| `T` | Cycle simulation speed: 1x → 5x → 20x → 50x |
| `D` | Cycle demand density: 1x → 10x → 25x → 50x |
| `ESC` | Quit |

### Security & attack simulation keys

| Key | Action |
|---|---|
| `E` | Toggle sensor-channel encryption on/off |
| `F` | Insert a false data injection attack |
| `G` | Insert a sensor spoofing attack |
| `H` | Clear all active attacks |
| `S` | Toggle stealthy attack mode |
| `K` | Toggle key-compromise attack mode |

## Configuration

The simulation is intentionally driven by a handful of editable constants at
the top of `traffic_sim.py`, rather than command-line flags or a config file:

- **`DRIVE_SIDE`** — `"right"` or `"left"`. Flips every lane and turn
  geometry consistently. Currently set to `"right"`; the Sri Lankan context
  this project is motivated by drives on the left, so this is left as an
  explicit, unresolved choice for the author rather than a silent default.
- **`MOVEMENT_SPLIT`** — per-arm straight/left/right percentages for spawned
  traffic, e.g. `North: {"straight": 55, "left": 25, "right": 20}`.
- **`HOURLY_DEMAND`** — hour-of-day → arrivals per arm, the mean of the real
  `data/traffic_final_cleaned.csv` per (road, hour), used to spawn traffic at
  `DEMAND_MULTIPLIER` 1.0. Verified against the dataset directly, no longer a
  placeholder.
- **`SIGNAL_TIMELINE_PATH`** — the phase-by-phase file `SignalController`
  plays back (default `signal_timeline.csv`, compiled by
  `generate_timeline.py` from the weekly plan).
- **`APPROVAL_TARGET_PATH`** — the schedule file the operator approval gate
  hashes and reviews before playback starts (default
  `signal_schedule_annual.csv` — the disclosed recursive-forecast
  demonstration artefact described above, ADR-036).

## Project structure

```
traffic_sim.py              # the simulation: signals, vehicles, sensors, security, rendering
main.py                     # entry point (python main.py)
generate_timeline.py        # offline: weekly plan -> signal_timeline.csv (ADR-012)
generate_dated_schedule.py  # offline: schedule over real held-out test dates (ADR-033)
generate_annual_forecast.py # offline: disclosed recursive annual forecast demo (ADR-034)
data_prep.py                 # Stage 1: feature engineering (FEATURE_COLUMNS, temporal split)
train_model.py               # Stage 2: trains the Random Forest count predictor
model_selection.py           # validation grid across model families/targets (ADR-020)
model_wrapper.py             # DiffTargetRandomForest wrapper used by every generation script
export_features.py           # writes data/feature_table.csv for inspection
explore_features.py          # regenerates the eight report figures in figures/
requirements.txt             # pinned dependencies
DECISIONS.md                 # the ADR log - why things were chosen
CLAUDE.md                    # working notes / conventions for AI-assisted development
sensor_log.csv               # generated on exit - sensor snapshots (gitignored)

security/            # encryption, auth, attacks, detection, approval gate (see security/README.md)
results/             # measured evidence artefacts (MODEL_SELECTION.md, RESULTS_LOG.md, runs/, ...)
models/              # model_card.json (committed) + count_model.joblib (gitignored, regenerate it)
data/                # traffic_final_cleaned.csv (source dataset) + feature_table_sample.csv
figures/             # the eight exploratory-analysis report figures (committed deliverables)
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
- The anomaly detector is a heuristic placeholder pending the Isolation
  Forest model planned for Stage 6.
- Sensor-channel encryption protects readings in transit only.
  `sensor_log.csv` is written as plaintext — see ADR-029.
- The in-process channel model means the simulated attacker and the
  simulated sensor share the same Python process and trust boundary; this
  demonstrates the cryptographic properties correctly but is not a claim
  the system has been tested against a network-positioned adversary.

## Roadmap

Genuinely outstanding: the Isolation Forest anomaly model trained on
`sensor_log.csv`, encryption of `sensor_log.csv` at rest, and a possible
move to SUMO with the TraCI API.

## Author

Sasiru Nimsara (C24110002) — Wrexham University, COM646 Final Year Project.

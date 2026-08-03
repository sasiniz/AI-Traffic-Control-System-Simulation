# CLAUDE.md

Design decisions and their reasoning are in DECISIONS.md.
Read it before changing anything you did not write.

## Project
This repo is the junction simulation for a final year cyber security project
(module COM646). The wider project is a Secure AI Driven Smart Traffic Control
System: AI signal scheduling, anomaly detection, encrypted sensor
communication, attack simulation, and a governance framework aligned with
ISO 27001 and the NIST Cybersecurity Framework. This repo covers the pygame
simulation, the sensor data it produces, and the AI pipeline built from the
historical dataset.

`traffic_sim.py` is the only file that runs the live simulation.
`generate_timeline.py` and `data_prep.py` run offline and never execute at
simulation time. `sensor_log.csv` is the simulation's output.

## Files
- `traffic_sim.py` - the simulation: layout, routes, signal playback, turning,
  accidents, sensors, anomaly detection, rendering.
- `main.py` - entry point (`python main.py`).
- `generate_timeline.py` - offline generator; compiles an hour-by-hour green
  seconds plan into the phase-by-phase signal timeline CSV.
- `signal_timeline_sample.csv` - the phase-by-phase schedule `SignalController`
  actually plays back. Currently sample data; later the Random Forest output.
- `data_prep.py` - Stage 1 of the AI pipeline: feature engineering only. No
  model, no prediction. Defines `FEATURE_COLUMNS`, the temporal split, and the
  `outlier_trailing` flag.
- `sensor_log.csv` - sensor snapshots written on exit; training input for the
  anomaly model.
- `data/traffic_final_cleaned.csv` - the source dataset: 58,368 rows, four
  roads by 14,592 hourly slots, 2015-11-01 to 2017-06-30. Input to the AI
  pipeline. See ADR-001 and ADR-002 in DECISIONS.md.
- `requirements.txt` - pinned dependencies (pygame, pandas, numpy).
- `DECISIONS.md` - why things were chosen. Read before changing anything you
  did not write.

## The one rule that must never break
Signal timing is PRE-PLANNED and never reacts to a live incident. Do not make
the traffic lights respond to accidents, queues, or anomalies. Ever.

Why: the design is "annually adaptive pre-planned scheduling". The AI generates
a full year of hourly signal timings in advance from historical and current
data, and a human operator authenticates it before deployment. It is not a real
time adaptive controller. That distinction is the academic core of the project.
If the lights start reacting live, the project no longer demonstrates what it
claims.

The `SignalController` class therefore has no reference to accidents, queues, or
sensors. Keep it that way. An accident only feeds the anomaly detection and the
observer camera view. It never touches signal timing.

## The AI predicts counts, not green times
The Random Forest predicts VEHICLE COUNT per road per hour. It does not predict
green seconds. There are no green-second labels anywhere in the dataset, so
training on them would mean generating labels from our own allocation formula
and then learning that formula back, which is circular.

A separate deterministic layer converts predicted counts into green seconds
using Webster's method with the UK constraints from Traffic Signs Manual
Chapter 6. Keep these two layers in separate files. The split is what makes the
schedule explainable, which the governance chapter depends on.

See ADR-007 in DECISIONS.md.

## Current scope of the simulation
Vehicles travel straight, left, or right from every arm, using a per-arm
split (`MOVEMENT_SPLIT`) and a compass-derived exit lookup (`TURN_EXIT`).
Turning is visual only: vehicles gap-keep along their own path and against
traffic merging onto the same exit lane, but they do not yield to crossing
traffic. Because one arm is green at a time, the only place this shows up is
a known, accepted edge case: a straggler from one phase still clearing the
box as the next phase's traffic arrives. See ADR-003 in DECISIONS.md.

Build new features one layer at a time. Confirm the current layer behaves
correctly before adding the next. Do not implement several features at once.

## How movement and queueing work
Each of the four through routes is modelled as ONE continuous lane from edge to
edge. Because of this, a queue behind an accident on the far side of the
junction automatically blocks traffic on the near side. This spillback is not
special cased. It falls out of the normal car following (gap keeping) logic.

Do not add a separate queueing system for accidents. If queueing looks wrong,
fix the shared car following logic, do not bolt on a second system.

Position is one dimensional on the straight segments and only there. In the
`approach` and `exit` phases a vehicle is a single number along its lane axis,
and screen x and y are derived from it. That is what makes queueing reliable,
so keep it that way.

The `arc` phase is the one deliberate exception: a turning vehicle follows a
quadratic Bezier through the junction box and is genuinely 2D. It is bounded on
both sides, entering the arc at the stop line and leaving it at the exit lane
edge, and `linear_progress` projects the arc back onto a comparable 1D scale so
gap keeping still works across the transition. Do not extend 2D movement beyond
the junction box, and do not try to flatten the arc back to one dimension.

## Anomaly detection
The anomaly signature is NOT "a high vehicle count" and NOT "a long queue on its
own". A normal rush hour red light produces a long queue and few discharges, so
those alone are not anomalies.

The correct signature is: a queue is present, the light HAS been green for a
reasonable share of the window, and vehicles still are not getting through.
Demand normal, green time available, throughput collapsed. That means a
blockage, not a red light.

This matters. An earlier version used the naive "long queue plus few discharges"
rule and fired on about 45 percent of normal frames. If you change the detector,
test the false positive rate with no accident present before trusting it.

The detector currently in the code is a heuristic placeholder. Its job is to
produce labelled data in `sensor_log.csv` for training the real model later. The
planned real models are Random Forest for vehicle count prediction, feeding the
signal scheduler, and Isolation Forest for anomaly detection, where Isolation
Forest also catches genuine traffic anomalies, not only attacks. Do not treat
the placeholder as the final model.

## Layout invariant
The window is 1280 by 720, split into three vertical zones: left control panel
220, centre junction 800, right dashboard 260. These must add up exactly to the
window width. The junction cross is centred inside the middle zone, not the whole
window. If you change any panel width, adjust the others so they still sum to the
width, or every position drifts.

## Open decisions, do not silently assume
- DRIVE_SIDE is currently set to "right". The project is motivated by Kesbewa
  junction in Sri Lanka, where traffic drives on the LEFT. This is an unresolved
  choice for the author to make, not a settled default. Changing the one
  DRIVE_SIDE constant flips every lane correctly. Flag it, do not assume "right"
  is intended. Resolve this before the allocation layer (stage 3) is built,
  because changing it later means regenerating everything downstream.

## Dataset rules
- The West road segment was extended with synthetic data covering 2015-11-01 to
  2016-12-31, marked by `Synthetic_Segment_Unverified` (10,248 of 14,592 West
  rows, roughly 70 percent). Boundary verified against the CSV on 2026-08-03.
  This boundary is the same date as the train/test split, so West trains
  entirely on synthetic data and tests entirely on real data. West results must
  always be reported separately from the other three arms, never averaged in.
  See ADR-002.
- Demand is NOT stationary. Monthly mean vehicles per hour roughly tripled over
  the dataset: North 20.5 to 73.4, South 8.4 to 25.7, East 6.9 to 18.0. West is
  flat at ~7.2 throughout its synthetic period, which is one of the signatures
  of that segment being generated. Any rule involving a fixed threshold must
  account for this trend.
- `Outlier_Flag` in the CSV is a per-road Tukey fence (Vehicles > Q3 + 1.5*IQR)
  computed once across the whole period, so it drifts with the growth trend and
  is partly a date proxy (South: 0.0% of hours flagged in every quarter of 2016,
  31.6% in 2017 Q2). `data_prep.py` derives `outlier_trailing` from a trailing
  28 day fence instead. Anomaly work uses `outlier_trailing`. Never delete
  either flag's rows from any dataset file. See ADR-009.
- The Stage 2 count predictor trains on ALL rows including flagged ones.
  Excluding peaks would make it underpredict rush hour, which is exactly when
  green time matters. `temporal_split_without_outliers` exists for the Stage 6
  Isolation Forest only, and when used there it must key on `outlier_trailing`,
  not `Outlier_Flag`. See ADR-010.
- The train/test split is temporal, not random: train before 2017-01-01, test
  from 2017-01-01 onward. Never use a random split on this data. See ADR-008.
- Model inputs are defined by `FEATURE_COLUMNS` in `data_prep.py`, and Stage 2
  must obtain them by calling `feature_matrix()` rather than selecting columns
  itself. `ID` is deliberately excluded because it encodes date, hour and road
  (2015-11-01 00:00 East is 20151101003), so a tree could memorise rows through
  it. There is no `year` feature, for the same reason.

## Where this is heading
Later phases add: the Random Forest scheduler, whose output will replace the
contents of `signal_timeline_sample.csv` - no code change in `SignalController`
is required, since it only ever plays back whatever CSV is at
`SIGNAL_TIMELINE_PATH`. See ADR-005 in DECISIONS.md. Also planned: the
Isolation Forest anomaly model, encrypted sensor communication (AES-256-GCM,
bcrypt), attack simulation scripts (Scapy), and a possible move to SUMO with
the TraCI API. The current pygame build is the visual and data foundation
for those.

## Working conventions
- Verify behaviour by running the code or checking the log. Do not claim
  something works without evidence.
- If a task specifies both reasoning and an implementation, write both. Do not
  document a feature that has not been implemented. This has happened twice in
  this project and the docstring reads convincingly enough to be missed.
- Keep changes small and reviewable.
- The simulation writes `sensor_log.csv` on exit. That file is the training
  input for the machine learning phase, so keep its columns stable and
  meaningful.

## AI pipeline stages
1. `data_prep.py` - feature engineering                          DONE
2. `train_model.py` - Random Forest count predictor              <- NEXT
3. allocation layer - counts to green seconds (Webster + Ch.6 limits)
4. replace `HOURLY_DEMAND` in traffic_sim.py with real dataset counts
5. encrypted sensor to database channel
6. Isolation Forest on logged sensor data
7. observer console, bcrypt auth, signed schedule deployment
8. attack simulation (Scapy) and evaluation

Work one stage at a time. Do not start a stage before the previous one has been
run and its output checked.

KNOWN INCONSISTENCY, stage 4 fixes it: `HOURLY_DEMAND` in traffic_sim.py is
placeholder data peaking at 780 vehicles/hour for North. The real dataset peaks
at 156. Until stage 4 replaces it, the schedule and the simulation are modelling
different junctions, and any evaluation comparing planned against delivered
green time is meaningless.
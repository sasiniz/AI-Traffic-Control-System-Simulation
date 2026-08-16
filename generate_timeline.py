"""
Offline generator that expands an hour-by-hour green-time plan into a full
phase-by-phase signal timeline CSV.

traffic_sim.py never runs this logic at run time and never will - it is
meant to read the finished CSV once that hookup is built (a separate task;
see the note at the bottom of this file). Running this script only ever
writes data files; it has no effect on the live simulation, and the
"signals never react to incidents" rule is untouched (ADR-005).

Stage 3: counts to green seconds
----------------------------------
This file used to expand a 3-hour hardcoded PLAN dict as a placeholder for
the Random Forest's output. That placeholder is gone (see LEGACY_DEMO_PLAN
below - kept, not deleted, for historical reference). build_plan_from_model()
now does the real thing: it loads models/count_model.joblib, builds the 14
features for each future hour per road, predicts counts, and converts those
counts into green seconds via allocate_green().

Usage:
    python generate_timeline.py
"""

import csv
import math
import os
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from data_prep import DATA_PATH, FEATURE_COLUMNS, load_and_engineer, feature_matrix
import model_wrapper  # noqa: F401 - required so joblib.load() can unpickle DiffTargetRandomForest

# -- fixed, decision-level constants (see DECISIONS.md ADR-006, ADR-012) ---
HOUR_SECONDS = 3600
CYCLE_SECONDS = 120
AMBER_SECONDS = 3

# ADR-006: 2.0s start-up lost time + 5 vehicles x 1.9s saturation headway
# = 11.5s, rounded up to 12s. Two roles, same underlying number:
#   (1) allocate_green()'s per-road floor within a cycle
#   (2) compile_timeline()'s boundary-fit threshold (the original
#       MIN_GREEN_TO_START name is kept as an alias below for continuity
#       with ADR-006's own wording)
# ADR-006 is still load-bearing even though S8 in this task's review shows
# role (2) is now DORMANT: 3600 / CYCLE_SECONDS = 30 exactly, so every hour
# ends on a complete cycle boundary and the boundary-fit branch in
# compile_timeline() never fires. Kept, not removed - a future change to
# CYCLE_SECONDS could make it live again.
MIN_GREEN_SECONDS = 12
MIN_GREEN_TO_START = MIN_GREEN_SECONDS  # alias - see comment above

ROTATION = ["North", "South", "East", "West"]

# Derived - computed, never hardcoded, so changing CYCLE_SECONDS or
# MIN_GREEN_SECONDS keeps these consistent automatically.
AVAILABLE_GREEN = CYCLE_SECONDS - AMBER_SECONDS * len(ROTATION)      # 108
COMMITTED_GREEN = MIN_GREEN_SECONDS * len(ROTATION)                  # 48
DISCRETIONARY_GREEN = AVAILABLE_GREEN - COMMITTED_GREEN              # 60
MAX_GREEN = MIN_GREEN_SECONDS + DISCRETIONARY_GREEN                  # 72

DEFAULT_HORIZON_HOURS = 168  # one week - ADR-012

MODEL_PATH = Path("models") / "count_model.joblib"

# Superseded placeholder - what PLAN used to be. Never invoked at runtime
# any more (build_plan_from_model() replaces it); kept for historical
# reference rather than deleted, the same append-don't-erase reasoning
# DECISIONS.md uses for its own log.
LEGACY_DEMO_PLAN_SUPERSEDED = {
    7: {"North": 51, "South": 18, "East": 13, "West": 12},
    8: {"North": 51, "South": 17, "East": 14, "West": 12},
    9: {"North": 51, "South": 15, "East": 15, "West": 12},
}


# -- R1: counts -> green seconds --------------------------------------------
def allocate_green(predicted_counts, cycle_seconds=CYCLE_SECONDS):
    """
    Convert one hour's four predicted counts into green seconds per road,
    within `cycle_seconds`. Returns (green_seconds: dict, n_clamped: int).

    Each road gets MIN_GREEN_SECONDS plus its share of the discretionary
    time (available_green - committed), where share is that road's
    predicted count over the four-road total.

    Three cases handled, all measured to occur on real data (see
    DECISIONS.md / this task's review):

    (a) ROUNDING. Naive round() does not reliably sum to available_green
        (measured: fails on 33.7% of all 14592 real hours at these exact
        settings). Largest-remainder allocation instead: floor every
        share, then hand the leftover seconds one at a time to the roads
        with the largest fractional remainder. This always sums exactly.

    (b) NEGATIVE PREDICTIONS. The model predicts a difference and adds
        lag_168 back (model_wrapper.DiffTargetRandomForest) - nothing
        constrains the result to be positive. Negative counts are clamped
        to 0 before shares are computed; the clamp count is returned so
        it can be reported, not silently absorbed.

    (c) ZERO TOTAL. If all four clamped counts are 0, split the
        discretionary time equally instead of dividing by a zero total.
        Measured: never occurs historically (minimum four-road hourly
        total across the whole dataset is 13) - this is a guard against a
        pathological model output, not an expected path.
    """
    n_roads = len(ROTATION)
    available_green = cycle_seconds - AMBER_SECONDS * n_roads
    committed = MIN_GREEN_SECONDS * n_roads
    discretionary = available_green - committed
    if discretionary < 0:
        raise ValueError(
            f"cycle_seconds={cycle_seconds} cannot fit {n_roads} roads at "
            f"MIN_GREEN_SECONDS={MIN_GREEN_SECONDS} plus amber={AMBER_SECONDS} each."
        )

    clamped = {}
    n_clamped = 0
    for road in ROTATION:
        v = float(predicted_counts[road])
        if v < 0:
            n_clamped += 1
            v = 0.0
        clamped[road] = v

    total = sum(clamped.values())
    if total == 0:
        # (c) ZERO TOTAL - equal weights reuse the exact same
        # largest-remainder machinery below rather than a separate branch.
        weights, total_weight = {road: 1.0 for road in ROTATION}, float(n_roads)
    else:
        weights, total_weight = clamped, total

    raw_shares = {road: discretionary * weights[road] / total_weight for road in ROTATION}
    floors = {road: math.floor(raw_shares[road]) for road in ROTATION}
    leftover = discretionary - sum(floors.values())

    # (a) Largest-remainder: `leftover` is always in [0, n_roads-1] here,
    # since each floor is within 1 of its raw share. Ties broken by
    # ROTATION order for a deterministic result.
    ranked = sorted(
        ROTATION,
        key=lambda r: (-(raw_shares[r] - floors[r]), ROTATION.index(r)),
    )
    bonus = {road: 0 for road in ROTATION}
    for road in ranked[:leftover]:
        bonus[road] = 1

    green = {road: MIN_GREEN_SECONDS + floors[road] + bonus[road] for road in ROTATION}
    assert sum(green.values()) == available_green, (
        f"largest-remainder allocation summed to {sum(green.values())}, "
        f"expected {available_green}"
    )
    return green, n_clamped


# -- R2: model -> per-hour predicted counts ----------------------------------
def build_plan_from_model(start_datetime=None, hours=DEFAULT_HORIZON_HOURS):
    """
    Load the trained model and build the 14 model features for every
    (future hour, road) pair over the next `hours` hours, predict counts,
    and convert each hour to green seconds. Returns
    {hour_index: {"datetime", "predicted_counts", "green_seconds",
    "n_clamped"}}.

    WHY THE REAL data_prep.load_and_engineer() IS CALLED, NOT REIMPLEMENTED
    ----------------------------------------------------------------------------
    ADR-019 keeps explore_features.py's feature computation independent
    from data_prep.py, because those figures are EVIDENCE and evidence
    sharing a code path with what it evidences cannot contradict it. This
    file is the opposite case: it is not evidence, it is the schedule the
    model actually drives, so it must use the IDENTICAL transformation the
    model was trained on - a hand-copied formula that drifted from
    data_prep.py by even one line would silently feed the model
    out-of-distribution inputs. The real historical rows plus the future
    target rows are combined into one frame, written to a temporary CSV,
    and run through data_prep.load_and_engineer() itself - the actual
    imported function, not a re-implementation of what it does - so the
    hour_sin/cos, dow_sin/cos, month_sin/cos, is_weekend, road one-hots
    and lag_168/lag_336/roll_168_lag168 are computed by exactly the same
    code that engineered the model's training data.

    All of lag_168, lag_336 and roll_168_lag168 for any hour in this
    168-hour horizon resolve to hours at or before `start_datetime`
    (ADR-015's legality rule) - i.e. already-real, already-known data, so
    no recursive/bootstrapped forecasting is needed anywhere in this
    function.
    """
    raw = pd.read_csv(DATA_PATH, parse_dates=["DateTime"])
    # ID is not used anywhere downstream (data_prep.FEATURE_COLUMNS
    # deliberately excludes it - see data_prep.py's module docstring) and
    # future rows have no meaningful ID, so it is dropped rather than
    # fabricated.
    raw = raw.drop(columns=["ID"]).sort_values(["Road", "DateTime"]).reset_index(drop=True)

    last_hour_per_road = raw.groupby("Road")["DateTime"].max()
    if last_hour_per_road.nunique() != 1:
        raise ValueError(
            "Roads do not share a common last historical hour - cannot build "
            "a single contiguous future window for all four roads."
        )
    last_hour = last_hour_per_road.iloc[0]
    expected_start = last_hour + pd.Timedelta(hours=1)

    if start_datetime is None:
        start_datetime = expected_start
    elif pd.Timestamp(start_datetime) != expected_start:
        # lag_168/lag_336/roll_168_lag168 use POSITIONAL shift (see
        # data_prep.py) - a gap between the historical data and the future
        # window would silently misalign every lag feature, the same
        # failure mode data_prep's own _assert_hourly_series_is_complete
        # guards against. Loud failure instead of a silent wrong schedule.
        raise ValueError(
            f"start_datetime must immediately follow the last historical hour "
            f"to keep the hourly series gap-free ({expected_start} expected, "
            f"got {start_datetime})."
        )
    start_datetime = pd.Timestamp(start_datetime)

    future_rows = [
        {
            "DateTime": start_datetime + pd.Timedelta(hours=h),
            "Road": road,
            "Vehicles": np.nan,
            "Outlier_Flag": False,
            "Synthetic_Segment_Unverified": False,
        }
        for h in range(hours)
        for road in ROTATION
    ]
    combined = pd.concat([raw, pd.DataFrame(future_rows)], ignore_index=True)
    combined = combined.sort_values(["Road", "DateTime"]).reset_index(drop=True)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv")
    os.close(tmp_fd)
    try:
        combined.to_csv(tmp_path, index=False)
        # The real data_prep.py function - see the docstring above for why
        # this must not be a re-implementation.
        engineered, _ = load_and_engineer(tmp_path)
    finally:
        os.remove(tmp_path)

    future_features = engineered.loc[engineered["DateTime"] >= start_datetime].copy()
    if len(future_features) != hours * len(ROTATION):
        raise ValueError(
            f"Expected {hours * len(ROTATION)} future feature rows, got "
            f"{len(future_features)} - a future (hour, road) row was dropped "
            "(likely missing lag history close to the end of the dataset)."
        )

    model = joblib.load(MODEL_PATH)

    plan = {}
    for h in range(hours):
        dt = start_datetime + pd.Timedelta(hours=h)
        hour_rows = future_features[future_features["DateTime"] == dt].set_index("Road")
        hour_rows = hour_rows.loc[ROTATION]  # fixed, deterministic order

        X, _ = feature_matrix(hour_rows)
        assert list(X.columns) == FEATURE_COLUMNS

        predicted_counts = dict(zip(ROTATION, model.predict(X)))
        green_seconds, n_clamped = allocate_green(predicted_counts, CYCLE_SECONDS)

        plan[h] = {
            "datetime": dt,
            "predicted_counts": predicted_counts,
            "green_seconds": green_seconds,
            "n_clamped": n_clamped,
        }

    return plan


def write_schedule_plan_csv(plan, output_path):
    """R4: signal_schedule_plan.csv - hour, road, predicted_count, green_seconds."""
    with open(output_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["hour", "road", "predicted_count", "green_seconds"])
        for hour_index in sorted(plan):
            entry = plan[hour_index]
            for road in ROTATION:
                writer.writerow([
                    hour_index, road,
                    entry["predicted_counts"][road],
                    entry["green_seconds"][road],
                ])


# -- phase compilation --------------------------------------------------------
def compile_timeline(plan_by_datetime, start_road, output_path):
    """
    Expand a {pd.Timestamp -> {road: green_seconds}} plan into one row per
    phase and write it to `output_path`. Returns (rows, boundary_triggers).

    Keys are real timestamps, sorted chronologically, rather than a single
    day's hour-of-day - this is what lets one call compile a schedule
    spanning any number of calendar days, not just one repeated day.

    Rotation is continuous across hour AND day boundaries: only the very
    first phase of the whole file uses `start_road`; every hour after that
    picks up wherever the previous hour left off.

    Each hour must sum to exactly HOUR_SECONDS of green + amber.

    FAST PATH (the current CYCLE_SECONDS=120): when CYCLE_SECONDS divides
    HOUR_SECONDS exactly, `full_cycles` complete N-S-E-W cycles fill the
    hour with nothing left over, so every hour is just emitted as that
    many whole cycles - no lookahead needed. A one-phase lookahead (the
    kind used below) would ALWAYS flag the very last phase of every hour,
    because it asks "does the next phase still fit in what's left of THIS
    hour's budget", which is trivially false once the budget hits exactly
    0 - regardless of whether the cycle length divides evenly. That is a
    fencepost artifact of the lookahead, not a real leftover-time problem,
    which is why it is bypassed here rather than left to fire on it.

    ADR-006 PATH (dormant under the current constants, not removed): when
    CYCLE_SECONDS does NOT divide HOUR_SECONDS evenly, a one-phase
    lookahead decides how to close each hour out exactly:
      - if the next road could still get MIN_GREEN_SECONDS or more of
        green, it starts with that shortened green and the hour ends
        there;
      - otherwise it does not start at all - the current road's green is
        extended to fill the hour exactly, and the skipped road becomes
        the first phase of the next hour instead.
    boundary_triggers counts how often this path's special-casing fires -
    see ADR-006/S8: it is only reachable at all when HOUR_SECONDS is not
    an exact multiple of CYCLE_SECONDS, which is not true today.
    """
    rows = []
    road = start_road
    phase_index = 0
    boundary_triggers = 0

    full_cycles, hour_remainder = divmod(HOUR_SECONDS, CYCLE_SECONDS)

    for dt in sorted(plan_by_datetime):
        hour_plan = plan_by_datetime[dt]

        if hour_remainder == 0:
            # FAST PATH - see docstring. No lookahead, so nothing can
            # trigger the ADR-006 boundary branch.
            for _ in range(full_cycles):
                for _ in range(len(ROTATION)):
                    rows.append(_row(dt, phase_index, road, hour_plan[road]))
                    phase_index += 1
                    road = ROTATION[(ROTATION.index(road) + 1) % len(ROTATION)]
            continue

        # ADR-006 PATH - see docstring. Dormant today, kept intact for a
        # CYCLE_SECONDS that does not divide HOUR_SECONDS evenly.
        remaining = HOUR_SECONDS
        while remaining > 0:
            green = hour_plan[road]
            next_road = ROTATION[(ROTATION.index(road) + 1) % len(ROTATION)]
            after_this = remaining - (green + AMBER_SECONDS)
            next_cost = hour_plan[next_road] + AMBER_SECONDS

            if next_cost <= after_this:
                # The next phase fits too - commit this one at full length
                # and carry straight on within the same hour.
                rows.append(_row(dt, phase_index, road, green))
                phase_index += 1
                remaining = after_this
                road = next_road
                continue

            boundary_triggers += 1
            possible_green = after_this - AMBER_SECONDS

            if possible_green >= MIN_GREEN_TO_START:
                rows.append(_row(dt, phase_index, road, green))
                phase_index += 1
                rows.append(_row(dt, phase_index, next_road, possible_green))
                phase_index += 1
                road = ROTATION[(ROTATION.index(next_road) + 1) % len(ROTATION)]
            else:
                extended_green = remaining - AMBER_SECONDS
                rows.append(_row(dt, phase_index, road, extended_green))
                phase_index += 1
                road = next_road

            remaining = 0

    write_csv(rows, output_path)
    return rows, boundary_triggers


def _row(dt, phase_index, road, green_seconds):
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "start_hour": dt.hour,
        "phase_index": phase_index,
        "road": road,
        "green_seconds": green_seconds,
        "amber_seconds": AMBER_SECONDS,
    }


def write_csv(rows, path):
    fields = ["date", "start_hour", "phase_index", "road", "green_seconds", "amber_seconds"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    print(f"CYCLE_SECONDS={CYCLE_SECONDS}  AMBER_SECONDS={AMBER_SECONDS}  "
          f"MIN_GREEN_SECONDS={MIN_GREEN_SECONDS}")
    print(f"AVAILABLE_GREEN={AVAILABLE_GREEN}  COMMITTED_GREEN={COMMITTED_GREEN}  "
          f"DISCRETIONARY_GREEN={DISCRETIONARY_GREEN}  MAX_GREEN={MAX_GREEN}")

    plan = build_plan_from_model(hours=DEFAULT_HORIZON_HOURS)
    total_clamped = sum(entry["n_clamped"] for entry in plan.values())
    print(f"Built plan for {len(plan)} hours starting {plan[0]['datetime']}. "
          f"Total negative predictions clamped: {total_clamped}")

    write_schedule_plan_csv(plan, "signal_schedule_plan.csv")
    print(f"Wrote signal_schedule_plan.csv ({len(plan) * len(ROTATION)} rows)")

    plan_by_datetime = {entry["datetime"]: entry["green_seconds"] for entry in plan.values()}
    rows, boundary_triggers = compile_timeline(plan_by_datetime, start_road="North",
                                                output_path="signal_timeline.csv")
    print(f"Wrote {len(rows)} phase rows to signal_timeline.csv. "
          f"Boundary-fit rule triggered {boundary_triggers} times "
          f"(expected 0 - see ADR-006/S8).")

    # signal_timeline_sample.csv is deliberately untouched by this script -
    # traffic_sim.py still reads that file; switching it over to
    # signal_timeline.csv is a separate task.

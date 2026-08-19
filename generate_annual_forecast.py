"""
Offline generator that produces a RECURSIVE, multi-step-ahead signal
schedule extending PAST the end of the historical dataset (2017-06-30).

data/traffic_final_cleaned.csv ends at 2017-06-30 23:00. lag_168, lag_336
and roll_168_lag168 are only LEGAL (ADR-015, CLAUDE.md's feature legality
rule) up to 168 hours past the most recent known Vehicles value - a single
week. A schedule reaching a full year past the data therefore cannot use
real lag inputs for 8,592 of its 8,760 hours (see STEP 0's changeover
computation, reported at run time below and in DECISIONS.md's
recursive-forecast ADR).

This script does it anyway, RECURSIVELY: predict one week (BLOCK_HOURS)
forward, append that week's own predictions to the per-road history
buffer as if they were real Vehicles, then predict the next week from a
buffer that now contains forecasts of forecasts. This is exactly the
technique ADR-012 and ADR-033 rejected for this same annual-horizon
problem, because error compounds across iterations with nothing in this
project's scope to bound it. It is used here anyway, for a stated reason
that does not reverse either ADR's underlying argument: see
DECISIONS.md's recursive-forecast ADR for why, what the measured
degradation actually is (--mode validation, below), and what horizon the
result can honestly be used for.

models/count_model.joblib is loaded as-is and only ever .predict()-ed -
this script never calls .fit() and never retrains anything (a hard
constraint on this task, not a design preference).

Two independent modes:

  --mode deliverable
    Seed on ALL real data (2015-11-01 to 2017-06-30). Forecast recursively
    to 2018-06-30 23:00, the literal 35,040-row annual window originally
    requested. Writes signal_schedule_annual.csv.

  --mode validation
    Seed on real data through 2016-12-31 only, deliberately withholding
    the six months of real data that follow. Forecast the same way across
    2017-01-01 to 2017-06-30, where real actuals already exist (ADR-008's
    held-out test period), and compare. Writes
    results/RECURSIVE_DEGRADATION.md and a plot of MAE against forecast
    horizon. This is the ONLY source of evidence for how fast recursive
    error actually compounds on this dataset - the deliverable mode alone
    would produce 35,040 numbers with no way to tell whether they are
    reasonable.

traffic_sim.py's APPROVAL_TARGET_PATH is deliberately NOT pointed at this
file's output - see DECISIONS.md's recursive-forecast ADR. This script
only ever writes signal_schedule_annual.csv; it has no effect on the live
simulation or the approval gate.

Usage:
    python generate_annual_forecast.py --mode deliverable
    python generate_annual_forecast.py --mode validation
"""

import argparse
import csv
import os
import tempfile

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_prep import DATA_PATH, FEATURE_COLUMNS, load_and_engineer, feature_matrix
from generate_timeline import (
    ROTATION, CYCLE_SECONDS, MIN_GREEN_SECONDS, AVAILABLE_GREEN, MODEL_PATH,
    allocate_green,
)
import model_wrapper  # noqa: F401 - required so joblib.load() can unpickle DiffTargetRandomForest

# ADR-012/ADR-015's legality boundary: 168 hours is the furthest any of
# lag_168/lag_336/roll_168_lag168 can be legally computed past the most
# recent known value. Blocking the recursion at exactly this size means no
# block ever needs a value from within itself - every dependency resolves
# to an earlier block (or real data), never a not-yet-predicted hour in
# the same block.
BLOCK_HOURS = 168

# -- deliverable mode: the literal annual window originally requested -----
DELIVERABLE_SEED_END = pd.Timestamp("2017-06-30 23:00")   # all real data
DELIVERABLE_START = pd.Timestamp("2017-07-01 00:00")
DELIVERABLE_END = pd.Timestamp("2018-06-30 23:00")
DELIVERABLE_OUTPUT = "signal_schedule_annual.csv"

# -- validation mode: seed withholds the real test period, then forecasts
#    across it so the forecast can be checked against real actuals -------
VALIDATION_SEED_END = pd.Timestamp("2016-12-31 23:00")
VALIDATION_START = pd.Timestamp("2017-01-01 00:00")
VALIDATION_END = pd.Timestamp("2017-06-30 23:00")
DEGRADATION_OUTPUT = "results/RECURSIVE_DEGRADATION.md"
DEGRADATION_PLOT = "results/recursive_degradation_mae_by_horizon.png"

HORIZON_WEEKS = [1, 2, 4, 8, 13, 26]


def _load_seed(seed_end):
    """Real rows only, up to and including seed_end. ID is dropped (not
    used by FEATURE_COLUMNS, and future rows have no meaningful ID) -
    same reasoning as generate_timeline.py's build_plan_from_model()."""
    raw = pd.read_csv(DATA_PATH, parse_dates=["DateTime"])
    raw = raw.drop(columns=["ID"]).sort_values(["Road", "DateTime"]).reset_index(drop=True)
    return raw[raw["DateTime"] <= seed_end].copy()


def recursive_forecast(seed_end, start, end, model, block_hours=BLOCK_HOURS, verbose=True):
    """
    Predict every (hour, road) in [start, end] recursively, in
    block_hours-sized chunks. Each block's own predictions are appended to
    the per-road history buffer (as if they were real Vehicles) before the
    next block is computed, so later blocks' lag_168/lag_336/
    roll_168_lag168 read forecasts rather than real data once the buffer's
    real tail is more than 168 hours behind the block being predicted.

    Returns a list of row dicts: datetime (pd.Timestamp), road,
    predicted_count (float, unclamped model output - what is fed back into
    the buffer), green_seconds (int, via the unmodified ADR-021
    allocate_green), lags_real (bool - whether EVERY feature this hour
    used was still backed by a real observation, i.e. the hour's lag_168
    lookup falls at or before seed_end).
    """
    working = _load_seed(seed_end)
    all_datetimes = pd.date_range(start, end, freq="h")
    n_blocks = (len(all_datetimes) + block_hours - 1) // block_hours
    results = []

    for i in range(n_blocks):
        block_dts = all_datetimes[i * block_hours:(i + 1) * block_hours]

        future_rows = pd.DataFrame([
            {"DateTime": dt, "Road": road, "Vehicles": np.nan,
             "Outlier_Flag": False, "Synthetic_Segment_Unverified": False}
            for dt in block_dts for road in ROTATION
        ])
        combined = pd.concat([working, future_rows], ignore_index=True)
        combined = combined.sort_values(["Road", "DateTime"]).reset_index(drop=True)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv")
        os.close(tmp_fd)
        try:
            combined.to_csv(tmp_path, index=False)
            # The real data_prep.py function, not a re-implementation - see
            # generate_timeline.py's build_plan_from_model() docstring for
            # why a hand-copied formula would be a silent correctness risk.
            engineered, _ = load_and_engineer(tmp_path)
        finally:
            os.remove(tmp_path)

        block_features = engineered[engineered["DateTime"].isin(block_dts)].copy()
        if len(block_features) != len(block_dts) * len(ROTATION):
            raise ValueError(
                f"Block starting {block_dts[0]}: expected "
                f"{len(block_dts) * len(ROTATION)} feature rows, got "
                f"{len(block_features)} - a (hour, road) row was dropped."
            )

        new_real_rows = []
        for dt in block_dts:
            hour_rows = block_features[block_features["DateTime"] == dt].set_index("Road")
            hour_rows = hour_rows.loc[ROTATION]  # fixed, deterministic order

            X, _ = feature_matrix(hour_rows)
            assert list(X.columns) == FEATURE_COLUMNS

            predicted_counts = dict(zip(ROTATION, model.predict(X)))
            green_seconds, _n_clamped = allocate_green(predicted_counts, CYCLE_SECONDS)
            lags_real = bool((dt - pd.Timedelta(hours=168)) <= seed_end)

            for road in ROTATION:
                results.append({
                    "datetime": dt,
                    "road": road,
                    "predicted_count": predicted_counts[road],
                    "green_seconds": green_seconds[road],
                    "lags_real": lags_real,
                })
                new_real_rows.append({
                    "DateTime": dt, "Road": road,
                    "Vehicles": predicted_counts[road],
                    "Outlier_Flag": False, "Synthetic_Segment_Unverified": False,
                })

        working = pd.concat([working, pd.DataFrame(new_real_rows)], ignore_index=True)
        working = working.sort_values(["Road", "DateTime"]).reset_index(drop=True)

        if verbose:
            print(f"  block {i + 1}/{n_blocks}: {block_dts[0]} to {block_dts[-1]} "
                  f"({len(block_dts)}h) done")

    return results


def write_annual_csv(rows, output_path=DELIVERABLE_OUTPUT):
    fields = ["datetime", "road", "predicted_count", "green_seconds", "lags_real"]
    with open(output_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "datetime": r["datetime"].isoformat(),
                "road": r["road"],
                "predicted_count": r["predicted_count"],
                "green_seconds": r["green_seconds"],
                "lags_real": "true" if r["lags_real"] else "false",
            })


def run_deliverable():
    model = joblib.load(MODEL_PATH)
    print(f"ADR-021 allocation constants in force (imported, not "
          f"redefined): CYCLE_SECONDS={CYCLE_SECONDS}, "
          f"MIN_GREEN_SECONDS={MIN_GREEN_SECONDS}, AVAILABLE_GREEN={AVAILABLE_GREEN}.")
    print(f"Seeding on all real data through {DELIVERABLE_SEED_END}, "
          f"forecasting {DELIVERABLE_START} to {DELIVERABLE_END} recursively "
          f"in {BLOCK_HOURS}h blocks.")

    rows = recursive_forecast(DELIVERABLE_SEED_END, DELIVERABLE_START, DELIVERABLE_END, model)

    n_hours = len(rows) // len(ROTATION)
    n_real = sum(1 for r in rows if r["lags_real"])
    n_pred = len(rows) - n_real
    print(f"Built {len(rows)} rows ({n_hours} hours x {len(ROTATION)} roads).")
    print(f"lags_real=true: {n_real} rows ({n_real // len(ROTATION)} hours) - "
          f"lags_real=false: {n_pred} rows ({n_pred // len(ROTATION)} hours).")

    write_annual_csv(rows)
    print(f"Wrote {DELIVERABLE_OUTPUT}")


def _mae_table(merged):
    """merged carries datetime, road, predicted_count, actual_count,
    abs_error, horizon_hour (1-indexed hours since forecast start). Returns
    a list of row dicts, one per HORIZON_WEEKS bucket that has data."""
    max_horizon = int(merged["horizon_hour"].max())
    table = []
    for w in HORIZON_WEEKS:
        lo = (w - 1) * 168 + 1
        hi = w * 168
        if lo > max_horizon:
            continue
        hi_avail = min(hi, max_horizon)
        bucket = merged[(merged["horizon_hour"] >= lo) & (merged["horizon_hour"] <= hi_avail)]
        per_road = bucket.groupby("road")["abs_error"].mean()
        excl_west = bucket[bucket["road"] != "West"]["abs_error"].mean()
        table.append({
            "week": w,
            "hours": f"{lo}-{hi_avail}",
            "partial": hi_avail < hi,
            "East": per_road.get("East", float("nan")),
            "North": per_road.get("North", float("nan")),
            "South": per_road.get("South", float("nan")),
            "West": per_road.get("West", float("nan")),
            "OVERALL_excl_West": excl_west,
        })
    return table


def run_validation():
    model = joblib.load(MODEL_PATH)
    print(f"Seeding on real data through {VALIDATION_SEED_END} only "
          f"(withholding {VALIDATION_START} to {VALIDATION_END}), forecasting "
          f"recursively across that withheld window in {BLOCK_HOURS}h blocks, "
          f"then comparing against the real actuals for it.")

    rows = recursive_forecast(VALIDATION_SEED_END, VALIDATION_START, VALIDATION_END, model)

    pred_df = pd.DataFrame(rows)

    actual_raw = pd.read_csv(DATA_PATH, parse_dates=["DateTime"])
    actual = actual_raw[
        (actual_raw["DateTime"] >= VALIDATION_START) & (actual_raw["DateTime"] <= VALIDATION_END)
    ][["DateTime", "Road", "Vehicles"]].rename(
        columns={"DateTime": "datetime", "Road": "road", "Vehicles": "actual_count"}
    )

    merged = pred_df.merge(actual, on=["datetime", "road"], how="left")
    if merged["actual_count"].isna().any():
        missing = merged[merged["actual_count"].isna()]
        raise ValueError(
            f"{len(missing)} forecast rows have no matching real actual - the "
            "validation window must be fully real data."
        )

    merged["abs_error"] = (merged["predicted_count"] - merged["actual_count"]).abs()
    merged["horizon_hour"] = (
        (merged["datetime"] - VALIDATION_START).dt.total_seconds() // 3600
    ).astype(int) + 1  # 1-indexed: first forecast hour is horizon 1

    table = _mae_table(merged)

    week1 = next((r for r in table if r["week"] == 1), None)
    week26 = next((r for r in table if r["week"] == 26), None)
    ratio = None
    if week1 and week26 and week1["OVERALL_excl_West"]:
        ratio = week26["OVERALL_excl_West"] / week1["OVERALL_excl_West"]

    os.makedirs("results", exist_ok=True)

    # -- plot -----------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 5))
    weeks_plotted = [r["week"] for r in table]
    for road in ["East", "North", "South", "West"]:
        ax.plot(weeks_plotted, [r[road] for r in table], marker="o", label=road)
    ax.plot(weeks_plotted, [r["OVERALL_excl_West"] for r in table],
            marker="s", linewidth=2.5, color="black", label="OVERALL_excl_West")
    ax.set_xlabel("Forecast horizon (weeks since recursion start)")
    ax.set_ylabel("MAE (vehicles/hour)")
    ax.set_title("Recursive forecast error growth by horizon\n"
                  "(validation: seeded through 2016-12-31, forecast across 2017 H1)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(DEGRADATION_PLOT, dpi=150)
    plt.close(fig)
    print(f"Wrote {DEGRADATION_PLOT}")

    # -- markdown report --------------------------------------------------
    lines = []
    lines.append("# Recursive Forecast Degradation\n")
    lines.append(
        f"Validation protocol: seeded on real data through {VALIDATION_SEED_END} "
        f"only, forecast recursively across {VALIDATION_START} to "
        f"{VALIDATION_END} ({len(merged) // len(ROTATION)} hours), compared "
        f"against the real actuals for that window (ADR-008's held-out test "
        f"period). No real 2017 data was used as a forecast input - only the "
        f"model's own prior predictions, fed back through the history buffer "
        f"exactly as --mode deliverable does. See DECISIONS.md's "
        f"recursive-forecast ADR.\n"
    )
    lines.append("MAE by forecast horizon, bucketed at weeks "
                  f"{', '.join(str(w) for w in HORIZON_WEEKS)}. "
                  "OVERALL_excl_West per ADR-011.\n")
    lines.append("| Week | Hours (horizon) | East | North | South | West | OVERALL_excl_West |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in table:
        hours_label = r["hours"] + (" (partial)" if r["partial"] else "")
        lines.append(
            f"| {r['week']} | {hours_label} | {r['East']:.3f} | {r['North']:.3f} | "
            f"{r['South']:.3f} | {r['West']:.3f} | {r['OVERALL_excl_West']:.3f} |"
        )
    lines.append("")

    if ratio is not None:
        lines.append(
            f"Week 26 / week 1 ratio, OVERALL_excl_West: "
            f"{week26['OVERALL_excl_West']:.3f} / {week1['OVERALL_excl_West']:.3f} "
            f"= **{ratio:.2f}x**.\n"
        )
        if ratio > 3:
            lines.append(
                f"Error grew by more than 3x across the horizon measured here "
                f"({ratio:.2f}x). This is stated plainly as the finding: "
                f"recursive forecasting on this feature set degrades "
                f"substantially within six months, well inside the annual "
                f"window --mode deliverable produces. See DECISIONS.md's "
                f"recursive-forecast ADR for what horizon this actually "
                f"supports.\n"
            )
        else:
            lines.append(
                f"Error grew by {ratio:.2f}x across the horizon measured here, "
                f"under the 3x threshold used elsewhere in this project to call "
                f"an effect large. Stated as measured, not rounded up.\n"
            )
    else:
        lines.append(
            "Week 1 or week 26 bucket unavailable in this validation window - "
            "see the table above for exactly what horizons were measured.\n"
        )

    lines.append(f"![MAE by horizon]({os.path.basename(DEGRADATION_PLOT)})\n")
    lines.append(
        "REQUIRES HUMAN READ: the plot above asserts, and only a human read "
        "can confirm, that (1) each road's line is monotonically labelled "
        "and coloured consistently with the legend, (2) the OVERALL_excl_West "
        "line (black, heavier) excludes West as claimed, and (3) the shape of "
        "the curve (flat, rising, or erratic) matches the numbers in the "
        "table above rather than an artifact of the plotting library's "
        "default axis scaling.\n"
    )

    with open(DEGRADATION_OUTPUT, "w") as fh:
        fh.write("\n".join(lines))
    print(f"Wrote {DEGRADATION_OUTPUT}")

    print("\nMAE by horizon (OVERALL_excl_West):")
    for r in table:
        print(f"  week {r['week']:>2} ({r['hours']}): {r['OVERALL_excl_West']:.3f}")
    if ratio is not None:
        print(f"week26/week1 ratio: {ratio:.2f}x")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["deliverable", "validation"], required=True)
    args = parser.parse_args()

    if args.mode == "deliverable":
        run_deliverable()
    else:
        run_validation()

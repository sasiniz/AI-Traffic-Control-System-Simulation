"""
Stage 2 of the AI pipeline: trains ONE Random Forest to predict vehicle
counts per road per hour. It does NOT convert counts into green seconds -
that is Stage 3, a separate deterministic layer (not yet written). See
ADR-007 in DECISIONS.md.

DECISIONS ALREADY MADE - NOT REVISITED HERE
---------------------------------------------
ONE MODEL, not four: the road_East/North/South/West one-hot columns from
data_prep.py let a tree split by road, so a single model learns
road-specific behaviour where it exists while sharing the hour-of-day and
weekday structure common to all four roads. One model trains on all
40,320 training rows; four separate models would train on ~10,080 rows
each. It is also one artefact to sign and deploy at Stage 7, not four.

TRAIN ON ALL ROWS, including flagged peaks. temporal_split_without_outliers
is deliberately not called here - it exists for the Stage 6 Isolation
Forest only. A count predictor trained without peaks would systematically
underpredict rush hour, which is exactly the hour green time allocation
matters most. See ADR-010.

Feature selection goes through feature_matrix() from data_prep.py, never
column selection here. ID is excluded there because it encodes date, hour
and road at once, so a tree could memorise individual rows through it
rather than learning a generalisable pattern.

METRICS: MAE and RMSE only. NOT MAPE - West falls to as little as 1
vehicle/hour in the test set, where percentage error explodes (100%+ for
missing by a single car) and would dominate any blended score for no
good reason. RMSE is reported alongside MAE because it penalises large
errors harder, and large errors are exactly the peak hours that drive
green time allocation - a metric that does not weight them is scoring the
wrong thing for this system's purpose.

WHY THE NAIVE BASELINE IS COMPUTED FIRST, BEFORE ANY MODEL
-------------------------------------------------------------
An accuracy number means nothing on its own. The baseline here is "predict
each hour as the same hour one week ago" (lag_168) - if the Random Forest
cannot beat that, it is not earning its place in the system and the report
has to say so plainly. Computing and printing the baseline before the
model is trained makes that comparison impossible to skip or bury.

HYPERPARAMETERS AND TARGET MODE: SELECTED ON VALIDATION, NOT TUNED HERE
----------------------------------------------------------------------------
n_estimators=100, max_depth=10 and the DIFF target (see below) were not
chosen in this file. They were selected by model_selection.py, which
compared 39 configurations - 5 model families x 3 target modes - on a
12-week validation split carved out of TRAIN, never on test. See
results/MODEL_SELECTION.md for the full comparison and results/RESULTS_LOG.md
for the provenance trail. This file trains the SELECTED configuration and
reports it under two TEST protocols (static and rolling, both below) - test
is touched here for measurement, never for selection, matching ADR-013.

WHY THE TARGET IS Vehicles - lag_168 ("diff"), NOT Vehicles DIRECTLY
-------------------------------------------------------------------------
A Random Forest predicts the mean of the training rows in a leaf, so its
raw-count predictions cannot exceed the maximum Vehicles value seen in
training. Demand keeps rising past the train/test boundary (ADR-008), and
the static-protocol raw-target model lost to the naive baseline as a
direct result (see results/MODEL_SELECTION.md, section 6). Training on
Vehicles - lag_168 instead, and adding lag_168 back at predict time, moves
the ceiling: lag_168 is an actual historical count carried forward from the
input row, not a value the forest has to have seen in training, so the
model's OUTPUT is no longer pinned to a fixed training-period maximum even
though the underlying forest's DIFFERENCE predictions still are. This is
why the rolling protocol (which keeps lag_168 current via weekly refits)
recovers accuracy that the static protocol cannot - see STEP 4b below.

The inversion is INTERNAL to DiffTargetRandomForest (model_wrapper.py, not
this file - see that module's docstring for why it lives separately).
Every caller of model.predict() - this file, a future Stage 3 allocation
layer, anything that joblib.load()s models/count_model.joblib - receives
vehicle counts. Nothing downstream ever sees, or needs to know about, the
diff representation.

WHY WEST IS REPORTED SEPARATELY, NEVER BLENDED INTO A HEADLINE FIGURE
--------------------------------------------------------------------------
West trains entirely on synthetic data and tests entirely on real data
(ADR-002). Its test-set error measures how well the synthetic generator
resembles real West traffic, not how well the model forecasts West
traffic. Folding it into an overall MAE/RMSE would quietly average a
different measurement into the headline number, so overall figures in
this file are computed over East/North/South only, and West always gets
its own clearly labelled line.

WHY PERMUTATION IMPORTANCE, NOT feature_importances_
---------------------------------------------------------
scikit-learn's built-in impurity-based importance is biased toward
continuous, high-cardinality features. lag_168 and roll_mean_24 offer far
more possible split points than a one-hot road column or is_weekend, so
they would tend to dominate the built-in ranking partly because of that,
not only because of genuine predictive value. Permutation importance
instead measures the actual drop in model performance when a feature's
values are shuffled, which is not subject to that bias. Both rankings are
printed here so any disagreement between them is visible rather than
hidden behind a single number.

WHY THE TRAINING-RANGE LIMITATION IS REPORTED, NOT FIXED
--------------------------------------------------------------
The underlying forest still cannot output a DIFFERENCE (Vehicles - lag_168)
outside the range of differences it saw in training - that part of the
leaf-mean argument is unchanged by the target transform. What changed is
where the ceiling sits in VEHICLE-COUNT terms: under the static protocol,
lag_168 in the test period is real, later, higher-level data than anything
lag_168 held during training, so the effective count ceiling
(train-diff-max + test-period lag_168) floats upward with demand even
though the forest's own diff predictions do not. This is reported as a
known limitation, still measured directly (see STEP 7) rather than assumed
away, and it is not clipped, corrected or hidden, because doing so would
misrepresent what the model actually learned.

STATIC vs ROLLING PROTOCOL, BOTH REPORTED
---------------------------------------------
STATIC (STEP 4): fit once on the full training period, predict all of
test - what every earlier Stage 2 run in this project did.
ROLLING (STEP 4b): refit every 7 days on everything seen so far (an
expanding window that, for later weeks, includes earlier, now-realised
test weeks as training data) and predict only that week - matching
ADR-012's weekly regeneration. This duplicates a small amount of logic
from model_selection.py rather than importing it, on the same reasoning
ADR-019 gives for explore_features.py's independence from data_prep.py:
this is the production evaluation and should not depend on the
exploratory selection script.

Both protocols are measured and both are written to models/model_card.json
(STEP 8) - the rolling figures are labelled deployment-representative
there, per ADR-012, because weekly regeneration is how this model is
actually meant to run, not a fit-once-forget-it artefact.
"""

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.inspection import permutation_importance

from data_prep import (
    DATA_PATH,
    FEATURE_COLUMNS,
    SPLIT_DATE,
    TARGET_COLUMN,
    feature_matrix,
    load_and_engineer,
    temporal_split,
)
from model_wrapper import DiffTargetRandomForest

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "count_model.joblib"
MODEL_CARD_PATH = MODEL_DIR / "model_card.json"

# Selected by model_selection.py's validation grid - see
# results/MODEL_SELECTION.md. Not tuned in this file.
N_ESTIMATORS = 100
MAX_DEPTH = 10
TARGET_MODE = "diff"  # Vehicles - lag_168, inverted internally - see DiffTargetRandomForest
RANDOM_STATE = 42

ROLL_WEEK_DAYS = 7  # rolling-protocol refit cadence, matching ADR-012

ROADS = ["East", "North", "South", "West"]

# DiffTargetRandomForest lives in model_wrapper.py, not here - see that
# file's docstring for why (joblib.load() from any OTHER script would
# otherwise fail, since this file's classes pickle under "__main__" when
# run the normal way: `python train_model.py`).

# Naive baseline values computed independently on 2026-08-03 (recorded for
# reproducibility). This run's own baseline is compared against these
# below; if they differ, the run stops rather than continuing on data or
# code that no longer matches what was verified. Never adjusted to force
# a match - see the task history in DECISIONS.md.
EXPECTED_BASELINE = {
    "North": {"MAE": 6.30, "RMSE": 9.31},
    "South": {"MAE": 2.88, "RMSE": 3.73},
    "East": {"MAE": 6.49, "RMSE": 12.54},
    "West": {"MAE": 2.65, "RMSE": 3.66},
    "ALL": {"MAE": 4.58, "RMSE": 8.23},
}
BASELINE_TOLERANCE = 0.005  # matches the two decimal places the values were recorded at


def mae_rmse(actual, predicted):
    """Plain MAE/RMSE, computed by hand rather than via sklearn.metrics so
    behaviour does not depend on a particular sklearn version's API for
    root-mean-squared-error (the `squared=` argument has moved around
    across versions)."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    mae = float(np.mean(np.abs(actual - predicted)))
    rmse = float(np.sqrt(np.mean((actual - predicted) ** 2)))
    return mae, rmse


def main():
    print("=== Stage 1 features (via data_prep.load_and_engineer) ===")
    features, rows_dropped = load_and_engineer(DATA_PATH)
    print(f"Rows dropped for NaN lag_168/lag_336/roll_168_lag168 upstream: {rows_dropped}")
    print(f"Feature rows: {len(features)}")

    train_df, test_df = temporal_split(features)
    print(f"Train rows: {len(train_df)}   Test rows: {len(test_df)}   split_date={SPLIT_DATE}")

    # Train on ALL rows, outliers included - ADR-010. Do not call
    # temporal_split_without_outliers here.
    X_train, y_train = feature_matrix(train_df)
    X_test, y_test = feature_matrix(test_df)
    actual = y_test.to_numpy()

    # ---------------------------------------------------------------
    # STEP 2: naive baseline. Predict each test row as its own lag_168 -
    # the same hour, one week earlier. Computed and printed before any
    # model exists - see module docstring.
    # ---------------------------------------------------------------
    print()
    print("=== Naive baseline: predict lag_168 (same hour, 1 week ago) ===")
    baseline_pred = test_df["lag_168"].to_numpy()

    baseline_metrics = {}
    for road in ["North", "South", "East", "West"]:
        mask = (test_df["Road"] == road).to_numpy()
        mae, rmse = mae_rmse(actual[mask], baseline_pred[mask])
        baseline_metrics[road] = {"MAE": mae, "RMSE": rmse}
        print(f"{road:6s}  MAE={mae:.2f}  RMSE={rmse:.2f}")
    mae_all, rmse_all = mae_rmse(actual, baseline_pred)
    baseline_metrics["ALL"] = {"MAE": mae_all, "RMSE": rmse_all}
    print(f"{'ALL':6s}  MAE={mae_all:.2f}  RMSE={rmse_all:.2f}")

    print()
    print("=== Baseline vs recorded values (2026-08-03) ===")
    baseline_mismatches = []
    for road, expected in EXPECTED_BASELINE.items():
        got = baseline_metrics[road]
        if (abs(got["MAE"] - expected["MAE"]) > BASELINE_TOLERANCE
                or abs(got["RMSE"] - expected["RMSE"]) > BASELINE_TOLERANCE):
            baseline_mismatches.append(
                f"{road}: expected MAE={expected['MAE']} RMSE={expected['RMSE']}, "
                f"got MAE={got['MAE']:.2f} RMSE={got['RMSE']:.2f}"
            )
    if baseline_mismatches:
        print("MISMATCH vs recorded baseline values:")
        for m in baseline_mismatches:
            print(" -", m)
        print()
        print("STOPPING here, as instructed - not adjusting code or data to "
              "force a match, and not training a model on top of an "
              "unverified baseline.")
        return
    print("All baseline values match the recorded state exactly.")

    # ---------------------------------------------------------------
    # STEP 3: train the selected configuration (STATIC protocol - fit
    # once on the full training period). No hyperparameter tuning here -
    # n_estimators/max_depth/target mode were selected by
    # model_selection.py on validation - and no outlier exclusion - see
    # module docstring and ADR-010.
    # ---------------------------------------------------------------
    print()
    print("=== Training DiffTargetRandomForest (STATIC protocol) ===")
    model = DiffTargetRandomForest(
        n_estimators=N_ESTIMATORS,
        max_depth=MAX_DEPTH,
        random_state=RANDOM_STATE,
    )
    t0 = time.time()
    model.fit(X_train, y_train)
    print(f"Trained on {len(X_train)} rows in {time.time() - t0:.1f}s "
          f"(n_estimators={N_ESTIMATORS}, max_depth={MAX_DEPTH}, "
          f"random_state={RANDOM_STATE}, target={TARGET_MODE})")

    # ---------------------------------------------------------------
    # STEP 4: evaluate on the test split. East/North/South form the
    # headline "overall" figure; West is measured and printed but never
    # blended into it - see module docstring. model.predict() already
    # returns vehicle counts (DiffTargetRandomForest inverts internally).
    # ---------------------------------------------------------------
    y_pred = model.predict(X_test)

    print()
    print("=== Test set evaluation ===")
    model_metrics = {}
    for road in ["East", "North", "South"]:
        mask = (test_df["Road"] == road).to_numpy()
        mae, rmse = mae_rmse(actual[mask], y_pred[mask])
        mean_actual = float(actual[mask].mean())
        mean_pred = float(y_pred[mask].mean())
        model_metrics[road] = {
            "MAE": mae, "RMSE": rmse,
            "mean_actual": mean_actual, "mean_predicted": mean_pred,
        }
        print(f"{road:6s}  MAE={mae:.2f}  RMSE={rmse:.2f}  "
              f"mean_actual={mean_actual:.2f}  mean_predicted={mean_pred:.2f}")

    overall_mask = (test_df["Road"] != "West").to_numpy()
    mae_o, rmse_o = mae_rmse(actual[overall_mask], y_pred[overall_mask])
    mean_actual_o = float(actual[overall_mask].mean())
    mean_pred_o = float(y_pred[overall_mask].mean())
    model_metrics["OVERALL_excl_West"] = {
        "MAE": mae_o, "RMSE": rmse_o,
        "mean_actual": mean_actual_o, "mean_predicted": mean_pred_o,
    }
    print(f"{'OVERALL':6s}  MAE={mae_o:.2f}  RMSE={rmse_o:.2f}  "
          f"mean_actual={mean_actual_o:.2f}  mean_predicted={mean_pred_o:.2f}  "
          f"(East+North+South; West excluded - see below)")

    print()
    print("West - trains on synthetic data, tests on real data (ADR-002). "
          "Not comparable to the other three roads and NOT included in "
          "the OVERALL figure above:")
    mask = (test_df["Road"] == "West").to_numpy()
    mae_w, rmse_w = mae_rmse(actual[mask], y_pred[mask])
    mean_actual_w = float(actual[mask].mean())
    mean_pred_w = float(y_pred[mask].mean())
    model_metrics["West"] = {
        "MAE": mae_w, "RMSE": rmse_w,
        "mean_actual": mean_actual_w, "mean_predicted": mean_pred_w,
    }
    print(f"{'West':6s}  MAE={mae_w:.2f}  RMSE={rmse_w:.2f}  "
          f"mean_actual={mean_actual_w:.2f}  mean_predicted={mean_pred_w:.2f}")

    # ---------------------------------------------------------------
    # STEP 4b: ROLLING protocol - refit every ROLL_WEEK_DAYS on an
    # expanding window, predict only that week, matching ADR-012's weekly
    # regeneration. Duplicated from model_selection.py rather than
    # imported - see the module docstring's "STATIC vs ROLLING PROTOCOL"
    # section for why.
    # ---------------------------------------------------------------
    print()
    print("=== Training DiffTargetRandomForest (ROLLING protocol) ===")
    test_start = pd.Timestamp(SPLIT_DATE)
    test_end_exclusive = test_df["DateTime"].max() + pd.Timedelta(hours=1)
    week_starts = []
    cur = test_start
    while cur < test_end_exclusive:
        week_starts.append(cur)
        cur += pd.Timedelta(days=ROLL_WEEK_DAYS)

    rolling_pred = np.full(len(test_df), np.nan)
    t0 = time.time()
    for week_start in week_starts:
        week_end = min(week_start + pd.Timedelta(days=ROLL_WEEK_DAYS), test_end_exclusive)
        # Expanding window: everything available by week_start, which for
        # later weeks includes EARLIER, now-realised test weeks - exactly
        # what weekly regeneration means in deployment.
        rolling_train_df = features.loc[features["DateTime"] < week_start]
        week_mask = ((test_df["DateTime"] >= week_start)
                     & (test_df["DateTime"] < week_end)).to_numpy()

        X_roll, y_roll = feature_matrix(rolling_train_df)
        week_model = DiffTargetRandomForest(
            n_estimators=N_ESTIMATORS, max_depth=MAX_DEPTH, random_state=RANDOM_STATE,
        )
        week_model.fit(X_roll, y_roll)

        X_week, _ = feature_matrix(test_df.loc[week_mask])
        rolling_pred[week_mask] = week_model.predict(X_week)

    n_refits = len(week_starts)
    assert not np.isnan(rolling_pred).any(), (
        "rolling protocol left some test rows unpredicted - week boundaries "
        "do not cover the full test period"
    )
    print(f"{n_refits} refits, one per {ROLL_WEEK_DAYS}-day block, "
          f"{week_starts[0]} to {week_starts[-1]}, in {time.time() - t0:.1f}s total")

    rolling_metrics = {}
    for road in ["East", "North", "South"]:
        mask = (test_df["Road"] == road).to_numpy()
        mae, rmse = mae_rmse(actual[mask], rolling_pred[mask])
        rolling_metrics[road] = {"MAE": mae, "RMSE": rmse}
        print(f"{road:6s}  MAE={mae:.2f}  RMSE={rmse:.2f}")
    overall_mask = (test_df["Road"] != "West").to_numpy()
    mae_ro, rmse_ro = mae_rmse(actual[overall_mask], rolling_pred[overall_mask])
    rolling_metrics["OVERALL_excl_West"] = {"MAE": mae_ro, "RMSE": rmse_ro}
    print(f"{'OVERALL':6s}  MAE={mae_ro:.2f}  RMSE={rmse_ro:.2f}  "
          f"(East+North+South; deployment-representative per ADR-012)")
    mask = (test_df["Road"] == "West").to_numpy()
    mae_rw, rmse_rw = mae_rmse(actual[mask], rolling_pred[mask])
    rolling_metrics["West"] = {"MAE": mae_rw, "RMSE": rmse_rw}
    print(f"{'West':6s}  MAE={mae_rw:.2f}  RMSE={rmse_rw:.2f}")

    # ---------------------------------------------------------------
    # STEP 5: baseline vs model, side by side, per road. STATIC and
    # ROLLING both shown against the same baseline.
    # ---------------------------------------------------------------
    print()
    print("=== Baseline vs model, per road (STATIC and ROLLING) ===")
    header = (f"{'Road':6s}  {'Base MAE':>9s}  {'Base RMSE':>9s}  "
              f"{'Static MAE':>10s}  {'Static RMSE':>11s}  "
              f"{'Roll MAE':>9s}  {'Roll RMSE':>10s}  "
              f"{'Static beats?':>13s}  {'Roll beats?':>11s}")
    print(header)
    for road in ["East", "North", "South", "West"]:
        b = baseline_metrics[road]
        m = model_metrics[road]
        r = rolling_metrics[road]
        static_beat = "YES" if (m["MAE"] < b["MAE"] and m["RMSE"] < b["RMSE"]) else "NO"
        roll_beat = "YES" if (r["MAE"] < b["MAE"] and r["RMSE"] < b["RMSE"]) else "NO"
        print(f"{road:6s}  {b['MAE']:9.2f}  {b['RMSE']:9.2f}  "
              f"{m['MAE']:10.2f}  {m['RMSE']:11.2f}  "
              f"{r['MAE']:9.2f}  {r['RMSE']:10.2f}  "
              f"{static_beat:>13s}  {roll_beat:>11s}")

    # ---------------------------------------------------------------
    # STEP 6: permutation importance (test set) vs built-in
    # feature_importances_ (train-derived), side by side. Scoring is
    # explicit rather than left at permutation_importance's default
    # (which would use DiffTargetRandomForest.score(), i.e. R^2 via
    # RegressorMixin) because neg_mean_absolute_error matches this
    # project's chosen metric - see module docstring's METRICS section.
    # ---------------------------------------------------------------
    print()
    print("=== Feature importance: permutation (test set) vs built-in ===")
    perm = permutation_importance(
        model, X_test, y_test, n_repeats=5, random_state=RANDOM_STATE, n_jobs=-1,
        scoring="neg_mean_absolute_error",
    )
    importance_df = pd.DataFrame({
        "feature": FEATURE_COLUMNS,
        "builtin_importance": model.feature_importances_,
        "perm_importance_mean": perm.importances_mean,
        "perm_importance_std": perm.importances_std,
    })
    importance_df["builtin_rank"] = importance_df["builtin_importance"].rank(
        ascending=False, method="min").astype(int)
    importance_df["perm_rank"] = importance_df["perm_importance_mean"].rank(
        ascending=False, method="min").astype(int)
    importance_df = importance_df.sort_values("perm_rank").reset_index(drop=True)
    print(importance_df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    disagreements = importance_df[importance_df["builtin_rank"] != importance_df["perm_rank"]]
    print()
    if len(disagreements) > 0:
        print(f"{len(disagreements)} of {len(FEATURE_COLUMNS)} features rank "
              f"differently between the two methods:")
        print(disagreements[["feature", "builtin_rank", "perm_rank"]].to_string(index=False))
    else:
        print("Both methods agree on every feature's rank.")

    # ---------------------------------------------------------------
    # STEP 7: training-range limitation. Not clipped or corrected.
    # ---------------------------------------------------------------
    print()
    print("=== Training target range limitation (not corrected) ===")
    train_min = float(y_train.min())
    train_max = float(y_train.max())
    print(f"Training target range: [{train_min:.0f}, {train_max:.0f}]")

    above_max = actual > train_max
    below_min = actual < train_min
    outside = above_max | below_min
    print(f"Test rows with actual outside training range: {int(outside.sum())} "
          f"of {len(actual)} ({100 * outside.mean():.2f}%)")
    print(f"  ...above training max: {int(above_max.sum())} ({100 * above_max.mean():.2f}%)")
    print(f"  ...below training min: {int(below_min.sum())} ({100 * below_min.mean():.2f}%)")

    print("Per road, actual above training max:")
    for road in ROADS:
        mask = (test_df["Road"] == road).to_numpy()
        n_above = int(above_max[mask].sum())
        pct = 100 * above_max[mask].mean() if mask.sum() else 0.0
        print(f"  {road:6s}  {n_above:5d} / {int(mask.sum())}  ({pct:.2f}%)")

    residual = actual - y_pred  # positive = under-prediction (actual > predicted)
    worst_idx = int(np.argmax(residual))
    worst_row = test_df.iloc[worst_idx]
    print()
    print(f"Largest under-prediction: actual={actual[worst_idx]:.0f}, "
          f"predicted={y_pred[worst_idx]:.1f}, "
          f"under by {residual[worst_idx]:.1f}, "
          f"road={worst_row['Road']}, DateTime={worst_row['DateTime']}")
    print("Not clipped or corrected - reported as a known limitation of a "
          "leaf-mean regressor evaluated on a rising trend (see ADR-008).")

    # ---------------------------------------------------------------
    # STEP 8: save the model and its model card.
    # ---------------------------------------------------------------
    print()
    print("=== Saving artefacts ===")
    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")

    model_card = {
        "training_row_count": int(len(X_train)),
        "test_row_count": int(len(X_test)),
        "split_date": SPLIT_DATE,
        "feature_columns": FEATURE_COLUMNS,
        "sklearn_version": sklearn.__version__,
        "n_estimators": N_ESTIMATORS,
        "max_depth": MAX_DEPTH,
        "target_mode": TARGET_MODE,
        "random_state": RANDOM_STATE,
        "selected_via": "model_selection.py validation grid - see results/MODEL_SELECTION.md",
        "protocols": {
            "static": {
                "description": "fit once on the full training period, predict all of test",
                "test_metrics_per_road": {
                    road: {"MAE": round(model_metrics[road]["MAE"], 4),
                           "RMSE": round(model_metrics[road]["RMSE"], 4)}
                    for road in ["East", "North", "South", "West"]
                },
            },
            "rolling": {
                "description": (
                    f"refit every {ROLL_WEEK_DAYS} days on an expanding window, "
                    "predict only that week - matches ADR-012's weekly regeneration"
                ),
                "n_refits": n_refits,
                "deployment_representative": True,
                "test_metrics_per_road": {
                    road: {"MAE": round(rolling_metrics[road]["MAE"], 4),
                           "RMSE": round(rolling_metrics[road]["RMSE"], 4)}
                    for road in ["East", "North", "South", "West"]
                },
            },
        },
    }
    with open(MODEL_CARD_PATH, "w") as fh:
        json.dump(model_card, fh, indent=2)
    print(f"Model card saved to {MODEL_CARD_PATH}")
    print(json.dumps(model_card, indent=2))


if __name__ == "__main__":
    main()
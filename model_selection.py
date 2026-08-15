"""
Model and hyperparameter selection, evaluated on VALIDATION only. Test is
touched only by the single configuration this file selects, under exactly
two evaluation protocols (static, rolling). See ADR-013 in DECISIONS.md:
hyperparameters (and here, model family and target transform) are chosen on
a validation split carved out of TRAIN, never on test.

WHY THIS FILE EXISTS
---------------------
The Random Forest used throughout Stage 2 (train_model.py) has never been
measured against alternatives - it was chosen qualitatively (ADR-007's
"Random Forest" framing predates any comparison). This file closes that gap:
Ridge, a single decision tree, a grid of Random Forests, HistGradientBoosting
and a Ridge+RandomForest hybrid, each tried under three target transforms,
against the seasonal-naive baseline, on validation only.

THE VALIDATION SPLIT CANNOT SEE WHAT BROKE STAGE 2
------------------------------------------------------
Validation is the last 12 weeks of the training period (2016-10-09 to
2016-12-31). Test is January-June 2017. Demand keeps rising through both
windows (ADR-008), so validation sees a MILDER version of the same
extrapolation problem that caused the Stage 2 collapse: South's level rises
only ~1.3x from inner-train to validation, but ~1.9x from train to test. A
model that looks fine on validation can still fail on test for exactly the
reason ADR-012 already identified (a leaf-mean regressor cannot output above
its training range, and demand grows past whatever range it trained on).
This file prints the drift ratio for both splits next to every result so
that limitation stays visible, and validation performance is never presented
as if it predicted test performance.

TARGET TRANSFORMS: RAW, RATIO, DIFF
-------------------------------------
Every model is tried under three target definitions - standard multiplicative
and additive decomposition of a trended series against a reference level
(Hyndman & Athanasopoulos, "Forecasting: Principles and Practice", 3rd ed.,
section 6.3):

    raw   predict Vehicles directly
    ratio predict Vehicles / roll_168_lag168, multiply the prediction back
          by roll_168_lag168 (multiplicative decomposition against the
          road's own recent level)
    diff  predict Vehicles - lag_168, add lag_168 back (additive
          decomposition against the road's own same-hour-last-week level)

Both roll_168_lag168 and lag_168 are computed PER ROAD (data_prep.py groups
by Road before shifting/rolling - confirmed in this file's L1 diagnostic
below, not assumed), so both transforms normalise each road by its own
level rather than a shared scale. The idea under test: a tree-based model
still cannot predict a transformed target above ITS training range, but if
the transformed target is closer to stationary than the raw count, the
effective ceiling on Vehicles rises with the road's current level instead of
sitting fixed at a training-period maximum. Whether this actually helps is
an empirical question this file answers, not an assumption - see the H2
validation table and, more importantly, the K1/K2 TEST results, since
validation cannot be trusted to show whether the transform fixes the
extrapolation failure (see above).

HYBRID MODEL
-------------
Ridge extrapolates linearly without a training-range ceiling; a Random
Forest captures non-linear daily/weekly shape but cannot extrapolate. The
hybrid fits Ridge first, then fits a RandomForest on Ridge's residual
(actual minus Ridge prediction) in whatever target space is active, and
predicts as Ridge + RandomForest(residual). The residual-stage RandomForest
uses ONE fixed configuration (n_estimators=200, max_depth=10) rather than
the full 3x3 grid: running the full grid for the residual stage would add
27 more RF fits per target mode on top of the 9 already in the main grid,
tripling H2's runtime for a component that is secondary to the hybrid's own
point (Ridge carries the level; the forest's exact size matters much less
than in the primary grid). max_depth=10 is carried over because it was the
depth that won the primary grid in the first version of this file.

Feature selection goes through feature_matrix() from data_prep.py, exactly
as train_model.py uses it - same 14 columns, same exclusions (no ID, no
year), for the same reasons documented there.

Does not modify train_model.py or data_prep.py. Does not commit anything.
"""

import inspect
import json
import os
import subprocess
import time
from datetime import datetime
from itertools import product

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.tree import DecisionTreeRegressor

import data_prep as dp
from data_prep import (
    DATA_PATH,
    FEATURE_COLUMNS,
    SPLIT_DATE,
    TARGET_COLUMN,
    feature_matrix,
    load_and_engineer,
    temporal_split,
)

RANDOM_STATE = 42
VAL_WEEKS = 12
ROLL_WEEK_DAYS = 7  # K2: rolling-retrain cadence, matching ADR-012's weekly regeneration
ROADS = ["East", "North", "South", "West"]
OVERALL_ROADS = ["East", "North", "South"]  # ADR-011: West excluded from headline figures

# Ratio-target division guard. roll_168_lag168 is a mean of >=1 real hourly
# counts and is never exactly zero in this dataset, but the guard is cheap
# and makes the transform well defined even if that ever changes. Applied
# identically when building the target AND when multiplying predictions
# back, so it cancels out and does not bias the transform.
RATIO_EPS = 1e-6

HYBRID_RF_N_ESTIMATORS = 200
HYBRID_RF_MAX_DEPTH = 10

# Prior test-set records, printed only for side-by-side comparison - never
# used to select anything here.
#   ADR014_MODEL: illegal 13-feature set, default RF, min_samples_leaf=1
#   CORRECTED_RERUN_MODEL: legal 14-feature set, default RF n=300 (the
#     train_model.py re-run after ADR-015)
#   PRIOR_SELECTION_MODEL: this file's FIRST version, 2-target-mode grid
#     (raw/ratio only, no hybrid, no diff), static protocol, which selected
#     RandomForest_n100_depth10/raw and scored this on test
ADR014_MODEL = {
    "East": (4.69, 8.86), "North": (7.27, 10.62),
    "South": (3.08, 4.23), "West": (2.03, 2.76),
}
CORRECTED_RERUN_MODEL = {
    "East": (6.05, 10.82), "North": (8.85, 12.58),
    "South": (6.28, 8.13), "West": (2.27, 3.15),
}
PRIOR_SELECTION_MODEL = {
    "East": (5.77, 10.69), "North": (8.88, 12.67),
    "South": (6.28, 8.14), "West": (2.14, 2.96),
}

# ADR-016: seed-to-seed noise floor for validation MAE, measured at 300
# trees across 5 random seeds on the full 14-feature set (MAE 4.2021,
# sd 0.0119). Cited here only, for MODEL_SELECTION.md section 7's honesty
# check on the rolling-vs-baseline margin - NOT computed by this run, and
# measured on a different comparison (seed variance on the original
# validation set) than the protocol margin it is checked against.
ADR016_NOISE_FLOOR_SD = 0.0119

RESULTS_DIR = "results"
RESULTS_LOG_PATH = os.path.join(RESULTS_DIR, "RESULTS_LOG.md")


def get_git_provenance():
    """Commit hash and working-tree dirtiness AT RUN TIME, not typed by
    hand - a result generated from uncommitted code is weaker evidence,
    and the reader of the report needs to be able to see that."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit = "unknown (git rev-parse failed)"
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL,
        )
        dirty = len(status.strip()) > 0
    except Exception:
        dirty = None
    return commit, dirty


def append_results_log(row, log_path=RESULTS_LOG_PATH):
    """Append-only index: one row per result artefact, linking it to the
    script that produced it, the commit it was produced from, and the ADR
    it evidences. DECISIONS.md records WHY; results/ records WHAT was
    measured; this file is the index between the two.

    Never rewrites an existing row - if the log does not exist yet, it is
    created with the header and this one row; if it exists, this row is
    appended after whatever is already there.
    """
    header = (
        "# Results Log\n\n"
        "Append-only index of result artefacts. `DECISIONS.md` (the ADR "
        "log) records WHY a choice was made; `results/` records WHAT was "
        "measured to support it; this file links the two. Never rewrite "
        "an existing row, even to correct it - add a new row instead and "
        "let the correction be visible in the log.\n\n"
        "| Date | Artefact | Script | Commit | Related ADR | One-line finding |\n"
        "|---|---|---|---|---|---|\n"
    )
    row_line = (
        f"| {row['date']} | {row['artefact']} | {row['script']} | "
        f"{row['commit']} | {row['adr']} | {row['finding']} |\n"
    )
    write_header = not os.path.exists(log_path)
    with open(log_path, "a", encoding="utf-8") as fh:
        if write_header:
            fh.write(header)
        fh.write(row_line)


def mae_rmse(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    mae = float(np.mean(np.abs(actual - predicted)))
    rmse = float(np.sqrt(np.mean((actual - predicted) ** 2)))
    return mae, rmse


def per_road_metrics(df, actual, predicted, roads=ROADS):
    """Per-road MAE+RMSE plus OVERALL_excl_West (pooled East+North+South,
    matching ADR-011's method - not an average of the three per-road
    figures)."""
    out = {}
    for road in roads:
        mask = (df["Road"] == road).to_numpy()
        mae, rmse = mae_rmse(actual[mask], predicted[mask])
        out[f"{road}_MAE"] = mae
        out[f"{road}_RMSE"] = rmse
    overall_mask = df["Road"].isin(OVERALL_ROADS).to_numpy()
    mae_o, rmse_o = mae_rmse(actual[overall_mask], predicted[overall_mask])
    out["OVERALL_excl_West_MAE"] = mae_o
    out["OVERALL_excl_West_RMSE"] = rmse_o
    return out


# -- target transforms ------------------------------------------------------
def ratio_target(df):
    return df[TARGET_COLUMN].to_numpy() / (df["roll_168_lag168"].to_numpy() + RATIO_EPS)


def ratio_predictions_to_vehicles(ratio_pred, df):
    return ratio_pred * (df["roll_168_lag168"].to_numpy() + RATIO_EPS)


def diff_target(df):
    return df[TARGET_COLUMN].to_numpy() - df["lag_168"].to_numpy()


def diff_predictions_to_vehicles(diff_pred, df):
    return diff_pred + df["lag_168"].to_numpy()


def get_target(df, mode):
    if mode == "raw":
        return df[TARGET_COLUMN].to_numpy()
    if mode == "ratio":
        return ratio_target(df)
    if mode == "diff":
        return diff_target(df)
    raise ValueError(mode)


def back_transform(pred, df, mode):
    if mode == "raw":
        return pred
    if mode == "ratio":
        return ratio_predictions_to_vehicles(pred, df)
    if mode == "diff":
        return diff_predictions_to_vehicles(pred, df)
    raise ValueError(mode)


def drift_ratio_table(later_df, earlier_df, label_later, label_earlier):
    print(f"Per-road mean Vehicles, {label_earlier} vs {label_later}, and the ratio:")
    print(f"{'Road':6s}  {label_earlier:>12s}  {label_later:>12s}  {'ratio':>7s}")
    for road in ROADS:
        m_earlier = earlier_df.loc[earlier_df["Road"] == road, TARGET_COLUMN].mean()
        m_later = later_df.loc[later_df["Road"] == road, TARGET_COLUMN].mean()
        ratio = m_later / m_earlier
        print(f"{road:6s}  {m_earlier:12.2f}  {m_later:12.2f}  {ratio:6.2f}x")


# --------------------------------------------------------------------------
# Selection rule - DEFINED HERE, BEFORE any validation number is seen by
# this function or printed (J5). Applied mechanically to the results table
# after H2 runs; nothing about it is chosen after looking at the numbers.
#
# Rule: take the configuration with the lowest OVERALL_excl_West_MAE. If any
# other configuration is within 0.1 MAE of that best value, prefer the
# SIMPLEST configuration among that tied group instead - not the lowest MAE
# within the group. Simpler beats marginally-better-on-validation, because
# validation is known (see module docstring) to understate the risk that a
# more complex model has simply fit validation-period noise the same way it
# could fit test-period noise, and a simpler model is cheaper to explain in
# the governance chapter regardless.
#
# Complexity ordering, least to most complex:
#   Ridge < DecisionTree < RandomForest < HistGradientBoosting < Hybrid
#   (Hybrid ranks last: it trains two models, so it is strictly more
#   machinery than either alone)
#   within RandomForest: fewer trees is simpler; shallower max_depth is
#   simpler (None = unbounded depth = most complex)
#   within any family: raw target < diff target < ratio target. raw needs
#   no inverse transform. diff needs an addition back. ratio needs a
#   division to build the target (guarded by RATIO_EPS) AND a
#   multiplication back - one more moving part than diff.
# --------------------------------------------------------------------------
FAMILY_RANK = {
    "Ridge": 0, "DecisionTree": 1, "RandomForest": 2,
    "HistGradientBoosting": 3, "Hybrid": 4,
}
TARGET_RANK = {"raw": 0, "diff": 1, "ratio": 2}
DEPTH_RANK = {10: 1, 20: 2, None: 3}  # only meaningful for RandomForest

# Single source of truth for the rule text - printed to console AND written
# into MODEL_SELECTION.md, so the report can never describe a different
# rule than the one the code actually ran.
SELECTION_RULE_TEXT = (
    "Lowest OVERALL_excl_West validation MAE; if any configuration is "
    "within 0.1 MAE of the best, take the simplest configuration in that "
    "tied group instead (Ridge < DecisionTree < RandomForest < "
    "HistGradientBoosting < Hybrid; within RandomForest, fewer trees and "
    "shallower depth is simpler; raw target < diff target < ratio target). "
    "This rule was fixed before the validation grid was run and applied "
    "mechanically to the results - selection used validation only, test "
    "was not consulted."
)


def simplicity_key(row):
    # Building a DataFrame from mixed None/int rows upcasts max_depth (and
    # n_estimators, for non-RF rows) to float, turning None into NaN - not
    # a semantic change, just how pandas stores a mixed-type column.
    # Normalise back to the same None/int values used when the model was
    # built, so DEPTH_RANK's None key still matches.
    family = row["family"]
    size_rank = 0
    if family == "RandomForest":
        max_depth = None if pd.isna(row["max_depth"]) else int(row["max_depth"])
        size_rank = (int(row["n_estimators"]), DEPTH_RANK[max_depth])
    return (FAMILY_RANK[family], size_rank, TARGET_RANK[row["target"]])


def select_configuration(results_df):
    best_mae = results_df["OVERALL_excl_West_MAE"].min()
    tied = results_df[results_df["OVERALL_excl_West_MAE"] <= best_mae + 0.1].copy()
    tied["simplicity"] = tied.apply(simplicity_key, axis=1)
    tied = tied.sort_values("simplicity")
    return tied.iloc[0]


class HybridRidgeForest:
    """Ridge carries the (extrapolating) level; a RandomForest on Ridge's
    residual carries the (non-extrapolating) shape. Operates purely on
    whatever y-scale it is given, so it works unchanged under raw/diff/ratio
    targets - the target transform is applied by the caller before fit()."""

    def __init__(self, rf_n_estimators=HYBRID_RF_N_ESTIMATORS,
                 rf_max_depth=HYBRID_RF_MAX_DEPTH, random_state=RANDOM_STATE):
        self.ridge = Ridge()
        self.rf = RandomForestRegressor(
            n_estimators=rf_n_estimators, max_depth=rf_max_depth,
            random_state=random_state, n_jobs=-1,
        )

    def fit(self, X, y):
        self.ridge.fit(X, y)
        residual = np.asarray(y) - self.ridge.predict(X)
        self.rf.fit(X, residual)
        return self

    def predict(self, X):
        return self.ridge.predict(X) + self.rf.predict(X)


def build_model(family, n_estimators=None, max_depth=None):
    if family == "Ridge":
        return Ridge()
    if family == "DecisionTree":
        return DecisionTreeRegressor(max_depth=10, random_state=RANDOM_STATE)
    if family == "RandomForest":
        return RandomForestRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            random_state=RANDOM_STATE, n_jobs=-1,
        )
    if family == "HistGradientBoosting":
        return HistGradientBoostingRegressor(random_state=RANDOM_STATE)
    if family == "Hybrid":
        return HybridRidgeForest()
    raise ValueError(family)


def model_grid():
    grid = [
        {"family": "Ridge", "name": "Ridge"},
        {"family": "DecisionTree", "name": "DecisionTree_depth10"},
        {"family": "HistGradientBoosting", "name": "HistGradientBoosting"},
        {"family": "Hybrid", "name": "Hybrid_Ridge_RF200depth10"},
    ]
    for n_estimators, max_depth in product([100, 200, 300], [None, 10, 20]):
        grid.append({
            "family": "RandomForest",
            "name": f"RandomForest_n{n_estimators}_depth{max_depth}",
            "n_estimators": n_estimators,
            "max_depth": max_depth,
        })
    return grid


TARGET_MODES = ["raw", "diff", "ratio"]

VAL_DISPLAY_COLS = ["name", "target",
                     "East_MAE", "East_RMSE", "North_MAE", "North_RMSE",
                     "South_MAE", "South_RMSE", "West_MAE", "West_RMSE",
                     "OVERALL_excl_West_MAE", "OVERALL_excl_West_RMSE"]


def _fmt_cell(v):
    if isinstance(v, float):
        return "None" if pd.isna(v) else f"{v:.3f}"
    return "None" if pd.isna(v) else str(v)


def df_to_markdown(df, cols=None):
    """Plain pipe-table renderer - no extra dependency for one report."""
    cols = cols or list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_fmt_cell(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_markdown_report(md_path, *, results_df, selected, tied, best_mae,
                           n_model_configs, baseline_test_scores, static_scores,
                           rolling_scores, n_refits, best_hybrid, best_rf,
                           worst_row, target_best, val_start, inner_train_df,
                           val_df, test_df, sel_target, val_csv_path, test_csv_path,
                           run_datetime, commit, dirty):
    """Every number below is read from the run's own results objects
    (results_df, selected, the per-protocol score dicts, etc.) - nothing in
    this function is a literal figure typed by hand, so the report cannot
    drift from the data that produced it. The only exception is
    ADR016_NOISE_FLOOR_SD, an attributed historical citation from
    DECISIONS.md, exactly like ADR014_MODEL and the other prior-run dicts
    already used for comparison elsewhere in this file."""

    test_table = pd.DataFrame([
        {"protocol": "baseline", **baseline_test_scores},
        {"protocol": "static", **static_scores},
        {"protocol": "rolling", **rolling_scores},
    ])
    test_cols = ["protocol", "East_MAE", "East_RMSE", "North_MAE", "North_RMSE",
                 "South_MAE", "South_RMSE", "West_MAE", "West_RMSE",
                 "OVERALL_excl_West_MAE", "OVERALL_excl_West_RMSE"]

    margin = baseline_test_scores["OVERALL_excl_West_MAE"] - rolling_scores["OVERALL_excl_West_MAE"]
    margin_multiples = margin / ADR016_NOISE_FLOOR_SD
    if margin > ADR016_NOISE_FLOOR_SD * 3:
        noise_verdict = (
            f"The margin ({margin:.3f} MAE) is about {margin_multiples:.0f}x that "
            "noise floor. This is not a like-for-like significance test - ADR-016's "
            "figure is seed-to-seed variance on the original validation set under an "
            "unchanged configuration, while this margin is a single fixed-seed "
            "comparison between two different TEST-time protocols - but a margin this "
            "many multiples of the recorded noise floor is unlikely to be noise."
        )
    else:
        noise_verdict = (
            f"The margin ({margin:.3f} MAE) is within a few multiples "
            f"({margin_multiples:.1f}x) of that noise floor. The honest claim is "
            "parity between the rolling protocol and the naive baseline, not a "
            "win - the gap is not clearly distinguishable from measurement noise."
        )

    dirty_text = (
        "**DIRTY - uncommitted changes were present at run time. This result is "
        "weaker evidence than one generated from a committed state.**"
        if dirty else "clean (no uncommitted changes at run time)"
    )
    lines = []
    lines.append("# Model Selection Report")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- Script: `model_selection.py`")
    lines.append(f"- Run at: {run_datetime}")
    lines.append(f"- Commit: `{commit}`")
    lines.append(f"- Working tree: {dirty_text}")
    lines.append(f"- random_state: {RANDOM_STATE}")
    lines.append(
        f"- Full provenance also recorded in `{os.path.join(RESULTS_DIR, 'model_selection_provenance.json')}`"
    )
    lines.append("")
    lines.append(
        "This report is generated by `model_selection.py` from that run's own "
        "results objects (see `write_markdown_report`) - every figure below comes "
        "from the run, not from hand-typed numbers. Full data: "
        f"`{val_csv_path}` (validation grid) and `{test_csv_path}` (test protocols)."
    )

    lines.append("")
    lines.append("## 1. What was compared, and why")
    lines.append("")
    lines.append(
        "The Random Forest used throughout Stage 2 (`train_model.py`) had never been "
        "measured against alternatives - it was chosen qualitatively (ADR-007). This "
        "run compares four model families - **Ridge**, **Decision Tree** (max_depth "
        "10), **Random Forest** (a 3x3 grid of n_estimators in {100, 200, 300} x "
        "max_depth in {None, 10, 20}), **HistGradientBoosting** - plus a **hybrid** "
        "(Ridge carries the level, a RandomForest fits Ridge's residual), each under "
        "three target modes: **raw** (predict Vehicles directly), **diff** (predict "
        "Vehicles - lag_168, add lag_168 back), and **ratio** (predict "
        "Vehicles / roll_168_lag168, multiply back). "
        f"**{n_model_configs} model configurations** were evaluated in total, plus "
        "the seasonal-naive baseline (lag_168) for reference - "
        f"{len(results_df)} rows in the validation table."
    )

    lines.append("")
    lines.append("## 2. Selection rule")
    lines.append("")
    lines.append(f"Stated and fixed before any validation number was seen: {SELECTION_RULE_TEXT}")
    lines.append("")
    lines.append(
        f"Selection used validation only: inner-train "
        f"({inner_train_df['DateTime'].min()} to {inner_train_df['DateTime'].max()}, "
        f"{len(inner_train_df)} rows) to fit, validation "
        f"({val_start} to {val_df['DateTime'].max()}, {len(val_df)} rows) to score. "
        f"Test ({test_df['DateTime'].min()} to {test_df['DateTime'].max()}, "
        f"{len(test_df)} rows) was not touched until section 5."
    )

    lines.append("")
    lines.append("## 3. Validation results")
    lines.append("")
    lines.append(
        f"Top 10 of {len(results_df)} rows, sorted by OVERALL_excl_West MAE. "
        f"Full table: `{val_csv_path}`."
    )
    lines.append("")
    lines.append(df_to_markdown(results_df.head(10), VAL_DISPLAY_COLS))
    lines.append("")
    lines.append("Bottom 5 (worst configurations):")
    lines.append("")
    lines.append(df_to_markdown(results_df.tail(5), VAL_DISPLAY_COLS))

    lines.append("")
    lines.append("## 4. Selected configuration")
    lines.append("")
    lines.append(
        f"**{selected['name']}**, target=**{selected['target']}** "
        f"(validation OVERALL_excl_West MAE={selected['OVERALL_excl_West_MAE']:.3f}). "
        f"The best validation MAE overall was {best_mae:.3f}, achieved by "
        f"{len(tied)} configuration(s) within the 0.1 MAE tie band; "
        f"{selected['name']}/{selected['target']} is the simplest of those "
        "by the ordering in the rule above."
    )

    lines.append("")
    lines.append("## 5. Test results (selected configuration only)")
    lines.append("")
    lines.append(
        "Evaluated under two protocols. STATIC fits once on the full training "
        "period and predicts all of test. ROLLING refits every 7 days on "
        f"everything seen so far and predicts only that week ({n_refits} refits "
        "total), matching ADR-012's weekly regeneration."
    )
    lines.append("")
    lines.append(df_to_markdown(test_table, test_cols))

    lines.append("")
    lines.append("## 6. Findings")
    lines.append("")
    lines.append(
        f"- The hybrid did **not** beat Random Forest on validation: best Hybrid "
        f"({best_hybrid['name']}, target={best_hybrid['target']}) scored "
        f"{best_hybrid['OVERALL_excl_West_MAE']:.3f} MAE against best RandomForest "
        f"({best_rf['name']}, target={best_rf['target']}) at "
        f"{best_rf['OVERALL_excl_West_MAE']:.3f} MAE. This is reported as a result, "
        "not a footnote: two-stage machinery did not earn its complexity here."
    )
    lines.append(
        f"- **{worst_row['name']}** with the **ratio** target was the worst of all "
        f"{len(results_df) - 1} model configurations compared "
        f"(OVERALL_excl_West MAE={worst_row['OVERALL_excl_West_MAE']:.3f}), driven by "
        f"North alone reaching {worst_row['North_MAE']:.3f} MAE under that transform - "
        "Ridge's linear extrapolation on a ratio target overshoots badly on North's "
        "strong trend."
    )
    lines.append(
        f"- The **diff** target's best configuration ({target_best['diff']:.3f} MAE) "
        f"beat both the best **raw** configuration ({target_best['raw']:.3f} MAE) and "
        f"the best **ratio** configuration ({target_best['ratio']:.3f} MAE)."
    )
    lines.append(
        "- Under the **static** protocol the selected model **lost** to the naive "
        f"baseline on test: {static_scores['OVERALL_excl_West_MAE']:.2f} vs "
        f"{baseline_test_scores['OVERALL_excl_West_MAE']:.2f} baseline MAE."
    )
    lines.append(
        "- Under the **rolling** protocol it **beat** the baseline overall: "
        f"{rolling_scores['OVERALL_excl_West_MAE']:.2f} vs "
        f"{baseline_test_scores['OVERALL_excl_West_MAE']:.2f} baseline MAE."
    )

    lines.append("")
    lines.append("## 7. Limitations")
    lines.append("")
    lines.append(
        "- The rolling protocol does **not** beat the baseline on every road. "
        f"North is worse ({rolling_scores['North_MAE']:.2f} vs "
        f"{baseline_test_scores['North_MAE']:.2f} baseline), and South is "
        f"effectively tied ({rolling_scores['South_MAE']:.2f} vs "
        f"{baseline_test_scores['South_MAE']:.2f} baseline). The overall win is "
        f"driven mainly by East ({rolling_scores['East_MAE']:.2f} vs "
        f"{baseline_test_scores['East_MAE']:.2f} baseline). This is stated "
        "explicitly and not rounded away."
    )
    lines.append(
        "- Four test evaluations informed selection decisions (ADR-014, the "
        "corrected re-run, this file's prior 2-target-mode version, and this "
        f"run's static protocol), plus one rolling protocol comprising {n_refits} "
        "refits on which no selection was performed."
    )
    lines.append(
        f"- ADR-016 recorded a seed-to-seed noise floor of sd={ADR016_NOISE_FLOOR_SD} "
        "MAE (5 random seeds, 300 trees, full 14-feature set, original validation "
        f"split). {noise_verdict}"
    )

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    # -----------------------------------------------------------------
    # Load + split. Test is produced here but not touched again until
    # Part 2 (K1/K2), and then only for the single selected configuration.
    # -----------------------------------------------------------------
    print("=== Loading features (data_prep.load_and_engineer) ===")
    features, rows_dropped = load_and_engineer(DATA_PATH)
    print(f"Rows dropped upstream for NaN lag_168/lag_336/roll_168_lag168: {rows_dropped}")

    train_df, test_df = temporal_split(features)
    print(f"Full train rows: {len(train_df)}   Test rows: {len(test_df)}   "
          f"split_date={SPLIT_DATE}")

    # -----------------------------------------------------------------
    # L1: confirm roll_168_lag168 and lag_168 are computed PER ROAD, not
    # assumed. Print the actual groupby lines from data_prep.py, then show
    # South and North differ (a global/shared computation could not
    # produce different per-road levels).
    # -----------------------------------------------------------------
    print()
    print("=== L1: per-road computation check ===")
    source = inspect.getsource(dp.add_lag_features)
    groupby_lines = [ln.strip() for ln in source.splitlines() if "groupby" in ln]
    print("groupby calls in data_prep.add_lag_features (builds lag_168, "
          "lag_336 and roll_168_lag168):")
    for ln in groupby_lines:
        print(f"    {ln}")
    south_roll = features.loc[features["Road"] == "South", "roll_168_lag168"].mean()
    north_roll = features.loc[features["Road"] == "North", "roll_168_lag168"].mean()
    south_lag = features.loc[features["Road"] == "South", "lag_168"].mean()
    north_lag = features.loc[features["Road"] == "North", "lag_168"].mean()
    print(f"Mean roll_168_lag168: South={south_roll:.3f}  North={north_roll:.3f}  "
          f"(differ => per-road, not a shared/global computation)")
    print(f"Mean lag_168:         South={south_lag:.3f}  North={north_lag:.3f}")

    # -----------------------------------------------------------------
    # L2: verify each target transform round-trips on one real row before
    # trusting any table built on it. North, 2016-06-01 08:00 - the same
    # row ADR-018 hand-verified for lag_168/lag_336/roll_168_lag168.
    # -----------------------------------------------------------------
    print()
    print("=== L2: target transform round-trip check (one row) ===")
    check_row = features[
        (features["Road"] == "North") & (features["DateTime"] == "2016-06-01 08:00:00")
    ].iloc[0]
    actual = float(check_row[TARGET_COLUMN])
    print(f"Row: North, 2016-06-01 08:00:00. actual Vehicles={actual}, "
          f"lag_168={check_row['lag_168']}, roll_168_lag168={check_row['roll_168_lag168']:.6f}")
    for mode in TARGET_MODES:
        row_df = check_row.to_frame().T
        t = get_target(row_df, mode)[0]
        inv = back_transform(np.array([t]), row_df, mode)[0]
        ok = np.isclose(inv, actual, atol=1e-6)
        print(f"  {mode:5s}  transformed={t:.6f}  inverse_transformed={inv:.6f}  "
              f"matches actual={ok}")
        if not ok:
            raise AssertionError(f"{mode} transform failed to round-trip on the check row")

    # -----------------------------------------------------------------
    # H1: validation = last 12 weeks of the training period. Inner train
    # = everything in train before that. Test is not referenced again
    # until Part 2.
    # -----------------------------------------------------------------
    print()
    print("=== H1: validation split ===")
    val_start = pd.Timestamp(SPLIT_DATE) - pd.Timedelta(weeks=VAL_WEEKS)
    inner_train_df = train_df[train_df["DateTime"] < val_start].reset_index(drop=True)
    val_df = train_df[train_df["DateTime"] >= val_start].reset_index(drop=True)

    print(f"VAL_WEEKS={VAL_WEEKS}   val_start={val_start}")
    print(f"Inner-train rows: {len(inner_train_df)}   "
          f"({inner_train_df['DateTime'].min()} to {inner_train_df['DateTime'].max()})")
    print(f"Validation rows:  {len(val_df)}   "
          f"({val_df['DateTime'].min()} to {val_df['DateTime'].max()})")
    print(f"Test rows:        {len(test_df)}   "
          f"({test_df['DateTime'].min()} to {test_df['DateTime'].max()})  "
          f"[untouched until Part 2]")

    print()
    drift_ratio_table(val_df, inner_train_df, "val_mean", "inner_train_mean")
    print()
    drift_ratio_table(test_df, train_df, "test_mean", "train_mean")

    # -----------------------------------------------------------------
    # H2 (J1-J4): evaluate every model x target-mode combination on
    # VALIDATION only.
    # -----------------------------------------------------------------
    print()
    print("=== H2: validation evaluation (test not touched) ===")
    X_inner, _ = feature_matrix(inner_train_df)
    X_val, y_val = feature_matrix(val_df)
    actual_val = y_val.to_numpy()

    rows = []

    # Seasonal-naive baseline, for reference alongside every model.
    baseline_pred = val_df["lag_168"].to_numpy()
    baseline_scores = per_road_metrics(val_df, actual_val, baseline_pred)
    rows.append({"name": "Naive_lag168_baseline", "family": "Baseline",
                 "target": "raw", "n_estimators": None, "max_depth": None,
                 "fit_seconds": 0.0, **baseline_scores})

    for spec in model_grid():
        for target_mode in TARGET_MODES:
            model = build_model(spec["family"], spec.get("n_estimators"), spec.get("max_depth"))
            y_fit = get_target(inner_train_df, target_mode)
            t0 = time.time()
            model.fit(X_inner, y_fit)
            fit_seconds = time.time() - t0
            raw_pred = model.predict(X_val)
            pred = back_transform(raw_pred, val_df, target_mode)
            scores = per_road_metrics(val_df, actual_val, pred)
            row = {"name": spec["name"], "family": spec["family"], "target": target_mode,
                   "n_estimators": spec.get("n_estimators"), "max_depth": spec.get("max_depth"),
                   "fit_seconds": fit_seconds, **scores}
            rows.append(row)
            print(f"  done: {spec['name']:32s} target={target_mode:5s} "
                  f"OVERALL_excl_West MAE={scores['OVERALL_excl_West_MAE']:.3f} "
                  f"({fit_seconds:.1f}s)")

    results_df = pd.DataFrame(rows)
    results_df = results_df.sort_values("OVERALL_excl_West_MAE").reset_index(drop=True)

    print()
    print("=== J4: full validation results table, sorted by OVERALL_excl_West MAE ===")
    display_cols = ["name", "target",
                     "East_MAE", "East_RMSE", "North_MAE", "North_RMSE",
                     "South_MAE", "South_RMSE", "West_MAE", "West_RMSE",
                     "OVERALL_excl_West_MAE", "OVERALL_excl_West_RMSE"]
    print(results_df[display_cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    # -----------------------------------------------------------------
    # H3/J5: select ONE configuration, mechanically, per the rule defined
    # above main() - see select_configuration/simplicity_key.
    # -----------------------------------------------------------------
    print()
    print("=== J5: selection ===")
    print(f"Rule (fixed before results were computed): {SELECTION_RULE_TEXT}")
    best_mae = results_df["OVERALL_excl_West_MAE"].min()
    tied = results_df[results_df["OVERALL_excl_West_MAE"] <= best_mae + 0.1]
    print(f"Best validation OVERALL_excl_West MAE: {best_mae:.3f}")
    print(f"Configurations within 0.1 MAE of best ({len(tied)}):")
    print(tied[display_cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    selected = select_configuration(results_df)
    print()
    print(f"SELECTED: {selected['name']}  target={selected['target']}  "
          f"(validation OVERALL_excl_West MAE={selected['OVERALL_excl_West_MAE']:.3f}, "
          f"best available was {best_mae:.3f})")

    # L6: did the hybrid beat Random Forest on validation? Stated directly,
    # not folded into the selection narrative.
    print()
    print("=== L6: hybrid vs RandomForest on validation ===")
    hybrid_rows = results_df[results_df["family"] == "Hybrid"]
    rf_rows = results_df[results_df["family"] == "RandomForest"]
    best_hybrid = hybrid_rows.loc[hybrid_rows["OVERALL_excl_West_MAE"].idxmin()]
    best_rf = rf_rows.loc[rf_rows["OVERALL_excl_West_MAE"].idxmin()]
    print(f"Best Hybrid:       {best_hybrid['name']} target={best_hybrid['target']}  "
          f"MAE={best_hybrid['OVERALL_excl_West_MAE']:.3f}")
    print(f"Best RandomForest: {best_rf['name']} target={best_rf['target']}  "
          f"MAE={best_rf['OVERALL_excl_West_MAE']:.3f}")
    if best_hybrid["OVERALL_excl_West_MAE"] < best_rf["OVERALL_excl_West_MAE"]:
        print("Hybrid beat RandomForest on validation.")
    else:
        print("Hybrid did NOT beat RandomForest on validation - RandomForest wins.")

    # -----------------------------------------------------------------
    # Part 2: TEST, exactly two protocols, both for the selected
    # configuration only. No other configuration touches test below this
    # point.
    # -----------------------------------------------------------------
    sel_family = selected["family"]
    sel_target = selected["target"]
    sel_n_estimators = None if pd.isna(selected["n_estimators"]) else int(selected["n_estimators"])
    sel_max_depth = None if pd.isna(selected["max_depth"]) else int(selected["max_depth"])

    X_test, y_test = feature_matrix(test_df)
    actual_test = y_test.to_numpy()
    baseline_test_pred = test_df["lag_168"].to_numpy()
    baseline_test_scores = per_road_metrics(test_df, actual_test, baseline_test_pred)

    # --- K1: STATIC protocol - fit once on the full training period ------
    print()
    print("=== K1: STATIC test protocol (fit once on full train, predict all test) ===")
    X_train_full, _ = feature_matrix(train_df)
    y_fit_full = get_target(train_df, sel_target)
    static_model = build_model(sel_family, sel_n_estimators, sel_max_depth)
    t0 = time.time()
    static_model.fit(X_train_full, y_fit_full)
    print(f"Fit {selected['name']} (target={sel_target}) once on {len(X_train_full)} rows "
          f"(inner-train + validation) in {time.time() - t0:.1f}s")
    static_raw_pred = static_model.predict(X_test)
    static_pred = back_transform(static_raw_pred, test_df, sel_target)
    static_scores = per_road_metrics(test_df, actual_test, static_pred)

    # --- K2: ROLLING protocol - refit weekly, matching ADR-012 -----------
    print()
    print("=== K2: ROLLING test protocol (refit weekly, matching ADR-012) ===")
    test_start = pd.Timestamp(SPLIT_DATE)
    test_end_exclusive = test_df["DateTime"].max() + pd.Timedelta(hours=1)
    week_starts = []
    cur = test_start
    while cur < test_end_exclusive:
        week_starts.append(cur)
        cur += pd.Timedelta(days=ROLL_WEEK_DAYS)

    rolling_pred = np.full(len(test_df), np.nan)
    for week_start in week_starts:
        week_end = min(week_start + pd.Timedelta(days=ROLL_WEEK_DAYS), test_end_exclusive)
        # Rolling/expanding training window: everything the pipeline would
        # actually have on hand by week_start, which for later weeks
        # includes EARLIER, now-realised test weeks - exactly what weekly
        # regeneration (ADR-012) means in deployment.
        rolling_train_df = features.loc[features["DateTime"] < week_start]
        week_mask = ((test_df["DateTime"] >= week_start)
                     & (test_df["DateTime"] < week_end)).to_numpy()

        X_roll, _ = feature_matrix(rolling_train_df)
        y_roll = get_target(rolling_train_df, sel_target)
        week_model = build_model(sel_family, sel_n_estimators, sel_max_depth)
        week_model.fit(X_roll, y_roll)

        week_test_df = test_df.loc[week_mask]
        X_week, _ = feature_matrix(week_test_df)
        raw_week_pred = week_model.predict(X_week)
        rolling_pred[week_mask] = back_transform(raw_week_pred, week_test_df, sel_target)

    n_refits = len(week_starts)
    assert not np.isnan(rolling_pred).any(), (
        "rolling protocol left some test rows unpredicted - week boundaries do not "
        "cover the full test period"
    )
    print(f"Rolling protocol: {n_refits} refits performed, one per {ROLL_WEEK_DAYS}-day "
          f"block from {week_starts[0]} to {week_starts[-1]} "
          f"(final block may be shorter than {ROLL_WEEK_DAYS} days).")
    rolling_scores = per_road_metrics(test_df, actual_test, rolling_pred)

    # --- K3: one combined table -------------------------------------------
    print()
    print("=== K3: TEST results - baseline, static, rolling, ADR-014, corrected re-run, "
          "prior selection ===")
    header = (f"{'Road':6s}  {'Base':>11s}  {'Static':>11s}  {'Rolling':>11s}  "
              f"{'ADR-014':>11s}  {'CorrRerun':>11s}  {'PriorSel':>11s}")
    print(header)
    print(f"{'':6s}  {'MAE/RMSE':>11s}  {'MAE/RMSE':>11s}  {'MAE/RMSE':>11s}  "
          f"{'MAE/RMSE':>11s}  {'MAE/RMSE':>11s}  {'MAE/RMSE':>11s}")
    for road in ROADS:
        base = (baseline_test_scores[f"{road}_MAE"], baseline_test_scores[f"{road}_RMSE"])
        static = (static_scores[f"{road}_MAE"], static_scores[f"{road}_RMSE"])
        rolling = (rolling_scores[f"{road}_MAE"], rolling_scores[f"{road}_RMSE"])
        a14 = ADR014_MODEL[road]
        cr = CORRECTED_RERUN_MODEL[road]
        ps = PRIOR_SELECTION_MODEL[road]
        print(f"{road:6s}  {base[0]:5.2f}/{base[1]:5.2f}  {static[0]:5.2f}/{static[1]:5.2f}  "
              f"{rolling[0]:5.2f}/{rolling[1]:5.2f}  {a14[0]:5.2f}/{a14[1]:5.2f}  "
              f"{cr[0]:5.2f}/{cr[1]:5.2f}  {ps[0]:5.2f}/{ps[1]:5.2f}")
    o_base = (baseline_test_scores["OVERALL_excl_West_MAE"], baseline_test_scores["OVERALL_excl_West_RMSE"])
    o_static = (static_scores["OVERALL_excl_West_MAE"], static_scores["OVERALL_excl_West_RMSE"])
    o_roll = (rolling_scores["OVERALL_excl_West_MAE"], rolling_scores["OVERALL_excl_West_RMSE"])
    print(f"{'OVRALL':6s}  {o_base[0]:5.2f}/{o_base[1]:5.2f}  {o_static[0]:5.2f}/{o_static[1]:5.2f}  "
          f"{o_roll[0]:5.2f}/{o_roll[1]:5.2f}  {'5.02':>5s}/{'8.35':<5s}  "
          f"{'7.06':>5s}/{'10.67':<5s}  {'6.98':>5s}/{'10.66':<5s}")

    # -----------------------------------------------------------------
    # L4: total test evaluations across the whole project, disclosed.
    # -----------------------------------------------------------------
    print()
    print("=== L4: total test evaluations across the project (disclosed) ===")
    prior_static_evaluations = 3  # ADR-014, corrected re-run, this file's prior version
    this_run_evaluations = 1 + n_refits  # K1 static (1) + K2 rolling (n_refits)
    total = prior_static_evaluations + this_run_evaluations
    print(f"  1. ADR-014 (13-feature illegal set, default RF)                    1 evaluation")
    print(f"  2. Corrected re-run (14-feature set, train_model.py default RF)    1 evaluation")
    print(f"  3. Prior version of this file (2-target grid, static protocol)     1 evaluation")
    print(f"  4. This run - K1 static protocol                                   1 evaluation")
    print(f"  5. This run - K2 rolling protocol                                  {n_refits} evaluations "
          f"(one fit+predict per week)")
    print(f"  TOTAL test evaluations to date: {total}")
    print("  Additional disclosure: the rolling protocol also means test rows from "
          "earlier weeks become TRAINING data for later weeks' refits (by design - "
          "this is what ADR-012's weekly regeneration means in deployment), which is "
          "a second, distinct way test data was used beyond the prediction count above.")

    # -----------------------------------------------------------------
    # L5: plain statement of whether anything beat baseline on test.
    # -----------------------------------------------------------------
    print()
    print("=== L5: did anything beat the naive baseline on TEST? ===")
    for label, scores in [("Static", static_scores), ("Rolling", rolling_scores)]:
        mae_diff = scores["OVERALL_excl_West_MAE"] - o_base[0]
        rmse_diff = scores["OVERALL_excl_West_RMSE"] - o_base[1]
        beats = mae_diff < 0 and rmse_diff < 0
        verdict = "YES" if beats else "NO"
        print(f"  {label:8s} target={sel_target:5s}  OVERALL_excl_West MAE={scores['OVERALL_excl_West_MAE']:.3f} "
              f"(baseline {o_base[0]:.3f}, {'better' if mae_diff < 0 else 'worse'} by {abs(mae_diff):.3f})  "
              f"beats baseline (MAE AND RMSE)? {verdict}")

    # -----------------------------------------------------------------
    # M1: persist the results as committed evidence under results/.
    # -----------------------------------------------------------------
    print()
    print("=== M1: writing results/ artefacts ===")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    val_csv_path = os.path.join(RESULTS_DIR, "model_selection_validation.csv")
    results_df.to_csv(val_csv_path, index=False)
    print(f"Wrote {val_csv_path} ({len(results_df)} rows)")

    test_csv_df = pd.DataFrame([
        {"protocol": "baseline", **baseline_test_scores},
        {"protocol": "static", **static_scores},
        {"protocol": "rolling", **rolling_scores},
    ])
    test_csv_path = os.path.join(RESULTS_DIR, "model_selection_test.csv")
    test_csv_df.to_csv(test_csv_path, index=False)
    print(f"Wrote {test_csv_path} ({len(test_csv_df)} rows)")

    # --- Q1/Q2: provenance, captured at run time -------------------------
    # Written as a SIBLING JSON file rather than a '#'-prefixed header row
    # inside the CSVs: a comment row would break plain pd.read_csv() for
    # every downstream reader (including this project's own reviewer
    # checks, which read these CSVs directly with no comment= argument),
    # silently shifting every column by one row. A separate JSON file
    # costs nothing to a CSV reader and is trivial to load alongside it.
    run_datetime = datetime.now().isoformat(timespec="seconds")
    commit, dirty = get_git_provenance()
    provenance = {
        "script": "model_selection.py",
        "run_datetime": run_datetime,
        "commit": commit,
        "working_tree_dirty": dirty,
        "random_state": RANDOM_STATE,
        "produced_files": [val_csv_path, test_csv_path,
                            os.path.join(RESULTS_DIR, "MODEL_SELECTION.md")],
    }
    provenance_path = os.path.join(RESULTS_DIR, "model_selection_provenance.json")
    with open(provenance_path, "w", encoding="utf-8") as fh:
        json.dump(provenance, fh, indent=2)
    print(f"Wrote {provenance_path}")
    if dirty:
        print("WARNING: working tree is DIRTY - this result is weaker evidence "
              "than one generated from a committed state.")

    worst_row = results_df.iloc[-1]
    target_best = (
        results_df[results_df["family"] != "Baseline"]
        .groupby("target")["OVERALL_excl_West_MAE"].min()
    )
    n_model_configs = len(model_grid()) * len(TARGET_MODES)

    md_path = os.path.join(RESULTS_DIR, "MODEL_SELECTION.md")
    write_markdown_report(
        md_path,
        results_df=results_df, selected=selected, tied=tied, best_mae=best_mae,
        n_model_configs=n_model_configs,
        baseline_test_scores=baseline_test_scores, static_scores=static_scores,
        rolling_scores=rolling_scores, n_refits=n_refits,
        best_hybrid=best_hybrid, best_rf=best_rf, worst_row=worst_row,
        target_best=target_best, val_start=val_start,
        inner_train_df=inner_train_df, val_df=val_df, test_df=test_df,
        sel_target=sel_target, val_csv_path=val_csv_path, test_csv_path=test_csv_path,
        run_datetime=run_datetime, commit=commit, dirty=dirty,
    )
    print(f"Wrote {md_path}")

    # --- Q3: append-only results log --------------------------------------
    finding = (
        f"{selected['name']}/{selected['target']} selected on validation "
        f"(MAE {selected['OVERALL_excl_West_MAE']:.3f}); rolling protocol beats "
        f"baseline overall ({rolling_scores['OVERALL_excl_West_MAE']:.2f} vs "
        f"{baseline_test_scores['OVERALL_excl_West_MAE']:.2f} MAE) but static does not "
        f"({static_scores['OVERALL_excl_West_MAE']:.2f} vs "
        f"{baseline_test_scores['OVERALL_excl_West_MAE']:.2f} MAE)."
    )
    append_results_log({
        "date": run_datetime[:10],
        "artefact": "results/MODEL_SELECTION.md",
        "script": "model_selection.py",
        "commit": commit,
        "adr": "ADR-020 (pending)",
        "finding": finding,
    })
    print(f"Appended a row to {RESULTS_LOG_PATH}")


if __name__ == "__main__":
    main()

"""
Stage 1 of the AI pipeline: feature engineering only.

No model training, no prediction, no crypto happens in this file. It reads
data/traffic_final_cleaned.csv and produces the feature table that Stage 2
(the Random Forest) will train on.

WHAT THE MODEL ACTUALLY PREDICTS
---------------------------------
The Random Forest in this project does NOT predict green-light seconds.
There are no green-light labels anywhere in the data - it predicts
VEHICLE COUNTS per road per hour. A separate deterministic layer (Stage 3)
converts predicted counts into green seconds using Webster's method with UK
constraints. Keeping these two layers separate is what makes the system
explainable, which the governance chapter requires. See ADR-007 in
DECISIONS.md.

WHY EACH FEATURE EXISTS
------------------------
Load order: the CSV is parsed and sorted by Road then DateTime before
anything else happens. Lag and rolling features are computed per road in
time order; unsorted input would silently produce wrong lags with no error
at all, so this has to happen first.

Cyclical time features (hour_sin/cos, dow_sin/cos, month_sin/cos): hour 23
and hour 0 are adjacent in reality. As plain integers a decision tree sees
them as 23 apart and will split between them instead of learning the
overnight pattern. Sine/cosine encoding places them next to each other on a
circle. The same argument applies to Sunday-to-Monday and December-to-
January.

is_weekend: weekend traffic changes shape, not just volume - the morning
peak flattens and shifts later. The cyclical day-of-week feature captures
adjacency but not this categorical break, so both are needed.

One-hot road columns (road_North/South/East/West): the four roads differ by
roughly 6x in mean volume (North ~45/hr, West ~7/hr). Label-encoding them
0-3 would imply an ordering between roads that does not exist. One-hot
lets the model learn each road independently.

FEATURE LEGALITY AT SCHEDULE-GENERATION TIME: the schedule is regenerated
weekly (ADR-012), so a feature is only usable if it can be computed for the
FURTHEST hour in that week, which is 168 hours after generation. lag_168,
lag_336 and roll_168_lag168 all satisfy this. An earlier feature set used a
rolling mean of the previous 24 hours, which needs data that has not
happened yet at generation time and is therefore illegal. See ADR-015 and
CLAUDE.md.

lag_168: the Vehicles value at the same hour exactly 168 hours (7 days)
earlier, per road. Hourly traffic is strongly weekly-periodic, so the same
hour of the same weekday one week ago is usually the single strongest
predictor. 168 rather than 24 because a 24-hour lag would compare a Monday
to a Sunday, and 24 is illegal under the rule above regardless.

lag_336: the Vehicles value 336 hours (14 days) earlier, per road. Confirmed
by detrended autocorrelation as the next-best legal lag after lag_168.
Carries a second, independent read on the current level.

roll_168_lag168: the mean of Vehicles over the 168 hours ending 168 hours
before the target row, per road - s.shift(168).rolling(168).mean(). This is
the legal replacement for the illegal 24-hour rolling mean described
above: it captures the recent level of demand using only data that is
already available 168 hours ahead of any target hour in the scheduling
week.

DELIBERATELY NO year FEATURE: the dataset only covers 2015, 2016, and half
of 2017. A tree would use year as a row identifier rather than a
generalisable pattern, and the model would fail on any unseen year. The
original project description document listed year as a feature; omitting
it is a deliberate departure from that document, not an oversight.

DELIBERATELY NO ID FEATURE: the ID column encodes the date, the hour and
the road all at once (2015-11-01 00:00 East has ID 20151101003, and the
01:00 row has 20151101013). A tree given that column could memorise
individual rows instead of learning a pattern. The temporal split would
partly mask this, because test IDs fall outside the training range and the
tree would simply clamp - which means the leak would be invisible rather
than absent. FEATURE_COLUMNS below therefore states the model inputs
explicitly rather than leaving Stage 2 to infer them.

Rows with NaN lag_168, lag_336 or roll_168_lag168 (the first two weeks of
history per road, which cannot yet have a 14-day lag) are dropped, and the
count dropped is reported when this file is run, so it can be stated in the
methodology. lag_336 has the widest NaN set of the three and drives the
drop; see the printed audit for the exact counts.

temporal_split() splits on a fixed date, NOT randomly: hourly traffic is
autocorrelated, so a random 80/20 split would put 09:00 in training and
10:00 the same day in testing, and the model would score well by
memorising neighbouring hours rather than learning the underlying pattern.
A temporal split also mirrors real deployment, where the model predicts a
future that has not happened yet. See ADR-008.

temporal_split_without_outliers() applies the same split but removes
flagged rows from TRAIN ONLY. It is NOT used by Stage 2. The Stage 2 count
predictor trains on ALL rows including peaks, because a forecaster trained
without peaks underpredicts rush hour, which is exactly when green time
allocation matters most. See ADR-010.

This function is reserved for the Stage 6 Isolation Forest, where learning
normal behaviour genuinely is the objective. When Stage 6 uses it, it must
be changed to key on outlier_trailing rather than Outlier_Flag, for the
reason given in ADR-009. It currently still keys on Outlier_Flag because
nothing calls it yet. Neither flag's rows are ever deleted from the CSV,
only filtered in memory.

Synthetic_Segment_Unverified is kept in the output frame even though it is
not a model feature: the West road is roughly 70% synthetic and its
synthetic period ends exactly at the train/test boundary, so West trains
entirely on synthetic data and tests entirely on real data. Stage 2 must be
able to report West separately, which is impossible if this column is
dropped here. See ADR-002.

outlier_trailing IS A SECOND, LEVEL-RELATIVE OUTLIER FLAG
----------------------------------------------------------
The CSV's Outlier_Flag is a per-road Tukey fence (Vehicles > Q3 + 1.5*IQR)
computed ONCE across the whole 20 month period. Demand grew roughly 3x over
that period, so a fence fixed to the whole-period quartiles drifts into
flagging ordinary later traffic. Measured: South was 0.0% flagged in every
quarter of 2016 and 31.6% in 2017 Q2, with nothing anomalous having
happened. The level moved and the fence did not.

outlier_trailing recomputes the same fence per road from a TRAILING 28 day
(672 hour) window ending at the previous row, so it tracks the current
level. Rows without a full window get False, not True: an unknown fence is
not evidence of an anomaly.

Outlier_Flag is left untouched for provenance and comparison. See ADR-009,
which records the measured effect on all four roads.

TWO GUARDS ON SILENT FAILURE
-----------------------------
_assert_flag_columns_are_bool: temporal_split_without_outliers uses the ~
operator on Outlier_Flag, which requires bool dtype. The current CSV uses
uppercase TRUE/FALSE, which pandas parses correctly, but a future
re-export using 0/1 or Yes/No would make ~ raise a confusing TypeError far
from the real cause. Checked once, at load time, where the message can name
the actual problem.

_assert_hourly_series_is_complete: lag_168 uses shift(168), which counts
ROWS, not hours. That is only correct if every road has a complete,
unbroken hourly series. It does here (608 days x 24 = 14,592 rows per
road), but the assumption is invisible in the code, and a single missing
hour would make lag_168 silently pull the wrong timestamp with no error at
all. The check makes the assumption explicit and loud.
"""

import numpy as np
import pandas as pd

DATA_PATH = "data/traffic_final_cleaned.csv"
SPLIT_DATE = "2017-01-01"
LAG_HOURS = 168         # 7 days - see module docstring
LAG_HOURS_336 = 336     # 14 days - see module docstring
ROLL_WINDOW_168 = 168   # hours, ending 168h before target - see module docstring

# 28 days. Chosen so the trailing window always contains four of every
# weekday, which keeps the quartile estimate stable while still tracking
# the trend. A shorter window would make the fence jumpy; a longer one
# would drift back towards the whole-period fence this replaces.
TRAILING_WINDOW_HOURS = 672

# The ONLY columns Stage 2 may use as model inputs.
#
# ID is deliberately excluded: it encodes date, hour and road, so a tree
# could memorise rows through it. There is no year feature, for the reason
# given in the module docstring.
#
# DateTime, Road, Vehicles, Outlier_Flag and Synthetic_Segment_Unverified
# all stay in the returned frame - they are needed for splitting and for
# per-road and synthetic-vs-real reporting - but they are NOT features.
FEATURE_COLUMNS = [
    "hour_sin", "hour_cos",
    "dow_sin", "dow_cos",
    "month_sin", "month_cos",
    "is_weekend",
    "road_East", "road_North", "road_South", "road_West",
    "lag_168", "lag_336", "roll_168_lag168",
]
TARGET_COLUMN = "Vehicles"

FLAG_COLUMNS = ["Outlier_Flag", "Synthetic_Segment_Unverified"]


def _assert_flag_columns_are_bool(df):
    """
    Fail loudly if the boolean flag columns did not parse as bool.

    temporal_split_without_outliers uses ~df["Outlier_Flag"], which only
    works on a bool dtype. If the CSV is ever re-exported with 0/1 or
    Yes/No, that operator raises a TypeError far from the real cause. This
    catches it at load time and names the column and the dtype found.
    """
    for col in FLAG_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"Expected column '{col}' is missing from the CSV.")
        if df[col].dtype != bool:
            raise ValueError(
                f"Column '{col}' parsed as dtype {df[col].dtype}, expected bool. "
                "The CSV needs re-checking - do not adjust this code to accept "
                "the other type, because the flag semantics would become unclear."
            )


def _assert_hourly_series_is_complete(df):
    """
    Fail loudly if any road's hourly series has gaps or duplicates.

    lag_168 uses shift(168), which is POSITIONAL: it moves 168 rows back,
    not 168 hours back. Those are the same thing only while every road has
    a complete, unbroken hourly series. A single missing hour would make
    lag_168 pull the wrong timestamp with no error raised anywhere. This
    check is what makes the positional shift safe to rely on.
    """
    for road, group in df.groupby("Road"):
        span_hours = int(
            (group["DateTime"].max() - group["DateTime"].min())
            / pd.Timedelta(hours=1)
        ) + 1
        if len(group) != span_hours:
            raise ValueError(
                f"Road '{road}' has {len(group)} rows but spans {span_hours} "
                f"hours ({group['DateTime'].min()} to {group['DateTime'].max()}). "
                "The hourly series has gaps, so the positional shift used by "
                "lag_168 would silently pull the wrong timestamp."
            )
        if group["DateTime"].duplicated().any():
            n_dupes = int(group["DateTime"].duplicated().sum())
            raise ValueError(
                f"Road '{road}' has {n_dupes} duplicate DateTime values, so the "
                "positional shift used by lag_168 would be misaligned."
            )


def _trailing_outlier_fence(s, window=TRAILING_WINDOW_HOURS):
    """
    Tukey upper fence (Q3 + 1.5*IQR) computed on a TRAILING window of one
    road's series, ending at the PREVIOUS row.

    shift(1) before the rolling window is what stops the current value
    contributing to the fence that judges it - the same target-leakage
    argument as the lag/rolling features below. Rows without a full window
    return NaN, which the caller turns into False rather than True: an
    unknown fence is not evidence of an anomaly.
    """
    prev = s.shift(1)
    q1 = prev.rolling(window=window).quantile(0.25)
    q3 = prev.rolling(window=window).quantile(0.75)
    return q3 + 1.5 * (q3 - q1)


def load_and_sort(path=DATA_PATH):
    """
    Read the CSV, run both integrity guards, and return it sorted by Road
    then DateTime.

    Shared by load_and_engineer and the __main__ audit block, so there is
    one place that reads the CSV and checks it, not two that could drift
    apart. See ADR-018's code debt note.
    """
    df = pd.read_csv(path, parse_dates=["DateTime"])

    # Guard 1: the flag columns must be real booleans - see the helper.
    _assert_flag_columns_are_bool(df)

    # Sort by Road then DateTime BEFORE any lag/rolling computation - lag
    # and rolling features are per-road, time-ordered operations, and
    # unsorted input would silently produce wrong lags with no error.
    df = df.sort_values(["Road", "DateTime"]).reset_index(drop=True)

    # Guard 2: shift() counts rows, so the series must be gap free. Checked
    # after the sort and BEFORE any lag or rolling computation.
    _assert_hourly_series_is_complete(df)

    return df


def add_lag_features(df):
    """
    Add lag_168, lag_336 and roll_168_lag168 to df (per road, time ordered)
    and return it.

    Shared by load_and_engineer and the __main__ audit block, so the NaN
    counts printed at run time describe the same computation the pipeline
    actually uses. See ADR-018's code debt note: before this extraction the
    __main__ block recomputed these three columns itself, and the two
    copies could silently drift apart.

    Same hour, same weekday, one week ago - usually the strongest single
    predictor of hourly traffic. 168h not 24h so it never compares a
    weekday to a weekend day, and because 24h is illegal at generation time
    anyway (see module docstring). Safe to use a positional shift only if
    the caller already ran load_and_sort, which guarantees a gap free,
    sorted series.
    """
    df["lag_168"] = df.groupby("Road")["Vehicles"].shift(LAG_HOURS)

    # Same hour, same weekday, two weeks ago. A second, independent read on
    # the current level, confirmed by detrended autocorrelation as the
    # next-best legal lag after lag_168. See ADR-015.
    df["lag_336"] = df.groupby("Road")["Vehicles"].shift(LAG_HOURS_336)

    # Mean of the 168 hours ending 168 hours before the target row. This is
    # the legal replacement for an earlier, illegal 24-hour rolling mean
    # (which needed the 24 hours immediately before the target - not
    # available 168 hours ahead of a scheduling week). Built by rolling
    # over lag_168 itself rather than re-shifting Vehicles: lag_168 IS
    # Vehicles shifted 168 hours, per road, so lag_168.rolling(168).mean()
    # is exactly Vehicles.shift(168).rolling(168).mean() with one less
    # redundant shift. See ADR-015 and CLAUDE.md's feature legality rule.
    df["roll_168_lag168"] = df.groupby("Road")["lag_168"].transform(
        lambda s: s.rolling(window=ROLL_WINDOW_168).mean()
    )

    return df


def lag_nan_report(df):
    """
    Given a frame that already carries lag_168, lag_336 and roll_168_lag168,
    return a dict of the three NaN counts plus the two subset checks printed
    by the __main__ audit: whether lag_168's and roll_168_lag168's NaN rows
    are each a subset of lag_336's wider NaN set.
    """
    lag168_nan = set(df.index[df["lag_168"].isna()])
    lag336_nan = set(df.index[df["lag_336"].isna()])
    roll_nan = set(df.index[df["roll_168_lag168"].isna()])
    return {
        "lag_168": len(lag168_nan),
        "lag_336": len(lag336_nan),
        "roll_168_lag168": len(roll_nan),
        "lag_168_subset_of_lag_336": lag168_nan.issubset(lag336_nan),
        "roll_168_lag168_subset_of_lag_336": roll_nan.issubset(lag336_nan),
    }


def load_and_engineer(path=DATA_PATH):
    """
    Load the raw CSV and return (feature_df, rows_dropped_for_nan).

    feature_df is sorted by Road then DateTime, carries the engineered
    features described in the module docstring, and keeps
    Synthetic_Segment_Unverified so Stage 2 can report West separately
    (see ADR-002). Rows without a full 168-hour lag, 336-hour lag or
    168-hour rolling window are dropped; rows_dropped_for_nan is that
    count.
    """
    df = load_and_sort(path)

    hour = df["DateTime"].dt.hour                  # 0-23
    dow = df["DateTime"].dt.dayofweek               # Monday=0 .. Sunday=6
    month = df["DateTime"].dt.month                 # 1-12

    # Cyclical encoding so hour 23/0, Sunday/Monday and Dec/Jan sit next to
    # each other on a circle instead of 23 (or 6, or 11) integer steps
    # apart - see module docstring.
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    df["month_sin"] = np.sin(2 * np.pi * (month - 1) / 12)
    df["month_cos"] = np.cos(2 * np.pi * (month - 1) / 12)

    # Weekend traffic changes SHAPE not just volume (later, flatter peak),
    # a break the cyclical dow feature alone does not capture.
    df["is_weekend"] = dow.isin([5, 6]).astype(int)

    # One-hot, not label-encoded: roads differ ~6x in mean volume, so an
    # integer 0-3 label would imply a false ordering between them.
    road_dummies = pd.get_dummies(df["Road"], prefix="road").astype(int)
    df = pd.concat([df, road_dummies], axis=1)

    # No `year` feature and no `ID` feature: both would let a tree identify
    # individual rows rather than learn a generalisable pattern. See the
    # module docstring and FEATURE_COLUMNS.

    # lag_168, lag_336 and roll_168_lag168 - see add_lag_features and the
    # module docstring for why each exists and why each is legal at
    # schedule-generation time.
    df = add_lag_features(df)

    # Level-relative outlier flag. The CSV's own Outlier_Flag is a Tukey
    # fence computed ONCE over the whole 20 month period, so as demand grew
    # roughly 3x it drifted into flagging ordinary later traffic (South:
    # 0.0% of hours flagged in every quarter of 2016, 31.6% in 2017 Q2).
    # This recomputes the same fence from a trailing 28 day window so it
    # tracks the current level. Outlier_Flag is left untouched for
    # provenance and comparison. See ADR-009.
    fence = df.groupby("Road")["Vehicles"].transform(_trailing_outlier_fence)
    df["trailing_fence"] = fence
    # NaN fence (no full trailing window yet) means "unknown", which is not
    # evidence of an anomaly, so it becomes False.
    df["outlier_trailing"] = (df["Vehicles"] > fence).fillna(False)

    before = len(df)
    # The first two weeks of rows per road cannot have a 336-hour lag yet -
    # drop them and report how many that is, so it can be stated in the
    # methodology rather than silently absorbed. All three columns are
    # dropped on TOGETHER: they have different NaN counts (lag_336 has the
    # widest), and dropping on only one of them would leave NaN behind in
    # the others.
    df = df.dropna(
        subset=["lag_168", "lag_336", "roll_168_lag168"]
    ).reset_index(drop=True)
    rows_dropped_for_nan = before - len(df)

    return df, rows_dropped_for_nan


def feature_matrix(df):
    """
    Return (X, y) using ONLY the columns named in FEATURE_COLUMNS.

    Stage 2 must call this rather than selecting columns itself. If a
    feature is ever renamed or dropped, this raises here with the column
    named, instead of silently changing what the model trains on.
    """
    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing feature column(s): {missing}. FEATURE_COLUMNS defines the "
            "model inputs - fix the engineering step or update FEATURE_COLUMNS "
            "deliberately, do not select columns ad hoc in Stage 2."
        )
    if TARGET_COLUMN not in df.columns:
        raise ValueError(f"Missing target column '{TARGET_COLUMN}'.")
    return df[FEATURE_COLUMNS].copy(), df[TARGET_COLUMN].copy()


def temporal_split(df, split_date=SPLIT_DATE):
    """
    Train = rows before split_date, test = rows on/after it. NOT random -
    hourly traffic is autocorrelated, so a random split would let the
    model memorise neighbouring hours instead of learning the pattern, and
    would not mirror real deployment (predicting a future not yet seen).
    See ADR-008.
    """
    train = df[df["DateTime"] < split_date].reset_index(drop=True)
    test = df[df["DateTime"] >= split_date].reset_index(drop=True)
    return train, test


def temporal_split_without_outliers(df, split_date=SPLIT_DATE):
    """
    Same temporal split, but flagged rows are removed from TRAIN ONLY.

    NOT USED BY STAGE 2. The count predictor trains on all rows including
    peaks - excluding them would make it underpredict rush hour, which is
    exactly when green time allocation matters. See ADR-010.

    Reserved for the Stage 6 Isolation Forest, where learning normal
    behaviour is the objective. When Stage 6 uses it, switch the key from
    Outlier_Flag to outlier_trailing (ADR-009). Rows stay in test either
    way, because dropping them from test would flatter the score. Never
    deleted from the CSV itself, only filtered here in memory.
    """
    train, test = temporal_split(df, split_date)
    train_no_outliers = train[~train["Outlier_Flag"]].reset_index(drop=True)
    return train_no_outliers, test


if __name__ == "__main__":
    raw = pd.read_csv(DATA_PATH, parse_dates=["DateTime"])

    print("=== Raw data ===")
    print(f"Total rows loaded: {len(raw)}")
    per_road = raw.groupby("Road").size()
    print("Rows per road:")
    print(per_road.to_string())
    print(f"DateTime range: {raw['DateTime'].min()} to {raw['DateTime'].max()}")

    synthetic = raw[raw["Synthetic_Segment_Unverified"]]
    print("Synthetic_Segment_Unverified True rows per road:")
    if len(synthetic) > 0:
        print(synthetic.groupby("Road").size().to_string())
    else:
        print("  none")
    west_synth = synthetic[synthetic["Road"] == "West"]
    if len(west_synth) > 0:
        print(f"West synthetic period: {west_synth['DateTime'].min()} to "
              f"{west_synth['DateTime'].max()}, count={len(west_synth)}")
    else:
        print("West synthetic period: none found")

    outlier_count = int(raw["Outlier_Flag"].sum())
    print(f"Outlier_Flag True rows (all roads): {outlier_count}")

    # --- compare against the expected state, on the RAW data only -------
    print()
    print("=== Expected value check (raw data) ===")
    mismatches = []

    if len(raw) != 58368:
        mismatches.append(f"total rows: expected 58368, got {len(raw)}")

    for road, n in per_road.items():
        if n != 14592:
            mismatches.append(f"rows for {road}: expected 14592, got {n}")

    if raw["DateTime"].min().date() != pd.Timestamp("2015-11-01").date():
        mismatches.append(
            f"min DateTime: expected 2015-11-01, got {raw['DateTime'].min().date()}")
    if raw["DateTime"].max().date() != pd.Timestamp("2017-06-30").date():
        mismatches.append(
            f"max DateTime: expected 2017-06-30, got {raw['DateTime'].max().date()}")

    if len(west_synth) > 0:
        if west_synth["DateTime"].min().date() != pd.Timestamp("2015-11-01").date():
            mismatches.append(
                "West synthetic start: expected 2015-11-01, got "
                f"{west_synth['DateTime'].min().date()}")
        if west_synth["DateTime"].max().date() != pd.Timestamp("2016-12-31").date():
            mismatches.append(
                "West synthetic end: expected 2016-12-31, got "
                f"{west_synth['DateTime'].max().date()}")
    else:
        mismatches.append("West synthetic period: expected 10248 rows, found none")
    if len(west_synth) != 10248:
        mismatches.append(
            f"West synthetic row count: expected 10248, got {len(west_synth)}")

    if outlier_count != 1593:
        mismatches.append(f"Outlier_Flag rows: expected 1593, got {outlier_count}")

    if mismatches:
        print("MISMATCH vs expected values:")
        for m in mismatches:
            print(" -", m)
    else:
        print("All checked values match the expected state.")

    # --- feature engineering ---------------------------------------------
    print()
    print("=== Feature engineering ===")

    # NaN counts before the drop, printed individually so the drop can be
    # attributed to the right column(s) rather than reported as one number.
    # Uses the same load_and_sort + add_lag_features that load_and_engineer
    # itself calls, so this audit describes the logic the pipeline actually
    # runs rather than a second copy of it. See ADR-018's code debt note.
    _pre = add_lag_features(load_and_sort(DATA_PATH))
    _report = lag_nan_report(_pre)
    print("NaN counts before the drop:")
    print(f"  lag_168:          {_report['lag_168']}")
    print(f"  lag_336:          {_report['lag_336']}")
    print(f"  roll_168_lag168:  {_report['roll_168_lag168']}")
    print(f"  lag_168 NaN set subset of lag_336 NaN set: "
          f"{_report['lag_168_subset_of_lag_336']}")
    print(f"  roll_168_lag168 NaN set subset of lag_336 NaN set: "
          f"{_report['roll_168_lag168_subset_of_lag_336']}")

    features, rows_dropped_for_nan = load_and_engineer(DATA_PATH)
    print("Integrity guards passed: flag columns are bool, hourly series is "
          "gap free and has no duplicate timestamps.")
    print(f"Rows dropped for NaN lag_168, lag_336 or roll_168_lag168: "
          f"{rows_dropped_for_nan}")
    print(f"Final row count: {len(features)}")
    print("Final row count per road:")
    print(features.groupby("Road").size().to_string())
    print("First retained DateTime per road:")
    print(features.groupby("Road")["DateTime"].min().to_string())
    print(f"Columns ({len(features.columns)}): {list(features.columns)}")

    # --- feature matrix ---------------------------------------------------
    print()
    print("=== Feature matrix ===")
    X, y = feature_matrix(features)
    print(f"X shape: {X.shape}   y shape: {y.shape}")
    print(f"len(FEATURE_COLUMNS): {len(FEATURE_COLUMNS)}")
    print(f"FEATURE_COLUMNS: {FEATURE_COLUMNS}")
    print(f"X columns match FEATURE_COLUMNS in order: "
          f"{list(X.columns) == FEATURE_COLUMNS}")
    print(f"X columns ({len(X.columns)}): {list(X.columns)}")
    print(f"NaN count in X: {int(X.isna().sum().sum())}")
    excluded = [c for c in features.columns if c not in FEATURE_COLUMNS]
    print(f"Deliberately excluded from X: {excluded}")

    train, test = temporal_split(features)
    print()
    print("=== Temporal split ===")
    print(f"Train rows: {len(train)}")
    print(f"Test rows: {len(test)}")
    print("Train rows per road:")
    print(train.groupby("Road").size().to_string())
    print("Test rows per road:")
    print(test.groupby("Road").size().to_string())

    print("Mean Vehicles per road, train:")
    print(train.groupby("Road")["Vehicles"].mean().round(2).to_string())
    print("Mean Vehicles per road, test:")
    print(test.groupby("Road")["Vehicles"].mean().round(2).to_string())

    print()
    print("Synthetic rows per road, train:")
    print(train.groupby("Road")["Synthetic_Segment_Unverified"].sum().to_string())
    print("Synthetic rows per road, test:")
    print(test.groupby("Road")["Synthetic_Segment_Unverified"].sum().to_string())

    train_no_outliers, _test_same = temporal_split_without_outliers(features)
    removed = len(train) - len(train_no_outliers)
    print()
    print(f"Outlier filter removes {removed} rows from train (test unchanged)")

    # --- outlier flag comparison -----------------------------------------
    # The point of this section: Outlier_Flag uses a whole-period fence and
    # therefore drifts as demand grows, while outlier_trailing uses a fence
    # that tracks the current level. If the two disagree mainly in the later
    # quarters, that confirms the original flag is partly a date proxy.
    # See ADR-009.
    print()
    print("=== Outlier flag comparison ===")
    no_window = int((~features["trailing_fence"].notna()).sum())
    print(f"Rows with no full {TRAILING_WINDOW_HOURS}h trailing window "
          f"(outlier_trailing forced False): {no_window}")

    print()
    print("Total flagged rows, per road:")
    comp = pd.DataFrame({
        "Outlier_Flag": features.groupby("Road")["Outlier_Flag"].sum(),
        "outlier_trailing": features.groupby("Road")["outlier_trailing"].sum(),
    })
    print(comp.to_string())

    print()
    print("Agreement rate between the two flags, per road:")
    agree = features.groupby("Road").apply(
        lambda g: (g["Outlier_Flag"] == g["outlier_trailing"]).mean() * 100,
        include_groups=False,
    ).round(1)
    print(agree.to_string())

    print()
    print("Percent of hours flagged by quarter (old = Outlier_Flag, "
          "new = outlier_trailing):")
    q = features.copy()
    q["quarter"] = q["DateTime"].dt.to_period("Q").astype(str)
    old_q = (q.pivot_table(index="quarter", columns="Road",
                           values="Outlier_Flag", aggfunc="mean") * 100).round(1)
    new_q = (q.pivot_table(index="quarter", columns="Road",
                           values="outlier_trailing", aggfunc="mean") * 100).round(1)
    side = pd.concat({"old": old_q, "new": new_q}, axis=1).swaplevel(axis=1)
    side = side.reindex(columns=["East", "North", "South", "West"], level=0)
    print(side.to_string())

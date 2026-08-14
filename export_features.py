"""
Exploratory export: writes the FULL Stage 1 feature frame - every column
data_prep.py produces, not just the 13 model features in FEATURE_COLUMNS -
to data/feature_table.csv, with an added `split` column ("train"/"test").

WHY ALL COLUMNS, NOT JUST THE 13 MODEL FEATURES
--------------------------------------------------
The point of this export is to be able to open the file and check a row
by eye. Seeing lag_168 sitting next to its own DateTime, Road and Vehicles
is what makes an engineering error visible (wrong row, wrong road, wrong
week) - the 13 model features in isolation, with no DateTime/Road/Vehicles
to check them against, are not checkable at all.

No model training happens here, and data_prep.py is not modified - this
only calls its public functions and writes their output to disk.
"""

import os

import pandas as pd

from data_prep import DATA_PATH, load_and_engineer, temporal_split

OUTPUT_PATH = "data/feature_table.csv"

# ADR-019: the full feature table is gitignored (reproducible from committed
# code + committed data), so this sample is committed in its place - a
# contiguous slice a marker can open without cloning the repo and running
# the pipeline.
SAMPLE_OUTPUT_PATH = "data/feature_table_sample.csv"
SAMPLE_START = pd.Timestamp("2016-12-19 00:00")
SAMPLE_END = pd.Timestamp("2017-01-08 23:00")  # inclusive
# Three contiguous weeks (504h), not two, is the minimum window here.
# lag_336 reaches 336 hours back, so a row near the end of the sample can
# only be checked against a same-sample row if the sample carries at least
# 336 hours before it; two weeks (336h) would leave no margin at all. The
# window is also positioned to straddle 2017-01-01 so both split values
# ("train" and "test") appear in the sample.


def main():
    features, rows_dropped = load_and_engineer(DATA_PATH)

    # Use the real temporal_split() output (not a re-implemented date
    # comparison) so the "split" column reflects exactly what Stage 2
    # actually trains and tests on.
    train_df, test_df = temporal_split(features)
    train_df = train_df.copy()
    test_df = test_df.copy()
    train_df["split"] = "train"
    test_df["split"] = "test"

    combined = pd.concat([train_df, test_df], ignore_index=True)
    combined = combined.sort_values(["Road", "DateTime"]).reset_index(drop=True)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    combined.to_csv(OUTPUT_PATH, index=False)

    file_size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)

    print(f"Rows dropped upstream by load_and_engineer (NaN lag/roll): {rows_dropped}")
    print(f"Row count: {len(combined)}")
    print(f"Column count: {len(combined.columns)}")
    print(f"File size: {file_size_mb:.2f} MB")
    print(f"split value counts: {combined['split'].value_counts().to_dict()}")

    print()
    print('First 3 rows for Road == "North", DateTime >= 2016-01-08 '
          '(far enough in that lag_168 is populated):')
    preview = combined[
        (combined["Road"] == "North") & (combined["DateTime"] >= "2016-01-08")
    ].head(3)
    print(preview.to_string())

    # --- feature_table_sample.csv (ADR-019) -------------------------------
    sample = combined[
        (combined["DateTime"] >= SAMPLE_START) & (combined["DateTime"] <= SAMPLE_END)
    ].copy()
    sample = sample.sort_values(["Road", "DateTime"]).reset_index(drop=True)
    sample.to_csv(SAMPLE_OUTPUT_PATH, index=False)

    sample_size_kb = os.path.getsize(SAMPLE_OUTPUT_PATH) / 1024

    print()
    print(f"=== {SAMPLE_OUTPUT_PATH} (ADR-019 sample) ===")
    print(f"Window: {SAMPLE_START} to {SAMPLE_END} inclusive")
    print(f"Row count: {len(sample)}")
    print("Rows per road:")
    print(sample.groupby("Road").size().to_string())
    print(f"Column count: {len(sample.columns)} "
          f"(full table: {len(combined.columns)}; match: "
          f"{len(sample.columns) == len(combined.columns)})")
    print(f"split value counts: {sample['split'].value_counts().to_dict()}")
    print(f"File size: {sample_size_kb:.1f} KB")


if __name__ == "__main__":
    main()
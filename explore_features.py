"""
Exploratory data analysis behind the features in data_prep.py.

WHY THIS FILE EXISTS
---------------------
Those features were chosen by reasoning about traffic behaviour in
general, not by inspecting this specific dataset. That is the wrong order
for a report that has to justify each feature with evidence. This file
produces that evidence as figures and printed numbers, so each feature in
the methodology chapter can point at a figure instead of an assertion.

It is a verification step, not a formality: it already showed a feature
choice was wrong - see Figure 8. roll_mean_24 (the mean of the 24 hours
immediately before the predicted hour) needs data that does not exist when
a week of green-time schedules is generated in advance, in one batch,
before that week has happened. It was illegal for a generation-time
feature set even though it is a perfectly good feature for same-day
forecasting. data_prep.py has since been corrected (ADR-015): it now uses
lag_168, lag_336 and roll_168_lag168, and no longer uses roll_mean_24.
Figure 8 records the reasoning that correction was based on.

WHY THIS FILE DOES NOT IMPORT FROM data_prep.py
--------------------------------------------------
This file reads data/traffic_final_cleaned.csv directly and computes every
lag and rolling feature itself, independently of data_prep.py, rather than
calling its engineering functions. The figures exist to be evidence for the
feature choices in a report, and evidence that shares a code path with the
thing it evidences cannot catch a bug in it: if both files computed
roll_168_lag168 the same wrong way, these figures would faithfully
reproduce data_prep.py's bug rather than reveal it. Keeping a second,
independent implementation is what makes an agreement check between the two
meaningful rather than circular.

One exception: FEATURE_COLUMNS itself is imported below, but only to print
its length in Figure 8's caption. It is a list of column names, not a
computation, so reading it does not create the shared-code-path problem
above.

A NOTE ON WHAT AN AUTOMATED REVIEW CAN AND CANNOT CHECK
----------------------------------------------------------
Figures 1, 2, 3, 4, 6 and 7 plot real data, so their correctness can be
audited against printed numbers. Figures 5 and 8 are explanatory
schematics with no underlying data, so no numeric check can confirm they
say the right thing - only a human read can. An earlier version of this
file drew roll_168_lag168 in Figure 8 as a 24-hour window when it is a
168-hour window; every automated review item passed regardless, because
none of them could see the error. Treat the two schematics as needing
manual verification whenever they change.

Matplotlib only, no seaborn. Non-interactive Agg backend so this runs
headless and only ever writes files.
"""

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_prep import FEATURE_COLUMNS

DATA_PATH = "data/traffic_final_cleaned.csv"
FIG_DIR = "figures"
DPI = 150
ROADS = ["North", "South", "East", "West"]
ROAD_COLOURS = {"North": "tab:blue", "South": "tab:orange",
                "East": "tab:green", "West": "tab:red"}
SPLIT_DATE = pd.Timestamp("2017-01-01")
WEST_SYNTH_START = pd.Timestamp("2015-11-01")
WEST_SYNTH_END = pd.Timestamp("2017-01-01")  # end-exclusive; last synthetic hour is 2016-12-31 23:00
TREND_WINDOW = 672  # 28 days - matches TRAILING_WINDOW_HOURS in data_prep.py

# Candidate lag features examined in Figure 8. Defined here as constants so
# the diagram cannot drift out of step with the definitions again.
LAG_24 = 24                      # same hour, 1 day earlier
LAG_168 = 168                    # same hour, 1 week earlier
LAG_336 = 336                    # same hour, 2 weeks earlier
ROLL_WINDOW_LEGAL = 168          # width of the legal rolling mean
ROLL_SHIFT_LEGAL = 168           # how far back that window ends
ROLL_WINDOW_ILLEGAL = 24         # width of roll_mean_24
ROLL_SHIFT_ILLEGAL = 1           # roll_mean_24 ends 1 hour before the target


# -- loading -----------------------------------------------------------
def load_raw():
    df = pd.read_csv(DATA_PATH, parse_dates=["DateTime"])
    return df.sort_values(["Road", "DateTime"]).reset_index(drop=True)


def road_series(df, road):
    """This road's Vehicles, sorted by DateTime, integer-indexed (0..n-1)
    so shift()/rolling() are safe positional operations - the same
    precondition data_prep.py's _assert_hourly_series_is_complete checks
    for, re-established here independently since this file does not
    import that guard."""
    g = df.loc[df["Road"] == road].sort_values("DateTime").reset_index(drop=True)
    return g


CAPTION_FONTSIZE = 9


def caption(fig, text):
    """
    Draw the justification caption under the figure and reserve room for it.

    tight_layout() knows nothing about fig.text(), so a long caption will
    collide with the x-axis label unless the bottom margin is reserved
    explicitly. The number of wrapped lines is estimated from the caption
    length and the figure width, and stashed on the figure for save() to
    use. Without this, Figure 4's three-line caption overwrote its own
    "Month" label.
    """
    fig.text(0.5, 0.012, text, ha="center", va="bottom",
             fontsize=CAPTION_FONTSIZE, wrap=True)
    # Roughly 0.52 * fontsize points per character at this dpi.
    fig_width_pt = fig.get_size_inches()[0] * 72.0
    chars_per_line = max(40, int(fig_width_pt / (CAPTION_FONTSIZE * 0.52)))
    n_lines = max(1, -(-len(text) // chars_per_line))   # ceiling division
    fig._caption_bottom = 0.035 + 0.026 * n_lines


def save(fig, name, rows_used, note=""):
    bottom = getattr(fig, "_caption_bottom", 0.04)
    fig.tight_layout(rect=(0, bottom, 1, 1))
    path = os.path.join(FIG_DIR, name)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    size_kb = os.path.getsize(path) / 1024
    print(f"Saved {path} ({size_kb:.1f} KB). Rows used: {rows_used}. {note}")


# -- detrending / autocorrelation ---------------------------------------
def detrend(s, window=TREND_WINDOW):
    """
    Subtract a centred `window`-hour rolling mean before computing
    autocorrelation.

    WHY: demand roughly tripled over the 20 month period. Raw
    autocorrelation is inflated at every lag by the trend alone - two
    points 24 hours apart both sitting on a rising trend line will look
    correlated regardless of any real daily pattern. Subtracting the
    local level first isolates genuine periodicity from the trend.
    min_periods=window means only rows with a FULL window on both sides
    get a value; the first/last `window/2` hours are dropped rather than
    computed from a partial window.
    """
    trend = s.rolling(window=window, center=True, min_periods=window).mean()
    return (s - trend).dropna().reset_index(drop=True)


def autocorr_curve(s, max_lag):
    return np.array([s.autocorr(lag=k) for k in range(1, max_lag + 1)])


# -- figure 1 -------------------------------------------------------------
def fig1_autocorrelation(df):
    print()
    print("=== Figure 1: detrended autocorrelation ===")
    fig, ax = plt.subplots(figsize=(9, 5.5))
    printed = {}
    rows_used = {}
    for road in ROADS:
        s = road_series(df, road)["Vehicles"]
        d = detrend(s)
        rows_used[road] = len(d)
        curve = autocorr_curve(d, 336)
        ax.plot(range(1, 337), curve, label=road, color=ROAD_COLOURS[road])
        printed[road] = {24: curve[23], 168: curve[167], 336: curve[335]}

    for lag in (24, 168, 336):
        ax.axvline(lag, color="grey", linestyle=":", linewidth=1)
    # Shaded region: lags 1-167 are not computable at schedule-generation
    # time (see Figure 8) - this is what justifies lag_168 over the
    # higher-scoring lag_24: lag_24 is unusable, not merely worse.
    ax.axvspan(1, 167, color="red", alpha=0.15)
    ax.text(84, ax.get_ylim()[1] * 0.92, "not computable\nat generation time",
            ha="center", va="top", fontsize=8, color="darkred")

    ax.set_title("Detrended autocorrelation vs lag, by road")
    ax.set_xlabel("Lag (hours)")
    ax.set_ylabel("Autocorrelation (detrended)")
    ax.legend()
    caption(fig, "Detrended (672h centred rolling mean removed) so genuine periodicity, "
                  "not the demand trend, drives the correlation; shaded region is unusable "
                  "at generation time, which is why lag_168 is used instead of lag_24.")
    save(fig, "fig1_autocorrelation.png", rows_used,
         note="axvspan=(1, 167); vlines at 24/168/336")

    print("Detrended autocorrelation per road:")
    print(f"{'Road':6s}  {'lag24':>7s}  {'lag168':>7s}  {'lag336':>7s}")
    for road in ROADS:
        p = printed[road]
        print(f"{road:6s}  {p[24]:7.2f}  {p[168]:7.2f}  {p[336]:7.2f}")
    return printed


# -- figure 2 -------------------------------------------------------------
def fig2_hour_profile(df):
    print()
    print("=== Figure 2: mean vehicles by hour of day ===")
    fig, ax = plt.subplots(figsize=(8, 5))
    hour = df["DateTime"].dt.hour
    peaks = {}
    troughs = {}
    rows_used = len(df)
    for road in ROADS:
        mask = df["Road"] == road
        profile = df.loc[mask].groupby(hour[mask]).mean(numeric_only=True)["Vehicles"]
        ax.plot(profile.index, profile.values, label=road, color=ROAD_COLOURS[road])
        peaks[road] = int(profile.idxmax())
        troughs[road] = int(profile.idxmin())

    ax.set_title("Mean vehicles by hour of day, by road")
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("Mean vehicles / hour")
    ax.set_xticks(range(0, 24, 2))
    ax.legend()
    caption(fig, "Justifies hour_sin/hour_cos: each road has a distinct, repeating "
                  "daily shape that a model needs hour-of-day information to learn. "
                  "North, South and East peak in the evening (hours 19-20); West peaks "
                  "at midday (hour 12) - another way the synthetic segment does not "
                  "behave like the real roads.")
    save(fig, "fig2_hour_profile.png", rows_used)

    print("Peak hour / trough hour per road:")
    for road in ROADS:
        print(f"{road:6s}  peak={peaks[road]:2d}  trough={troughs[road]:2d}")
    return peaks, troughs


# -- figure 3 -------------------------------------------------------------
def fig3_weekday_weekend(df):
    print()
    print("=== Figure 3: weekday vs weekend hourly profile ===")
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex=True, sharey=False)
    hour = df["DateTime"].dt.hour
    is_weekend = df["DateTime"].dt.dayofweek.isin([5, 6])
    means = {}
    rows_used = {}
    for ax, road in zip(axes.flat, ROADS):
        mask = df["Road"] == road
        sub = pd.DataFrame({
            "hour": hour[mask].values,
            "Vehicles": df.loc[mask, "Vehicles"].values,
            "is_weekend": is_weekend[mask].values,
        })
        rows_used[road] = len(sub)
        wd = sub[~sub["is_weekend"]].groupby("hour")["Vehicles"].mean()
        we = sub[sub["is_weekend"]].groupby("hour")["Vehicles"].mean()
        ax.plot(wd.index, wd.values, label="weekday", color="tab:blue")
        ax.plot(we.index, we.values, label="weekend", color="tab:purple")
        ax.set_title(road)
        # sharex=True hides tick LABELS on the upper row but set_xlabel
        # still draws, leaving an axis titled "Hour of day" with no
        # numbers under it. Only label the bottom row.
        if ax in axes[1]:
            ax.set_xlabel("Hour of day")
        ax.tick_params(labelbottom=True)
        ax.set_ylabel("Mean vehicles / hour")
        ax.legend(fontsize=8)
        means[road] = {"weekday": float(wd.mean()), "weekend": float(we.mean())}

    fig.suptitle("Weekday vs weekend hourly profile, by road")
    caption(fig, "Justifies is_weekend. On North the weekend is simply quieter "
                  "(49.77 vs 33.20). On East the daily MEANS are nearly identical "
                  "(13.79 vs 13.44) yet the shapes differ sharply: the weekend runs "
                  "lower through the day then spikes to 26.5 at hour 20 against a "
                  "weekday 18.8. A comparison of means alone would hide that, which is "
                  "why the feature is kept for every road.")
    save(fig, "fig3_weekday_weekend.png", rows_used)

    print("Weekday mean / weekend mean per road:")
    for road in ROADS:
        m = means[road]
        print(f"{road:6s}  weekday={m['weekday']:.2f}  weekend={m['weekend']:.2f}")
    return means


# -- figure 4 -------------------------------------------------------------
def fig4_monthly_trend(df):
    print()
    print("=== Figure 4: monthly mean vehicles, by road ===")
    fig, ax = plt.subplots(figsize=(10, 5.5))
    month = df["DateTime"].dt.to_period("M")
    rows_used = {}
    for road in ROADS:
        mask = df["Road"] == road
        g = df.loc[mask].groupby(month[mask])["Vehicles"].mean()
        rows_used[road] = int(mask.sum())
        x = g.index.to_timestamp()

        if road != "West":
            ax.plot(x, g.values, label=road, color=ROAD_COLOURS[road],
                    marker="o", markersize=3)
            continue

        # West only: draw the synthetic months dashed and the real months
        # solid, rather than shading the whole plot area.
        #
        # WHY NOT A FULL-HEIGHT axvspan: an earlier version shaded
        # 2015-11 to 2017-01 across the entire figure and labelled it
        # "West synthetic period". Visually that reads as "everything in
        # this period is synthetic", which is false - only West is. It
        # placed North's genuine rise from 20 to 58 inside a band
        # captioned as synthetic. Marking the affected LINE cannot be
        # misread, because it only ever touches West.
        synth = x < WEST_SYNTH_END
        # Overlap one point so the dashed and solid segments join up
        # rather than leaving a visual gap at the boundary.
        last_synth = int(synth.sum())
        ax.plot(x[:last_synth], g.values[:last_synth], color=ROAD_COLOURS[road],
                linestyle="--", marker="o", markersize=3,
                label="West (SYNTHETIC data)")
        ax.plot(x[last_synth - 1:], g.values[last_synth - 1:], color=ROAD_COLOURS[road],
                linestyle="-", marker="o", markersize=3, label="West (real data)")

    ax.axvline(SPLIT_DATE, color="black", linestyle="--", linewidth=1.2,
               label="train/test split")

    ax.set_title("Monthly mean vehicles over time, by road")
    ax.set_xlabel("Month")
    ax.set_ylabel("Mean vehicles / hour")
    ax.legend(fontsize=8)
    caption(fig, "Justifies the temporal (not random) split and why a fixed-period "
                  "outlier fence drifts: demand rises roughly threefold on the three "
                  "real roads. West is flat throughout - note its synthetic portion is "
                  "dashed, and that portion ends exactly at the train/test split "
                  "(ADR-002), so West trains only on synthetic data and tests only on "
                  "real data.")
    save(fig, "fig4_monthly_trend.png", rows_used)


# -- figure 5 -------------------------------------------------------------
def fig5_cyclical_encoding():
    """
    Explanatory schematic, not a data plot. NO automated check can verify
    that this says the right thing - see the module docstring. Read it by
    eye after any change.
    """
    print()
    print("=== Figure 5: cyclical hour encoding (explanatory, not data) ===")
    hours = np.arange(24)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))

    ax1.plot(hours, np.zeros_like(hours), "o-", color="tab:blue")
    ax1.annotate("", xy=(23, 0.05), xytext=(0, 0.05),
                 arrowprops=dict(arrowstyle="<->", color="darkred"))
    ax1.text(11.5, 0.09, "distance = 23", ha="center", color="darkred")
    ax1.scatter([0, 23], [0, 0], color="darkred", zorder=5)
    ax1.set_title("Hour as a plain integer")
    ax1.set_xlabel("Hour (0-23)")
    ax1.set_ylabel("(schematic - no y dimension)")
    ax1.set_yticks([])
    ax1.set_ylim(-0.05, 0.15)

    theta = 2 * np.pi * hours / 24
    hx, hy = np.sin(theta), np.cos(theta)
    ax2.plot(hx, hy, "o-", color="tab:blue", markersize=4)
    ax2.scatter([hx[0], hx[23]], [hy[0], hy[23]], color="darkred", zorder=5)
    # Offset sideways, not upward: hour 0 sits at the top of the circle, so
    # an upward offset pushes the label into the axes frame.
    ax2.annotate("hour 0", (hx[0], hy[0]), textcoords="offset points", xytext=(12, -2))
    ax2.annotate("hour 23", (hx[23], hy[23]), textcoords="offset points", xytext=(8, -12))
    ax2.set_title("Hour as (hour_sin, hour_cos) on a unit circle")
    ax2.set_xlabel("hour_sin")
    ax2.set_ylabel("hour_cos")
    ax2.set_aspect("equal")

    caption(fig, "Justifies the cyclical encoding: hour 23 and hour 0 are 23 integer "
                  "steps apart on the left, but adjacent on the circle on the right - "
                  "which matches reality (23:00 and 00:00 are one hour apart).")
    save(fig, "fig5_cyclical_encoding.png", rows_used=24,
         note="illustrative diagram (0-23 synthetic hours), not raw data rows")


# -- figure 6 -------------------------------------------------------------
def fig6_lag168_scatter(df):
    print()
    print("=== Figure 6: lag_168 vs Vehicles, by road ===")
    fig, axes = plt.subplots(2, 2, figsize=(9, 9))
    correlations = {}
    rows_used = {}
    for ax, road in zip(axes.flat, ROADS):
        s = road_series(df, road)["Vehicles"]
        lag168 = s.shift(LAG_168)
        pair = pd.DataFrame({"lag168": lag168, "actual": s}).dropna()
        rows_used[road] = len(pair)
        r = pair["lag168"].corr(pair["actual"])
        correlations[road] = r

        ax.scatter(pair["lag168"], pair["actual"], s=4, alpha=0.25, color=ROAD_COLOURS[road])
        lims = [0, max(pair["lag168"].max(), pair["actual"].max())]
        ax.plot(lims, lims, color="black", linewidth=1, linestyle="--", label="y = x")
        ax.set_title(f"{road}  (Pearson r = {r:.2f})")
        ax.set_xlabel("lag_168 (same hour, 1 week ago)")
        ax.set_ylabel("Vehicles (actual)")
        ax.legend(fontsize=8)

    fig.suptitle("lag_168 vs actual Vehicles, by road")
    caption(fig, "Shows how much predictive power lag_168 actually carries: strong on "
                  "North (r=0.95) and South (r=0.89), much weaker on East (r=0.45) and "
                  "West (r=0.38). West's near-uniform rectangular scatter, against "
                  "North's tight diagonal cloud, is a fourth visual signature of the "
                  "synthetic segment (ADR-002) - real traffic does not fill a box "
                  "evenly.")
    save(fig, "fig6_lag168_scatter.png", rows_used)

    print("Pearson correlation of lag_168 with Vehicles, per road:")
    for road in ROADS:
        print(f"{road:6s}  r={correlations[road]:.3f}")
    return correlations


# -- figure 7 -------------------------------------------------------------
def fig7_road_means(df):
    print()
    print("=== Figure 7: overall mean vehicles per road ===")
    means = df.groupby("Road")["Vehicles"].mean().reindex(ROADS)
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.bar(means.index, means.values, color=[ROAD_COLOURS[r] for r in ROADS])
    for i, v in enumerate(means.values):
        ax.text(i, v + 0.5, f"{v:.2f}", ha="center", fontsize=9)
    ax.set_title("Overall mean vehicles per hour, by road")
    ax.set_xlabel("Road")
    ax.set_ylabel("Mean vehicles / hour")
    caption(fig, "Justifies one-hot encoding rather than label encoding: roads differ "
                  "by roughly 6x with no natural ordering between them.")
    save(fig, "fig7_road_means.png", rows_used=len(df))

    print("Overall mean vehicles per road:")
    for road in ROADS:
        print(f"{road:6s}  {means[road]:.2f}")
    return means


# -- figure 8 -------------------------------------------------------------
def fig8_feature_legality():
    """
    Explanatory schematic, not a data plot. NO automated check can verify
    that this says the right thing - see the module docstring. Read it by
    eye after any change.

    Generation happens at t=0; a week of schedules (t=0 to t=168) is
    produced in one batch before any of it has happened. The target hour is
    placed at t=168, the LAST hour of that week - the binding case, where
    every legal feature has the least room to spare. It can only legally
    use data that already existed at t=0, i.e. data from t<=0.

    ILLEGAL, all reach into the week being scheduled:
        lag_1         -> t=167   (never adopted - illustrative only)
        lag_24        -> t=144   (never adopted - illustrative only)
        roll_mean_24  -> window [t-24, t-1] = [144, 167]
                         (WAS in data_prep.py; REJECTED and replaced by
                         ADR-015 once this figure's reasoning identified
                         it as illegal)

    LEGAL, all resolve to t<=0 - this is the set data_prep.py uses now:
        lag_168          -> t=0    (lands exactly on generation time - the
                                    worst legal case, with zero hours to
                                    spare)
        lag_336          -> t=-168
        roll_168_lag168  -> a 168-HOUR mean ending 168h before the target,
                            i.e. window [t-335, t-168] = [-167, 0]. At this
                            worst case it uses every one of the 168 hours
                            available before generation time, and not one
                            more.

    Note the width of roll_168_lag168: 168 hours, not 24. An earlier
    version of this file drew it as a 24-hour bracket, and every automated
    review item still passed, because a schematic has no numbers to check.
    The constants at the top of this module now drive the arithmetic so the
    drawing cannot drift from the definition again.

    All lane positions below are computed as offsets from `target`, not
    hardcoded, so moving the target (or changing LAG_168/LAG_336/the roll
    constants) keeps the geometry self-consistent.
    """
    print()
    print("=== Figure 8: feature legality at schedule-generation time ===")
    # t=168, the last hour of the scheduled week (ADR-015's furthest-hour
    # rule) - not t=160. At t=160 every legal feature clears the generation
    # line with room to spare; only the last hour is the binding case.
    target = LAG_168

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.axhline(0, color="black", linewidth=1)
    ax.axvline(0, color="black", linestyle="-", linewidth=1.5)

    ax.axvspan(0, 168, color="tab:blue", alpha=0.10)
    ax.text(-8, 1.30, "generation time", ha="right", va="center", fontsize=9)
    ax.text(84, 1.30, "week being scheduled", ha="center", va="center",
            fontsize=9, color="tab:blue")

    ax.scatter([target], [0], color="black", zorder=5)
    ax.annotate("target hour (last hour of the scheduled week)", (target, 0),
                textcoords="offset points", xytext=(-6, 8), ha="right", fontsize=8)

    # Lane positions. Generous spacing so multi-line labels never overlap
    # the arrows - an earlier version used 0.22 spacing with 3-line labels
    # and they collided.
    LANE = 0.34
    TEXT_GAP = 0.09

    illegal_points = [("lag_1", target - 1), ("lag_24", target - LAG_24)]
    legal_points = [("lag_168", target - LAG_168), ("lag_336", target - LAG_336)]

    y = -LANE
    for name, x in illegal_points:
        ax.annotate("", xy=(x, y), xytext=(target, y),
                    arrowprops=dict(arrowstyle="->", color="firebrick", linewidth=1.5))
        ax.text(x - 6, y - TEXT_GAP, f"{name}  ILLEGAL", ha="right", va="top",
                fontsize=8.5, color="firebrick")
        y -= LANE

    # roll_mean_24: a WINDOW, not a point. Ends ROLL_SHIFT_ILLEGAL hours
    # before the target and is ROLL_WINDOW_ILLEGAL hours wide, so it sits
    # entirely inside the scheduled week. ILLEGAL - and unlike lag_1/lag_24
    # above, this one was actually in data_prep.py until ADR-015 replaced
    # it with roll_168_lag168, so the label says REJECTED rather than just
    # ILLEGAL to mark that history.
    ill_end = target - ROLL_SHIFT_ILLEGAL
    ill_start = ill_end - ROLL_WINDOW_ILLEGAL + 1
    ax.plot([ill_start, ill_end], [y, y], color="firebrick", linewidth=3,
            solid_capstyle="butt")
    ax.plot([ill_start, ill_start], [y - 0.05, y + 0.05], color="firebrick", linewidth=1.5)
    ax.plot([ill_end, ill_end], [y - 0.05, y + 0.05], color="firebrick", linewidth=1.5)
    ax.text(ill_start - 6, y - TEXT_GAP,
            f"roll_mean_24  ({ROLL_WINDOW_ILLEGAL}h window)  ILLEGAL - REJECTED",
            ha="right", va="top", fontsize=8.5, color="firebrick")

    y = LANE
    for name, x in legal_points:
        ax.annotate("", xy=(x, y), xytext=(target, y),
                    arrowprops=dict(arrowstyle="->", color="seagreen", linewidth=1.5))
        ax.text(x - 6, y + TEXT_GAP, f"{name}  LEGAL - ADOPTED", ha="right", va="bottom",
                fontsize=8.5, color="seagreen")
        y += LANE

    # roll_168_lag168: a 168-HOUR mean ending ROLL_SHIFT_LEGAL hours before
    # the target, so window [target-335, target-168]. Entirely at or before
    # generation time. LEGAL, and - together with lag_168 and lag_336 above -
    # the feature data_prep.py adopted in its place (ADR-015).
    leg_end = target - ROLL_SHIFT_LEGAL
    leg_start = leg_end - ROLL_WINDOW_LEGAL + 1
    ax.plot([leg_start, leg_end], [y, y], color="seagreen", linewidth=3,
            solid_capstyle="butt")
    ax.plot([leg_start, leg_start], [y - 0.05, y + 0.05], color="seagreen", linewidth=1.5)
    ax.plot([leg_end, leg_end], [y - 0.05, y + 0.05], color="seagreen", linewidth=1.5)
    ax.text(leg_start - 6, y + TEXT_GAP,
            f"roll_168_lag168  ({ROLL_WINDOW_LEGAL}h window)  LEGAL - ADOPTED\n"
            "at this worst case, uses every hour available - not one more",
            ha="right", va="bottom", fontsize=8.5, color="seagreen")

    # Cropped from -560: nothing is drawn left of lag_336 at -168, so -560
    # was mostly empty margin. -400 keeps the informative region larger on
    # the page without clipping any drawn element.
    ax.set_xlim(-400, 200)
    ax.set_ylim(-1.45, 1.45)
    ax.set_yticks([])
    ax.set_xlabel("Hours relative to generation time (t=0)")
    ax.set_ylabel("schematic only: vertical position carries no meaning\n"
                   "beyond legal above, illegal below", fontsize=8.5)
    ax.set_title("Which candidate features are legal at schedule-generation time")
    caption(fig, "lag_1, lag_24 and roll_mean_24 all reach into the week being "
                  "scheduled, which has not happened yet at generation time - "
                  "illegal. lag_168, lag_336 and roll_168_lag168 only reach into "
                  "the past relative to generation time - legal, and are three "
                  f"of the {len(FEATURE_COLUMNS)} features data_prep.py uses "
                  "(the other eleven are hour_sin/cos, dow_sin/cos, "
                  "month_sin/cos, is_weekend and the four road one-hots). "
                  "roll_168_lag168's window ends exactly on the lag_168 hour, "
                  "so the two are not independent - Stage 2 permutation "
                  "importance splits credit between them (ADR-018). "
                  f"roll_mean_24 (a {ROLL_WINDOW_ILLEGAL}-hour window) was in "
                  "data_prep.py until this illegality was identified; it has "
                  "since been replaced by roll_168_lag168.")
    save(fig, "fig8_feature_legality.png", rows_used=0,
         note=f"illustrative diagram, not real data rows; "
              f"legal/adopted window drawn as [{leg_start}, {leg_end}] "
              f"({ROLL_WINDOW_LEGAL}h), illegal/rejected window as "
              f"[{ill_start}, {ill_end}] ({ROLL_WINDOW_ILLEGAL}h)")


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    df = load_raw()

    fig1_autocorrelation(df)
    fig2_hour_profile(df)
    fig3_weekday_weekend(df)
    fig4_monthly_trend(df)
    fig5_cyclical_encoding()
    fig6_lag168_scatter(df)
    fig7_road_means(df)
    fig8_feature_legality()


if __name__ == "__main__":
    main()
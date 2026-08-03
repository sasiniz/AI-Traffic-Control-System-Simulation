# Decision Log

Append only. Never edit or delete an entry. If a decision changes, add a
new entry and mark the old one "Superseded by ADR-XXX".

Record DECISIONS, not code changes. Git already records code changes. An
entry is warranted only when a choice had a real alternative that was
rejected.

**Backfill note.** ADR-001 to ADR-008 were all written on 2026-08-03,
consolidating decisions taken across earlier sessions. The date on each
entry is therefore the date the record was created, not the date the
decision was made. The commit history in this repository is the
authoritative record of when each change was actually applied. Entries
from ADR-009 onward are written at the time of the decision, so their
dates are decision dates.

---

## ADR-001: Kaggle traffic dataset relabelled as one four arm junction

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The project is motivated by Kesbewa junction in Sri Lanka, where all four
approaches receive equal green time regardless of demand. Real Sri Lankan
intersection data is not publicly available in structured, time series
form. A model cannot be trained without hourly vehicle counts per
approach.

**Decision**
Use the Kaggle Traffic Prediction Dataset by fedesoriano, which contains
hourly vehicle counts for four separate junctions, and relabel those four
series as the North, South, East and West arms of a single junction. The
working file is traffic_final_cleaned.csv: 58,368 rows, four roads by
14,592 hourly slots, covering 2015-11-01 to 2017-06-30.

**Alternatives rejected**
Collecting primary count data at Kesbewa junction. Rejected on scope and
ethics grounds within a single academic year.
Using the Wijesekara and Pushpakumara (2025) MERCon paper on Nupe
Junction as a data source. Rejected because it publishes only output
performance metrics, not raw vehicle count time series. It is used as a
citation supporting the synthetic and proxy data approach, not as
training data.

**Consequences**
The four series are statistically independent, so any correlation a real
junction would show between opposing arms is absent, and total demand
across the four arms is not conserved the way it would be at a real
intersection. Mean hourly counts differ by roughly six times between
arms (North 45, South 14, East 14, West 7), so the resulting schedule
will consistently favour North. This is a construct validity limitation
and must be stated plainly in the methodology chapter rather than left
for a reader to discover. It does not affect the security, governance or
anomaly detection contributions of the project, which are the primary
contributions.

**Sources**
fedesoriano, "Traffic Prediction Dataset," Kaggle, 2021. [Online].
Available: https://www.kaggle.com/datasets/fedesoriano/traffic-prediction-dataset

---

## ADR-002: Synthetic West extension kept and disclosed

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The West series is shorter than the other three. Its real data begins
2017-01-01, while North, South and East begin 2015-11-01. A model trained
on a ragged panel would either drop those rows entirely or require
special handling for one arm.

**Decision**
Extend the West series backward with synthetic data covering 2015-11-01
to 2016-12-31, and mark every synthetic row with the column
`Synthetic_Segment_Unverified`.

**Alternatives rejected**
Dropping the West arm and modelling a three arm junction. Rejected
because the four arm layout is the whole point of the Kesbewa
observation.
Truncating all four series to the West overlap window. Rejected because
it discards roughly 14 months of real data from three arms to fix one.
Extending West without marking it. Rejected outright as academically
dishonest.

**Consequences**
10,248 of 14,592 West rows are synthetic, roughly 70 percent. Any
accuracy figure for West measures the data generator rather than real
traffic, and must be reported as a separate row in the evaluation table
rather than averaged into an overall score.
The synthetic period ends exactly at 2017-01-01, which is also the
train/test boundary chosen in ADR-008. The consequence is severe and was
not intended: under that split, every West training row is synthetic and
every West test row is real. The West model is therefore trained entirely
on generated data and evaluated entirely on real data. This is expected
to produce poor West performance, and that result is honest rather than a
fault to be corrected by moving the split. See ADR-008 for the reasoning
on why the split is kept anyway.
The `Synthetic_Segment_Unverified` column must survive all feature
engineering so this separation stays possible at evaluation time.
A third signature of the synthetic segment was found on 2026-08-03: the
West monthly mean sits between 7.1 and 7.6 for all fourteen synthetic
months, while all three real roads trend upward strongly over the same
period. Real traffic data does not hold that flat. The synthetic segment
is therefore detectable by inspection, not only by the marker column.

**Sources**
None, design decision. Boundary dates verified directly against
traffic_final_cleaned.csv on 2026-08-03.

---

## ADR-003: One arm green at a time

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The original SignalController paired opposing arms into two phases,
North+South green together and East+West green together. The planned
turning feature would have required conflict resolution between opposing
traffic inside the junction box.

**Decision**
Rewrite SignalController as a four phase ring over
["North","South","East","West"], one arm green at a time, amber between
every step.

**Alternatives rejected**
Keeping the two phase pairing and adding yield logic for opposing turns.
Rejected because it would have required modelling gap acceptance, which
is a large piece of work and not the academic focus of this project.

**Consequences**
Turning became implementable as a purely visual feature with no oncoming
conflict. Full cycle length roughly doubled because arms no longer share
green, so average per arm wait increased. This tradeoff is accepted and
must be acknowledged in the evaluation.
One accepted edge case remains: a vehicle from one phase can still
geometrically cross the path of a vehicle from the next phase while
clearing the junction box. Closing it would require an all red clearance
interval or real yielding logic, both out of scope for this project.

**Sources**
None, design decision.

---

## ADR-004: Schedule divided into hourly blocks of exactly 3600 seconds

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The Random Forest predicts vehicle counts per road per hour, so green
time allocation is naturally computed per hour. A signal cycle of roughly
100 seconds does not divide evenly into 3600 seconds, so the last cycle
of each hour will not land on the hour boundary.

**Decision**
Every schedule hour sums to exactly 3600 seconds of green plus amber. The
final phase of each hour is adjusted so the block closes exactly, and the
next hour begins under a new plan.

**Alternatives rejected**
Continuous cycles that cross hour boundaries, as real controllers use.
Rejected because the boundary would then fall mid phase, meaning a phase
could begin under one hour's plan and finish under the next, making
delivered green time unattributable to either prediction. Clean blocks
keep every second of green traceable to one specific model output, which
is what the audit and governance requirements need.

**Consequences**
This is the decision that creates the boundary problem resolved by
ADR-006. Without it, ADR-006 would not be needed.
The system deviates from real controller behaviour, which must be
acknowledged as a simplification in the evaluation chapter.
In exchange, the schedule becomes fully auditable: any delivered green
period can be traced to exactly one prediction for one hour, which is
what makes an approval and signing workflow meaningful.

**Sources**
None, design decision.

---

## ADR-005: Signal timeline compiled offline, simulation only plays it back

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The project's core rule is that signal timing is pre-planned and never
reacts to live incidents. When timings were computed inside
SignalController, that rule was a convention that any future edit could
break.

**Decision**
generate_timeline.py compiles a full phase by phase CSV offline.
traffic_sim.py loads that CSV and steps through it by elapsed time. All
timing logic and the AMBER_S constant were removed from traffic_sim.py.

**Alternatives rejected**
Keeping computation inside SignalController with a comment forbidding
reactive behaviour. Rejected because a comment is not an enforcement
mechanism.

**Consequences**
The rule is now structural rather than conventional. SignalController
cannot react to incidents because it holds no timing logic at all.
Replacing the CSV with real Random Forest output requires no code change.
Playback is decoupled from the simulation clock, so the CSV start_hour
column is provenance information only, not something the simulation
synchronises against.
A security consequence follows directly: the schedule CSV is now the
single artefact that determines signal behaviour. That makes it the
artefact that must be authenticated, integrity protected and replay
protected before deployment, and it is why the security design centres on
the schedule file rather than on the channel alone.

**Sources**
None, design decision.

---

## ADR-006: MIN_GREEN_TO_START set to 12 seconds

**Date:** 2026-08-03
**Status:** Accepted

**Context**
Following ADR-004, each schedule hour must close at exactly 3600 seconds.
At the end of an hour the next road in rotation may have only a few
seconds available. Starting a phase that short wastes a large share of it
on start-up lost time.

**Decision**
If the next road would receive less than 12 seconds of green, it does not
start. The current road's green is extended to fill the hour exactly, and
the skipped road becomes the first phase of the next hour. If the next
road would receive 12 seconds or more, it starts with that shortened
green and the hour ends there.

**Alternatives rejected**
Using the 7 second UK regulatory minimum as the threshold. Rejected
because a 7 second phase is legal but clears almost nothing, and the road
would then take two turns back to back, since it opens the next hour
anyway.
Always extending the current road regardless of how much time is left.
Rejected because it would discard usable green time in hours where the
leftover is substantial.

**Consequences**
Extension is bounded at the planned green plus at most 14 seconds, since
the extend branch only fires when fewer than 15 seconds remain and the
generator works in whole seconds.
The rotation shifts by one position at each hour boundary, which prevents
the same road being starved every hour.
Delivered green will differ from planned green at every hour boundary, so
the evaluation must measure planned versus delivered as a separate
quantity rather than treating the difference as model error.

**Sources**
Derived as start-up lost time (2.0 s) plus five vehicles at saturation
headway (1.9 s) = 11.5 s, rounded up to 12 s because the generator works
in whole seconds. Values from Highway Capacity Manual, 6th ed.,
Transportation Research Board, Washington, DC, 2016.
Five vehicles chosen to match ANOMALY_QUEUE_MIN in traffic_sim.py, so the
smallest queue the anomaly detector treats as significant is the same
queue this threshold is sized against.
The 7 second figure in the rejected alternative is from Department for
Transport, Traffic Signs Manual Chapter 6: Traffic Control. London, UK:
TSO, 2019, section 6.11.3.

---

## ADR-007: Random Forest predicts vehicle counts, not green seconds

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The submitted project description document states the model output target
is "recommended green-light duration in seconds". The dataset contains no
green-light durations. It contains vehicle counts only.

**Decision**
The Random Forest predicts vehicle count per road per hour. A separate
deterministic layer converts predicted counts into green seconds using
Webster's method, constrained by the UK minimum green and maximum cycle
values in Traffic Signs Manual Chapter 6.

**Alternatives rejected**
Training directly on green duration. Rejected because the labels would
have to be generated by our own allocation formula first, so the model
would only learn that formula back. This is circular and not a valid
supervised learning problem.

**Consequences**
This is a deliberate departure from the submitted project description and
must be stated explicitly in the methodology chapter.
The two layer split makes the system explainable, which the governance
chapter requires, and means schedule quality depends on two separately
testable components rather than one opaque one.
One consequence remains to be verified empirically. At the observed
traffic volumes, roughly 80 vehicles per hour across all four arms, the
sum of flow ratios is small and Webster's formula may return a cycle
length shorter than four minimum greens can fit into. If that happens,
the minimum green floor rather than Webster's formula will be the binding
constraint on the timings. This must be checked with real numbers at
Stage 3 and reported accurately. Claiming that Webster's method
determined the timings when the minimum green floor actually did would be
a false statement about the method.

**Sources**
F. V. Webster, "Traffic signal settings," Road Research Technical Paper
No. 39, Road Research Laboratory, HMSO, London, UK, 1958.
Department for Transport, Traffic Signs Manual Chapter 6: Traffic
Control. London, UK: TSO, 2019.

---

## ADR-008: Temporal train/test split, not random 80/20

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The submitted project description specifies a random 80/20 split. Hourly
traffic counts are strongly autocorrelated, so neighbouring rows are near
duplicates of each other.

**Decision**
Train on rows before 2017-01-01, test on rows from 2017-01-01 onward.

**Alternatives rejected**
Random 80/20 split as originally specified. Rejected because it places
09:00 in training and 10:00 the same day in testing, so the model would
score well by memorising neighbouring hours rather than learning the
underlying pattern.
Rolling origin cross validation. Not rejected on merit. It is the
stronger evaluation method, but a single held out future period is
sufficient to demonstrate the point at this scope. Deferred, and worth
revisiting if time allows.
Moving the split date to avoid the West synthetic boundary described in
ADR-002. Rejected because any split that mixes synthetic and real West
data into both training and test would hide the problem rather than
measure it, and because there is no real West data before 2017-01-01 to
move the boundary into.

**Consequences**
Reported accuracy will be lower than a random split would show, and that
lower figure is the honest one.
The split mirrors real deployment, where the model predicts a future it
has not seen. This is the second deliberate departure from the submitted
project description.
The test period, January to June 2017, is seasonally narrow and does not
cover a full year, which limits claims about annual adaptability.
The split boundary coincides exactly with the West synthetic boundary, so
West is trained purely on synthetic data and tested purely on real data.
North, South and East do not have this problem. West results must
therefore be reported and discussed separately from the other three arms
throughout the evaluation.
A further consequence measured on 2026-08-03: demand is not stationary
across the split. Mean vehicles per hour rise from train to test on every
real road (North 36.92 to 64.94, South 11.31 to 21.34, East 12.17 to
17.56), while synthetic West stays flat (7.27 to 7.25). The training and
test sets are therefore not drawn from the same distribution, and no
choice of split date would fix that, because the trend runs through the
entire period. This makes lag_168 and roll_mean_24 load bearing rather
than optional: they are the only features carrying level information, and
without them the model would predict a fixed value per road. It also
means the Random Forest's inability to extrapolate beyond its training
range becomes relevant, although the effect is bounded: only 0.1 to 1.5
percent of test rows per road exceed the training maximum.

**Sources**
C. Bergmeir and J. M. Benítez, "On the use of cross-validation for time
series predictor evaluation," Information Sciences, vol. 191,
pp. 192-213, May 2012, doi: 10.1016/j.ins.2011.12.028.

---

## ADR-009: Outlier_Flag recomputed on a trailing window

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The Outlier_Flag column in traffic_final_cleaned.csv was reverse
engineered on 2026-08-03 as a per-road Tukey fence, Vehicles > Q3 +
1.5*IQR, computed once across the whole 2015-11 to 2017-06 period. That
rule reproduces the existing column with 100 percent agreement on all
four roads.

Because demand grew roughly threefold over the period, a fence fixed to
the whole-period distribution drifts. Measured flag rate for South: 0.0
percent in every quarter of 2016, rising to 6.2 percent in 2017 Q1 and
31.6 percent in 2017 Q2. North went from 0.0 percent through 2016 to 6.4
percent in 2017 Q2. Those rows are ordinary traffic at a higher level,
not anomalies. The column is therefore substantially a proxy for date.

**Decision**
Keep Outlier_Flag unchanged in the CSV for provenance. Add a derived
column `outlier_trailing` in data_prep.py, using the same Tukey fence
but computed per road on a trailing 28 day (672 hour) window ending at
the previous row. Downstream anomaly work uses outlier_trailing.

**Alternatives rejected**
Correcting Outlier_Flag in the CSV itself. Rejected because the original
column is referenced in earlier documentation and the comparison between
the two flags is itself a reportable result.
Dropping outlier handling entirely. Rejected because the Isolation
Forest at Stage 6 needs a defensible notion of what is unusual.
Using a per-year or per-quarter fence. Rejected because the boundaries
would be arbitrary and a row on 1 January would be judged against a
window it barely overlaps.

**Consequences**
Measured effect, verified on 2026-08-03 and reproduced independently on
Windows and Linux with identical results:

    Road    Outlier_Flag    outlier_trailing    agreement
    East         473              719             97.0%
    North        157               14             98.9%
    South        831               32             94.2%
    West         131              123             99.4%

West is the control case. It has no trend, and the two flags agree 99.4
percent of the time, which shows the trailing method only changes results
where the trend actually is rather than reshuffling everything. South's
2017 Q2 rate falls from 31.6 percent to 0.0 percent. East rises rather
than falls, because it is the spikiest road (maximum 180 against a mean
of 17) and the whole-period fence sat high enough to miss genuine local
spikes, particularly in early 2016.

2,016 rows have no full trailing window and are set to False rather than
True, since an unknown fence is not evidence of an anomaly.

The effect at Stage 6 would have been large. Training or evaluating the
Isolation Forest against the original flag would have produced a model
that detects the year and scores well while measuring nothing. This is
the same failure mode as the discarded queue-length anomaly rule recorded
in CLAUDE.md, and harder to detect because the scores would look good.

The comparison between the two flags is a reportable finding in its own
right, demonstrating that an outlier definition must be relative to the
current level when the underlying series is non-stationary.

A known limitation remains. The trailing fence is computed across all
hours in the window, so the interquartile range is dominated by the daily
cycle rather than by anomaly. On North, whose hourly counts swing from
near zero overnight to near 100 at peak, this makes the fence almost
unreachable: only 14 of 14,592 hours are flagged, against roughly 0.7
percent for a well behaved series. Stage 6 should compute the fence per
road AND per hour of day, so an 08:00 reading is judged against other
08:00 readings. A 28 day window supplies 28 samples per hour slot, which
is sufficient for a quartile estimate. Not implemented at Stage 1 because
nothing before Stage 6 depends on it.

**Sources**
J. W. Tukey, Exploratory Data Analysis. Reading, MA: Addison-Wesley,
1977. (Origin of the 1.5*IQR fence.)

---

## ADR-010: Stage 2 trains on all rows, outliers included

**Date:** 2026-08-03
**Status:** Accepted

**Context**
The Stage 1 task specification, and the module docstring of data_prep.py
that resulted from it, stated that flagged rows should be removed from the
training set so the model "learns normal behaviour". That reasoning is not
recorded in any earlier ADR; it originated in the task wording and was
carried over from anomaly detection, where learning normal genuinely is
the objective. ADR-008 established the temporal split itself and says
nothing about outlier exclusion.

Stage 2 is not anomaly detection. It is a vehicle count predictor whose
output is converted into green time by the Stage 3 allocation layer. Rush
hour peaks are precisely the hours where green time allocation matters
most.

**Decision**
The Stage 2 Random Forest trains on ALL rows, including flagged ones.
temporal_split_without_outliers stays in data_prep.py unchanged, unused
by Stage 2, and is reserved for the Isolation Forest at Stage 6. When
Stage 6 uses it, it must be changed to key on outlier_trailing rather
than Outlier_Flag, per ADR-009.

**Alternatives rejected**
Excluding flagged rows from Stage 2 training, as the Stage 1 task
specification directed. Rejected because a count predictor trained
without peaks will systematically underpredict rush hour, and the
allocation layer would then under-allocate green at the busiest hour of
the day. That is the opposite of what the system exists to do.
Training two models, one with and one without. Rejected as unnecessary:
under the original flag the exclusion removes only 347 rows, 0.86 percent
of the training set, so the two models would be near identical and the
comparison would not be informative.

**Consequences**
Reported Stage 2 accuracy includes peak hours, which are the hardest to
predict, so the error figures will be higher than a peak-excluded model
would show. That higher figure is the honest one and is also the one
relevant to schedule quality.
The distinction between "normal behaviour" for anomaly detection and
"full behaviour" for forecasting must be stated explicitly in the
methodology chapter, because the same dataset serves both models with
different filtering.
The data_prep.py docstring was corrected on 2026-08-03 to match this
decision. Before that correction it stated the opposite, and a Stage 2
implementation reading only the docstring would have excluded the peaks.
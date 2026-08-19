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

---

## ADR-011: Overall metrics exclude West

**Date:** 2026-08-04
**Status:** Accepted

**Context**
ADR-002 established that West trains entirely on synthetic data and tests
entirely on real data, so its error measures how closely the synthetic
generator resembles real West traffic rather than how well the model
forecasts West traffic. It records that West must never be averaged into
a headline figure, but does not say what a headline figure should then
contain.

**Decision**
Every overall MAE and RMSE in the evaluation is computed over East, North
and South only, and is labelled OVERALL_excl_West. West is always
reported on its own clearly separated line. This applies to the naive
baseline as well as to the model, so the two overall figures cover the
same population and can be compared directly.

**Alternatives rejected**
Reporting a four road overall alongside the three road one. Rejected
because two similar looking headline numbers invite the wrong one being
quoted, and the four road figure has no valid interpretation.
Weighting West down rather than excluding it. Rejected because any weight
would be arbitrary and would still blend two different measurements.

**Consequences**
Measured naive baseline over East, North and South: MAE 5.22, RMSE 9.27.
The four road figure, MAE 4.58 and RMSE 8.23, is lower only because West
is both low volume and synthetic. That four road figure appears in the
early Stage 2 output and in this project's history; it must not be quoted
as a result.
Any future evaluation script must apply the same exclusion to both
baseline and model, or the comparison becomes meaningless.

**Sources**
None, design decision. Metrics measured on 2026-08-04.

---

## ADR-012: Schedule regenerated weekly, not annually

**Date:** 2026-08-04
**Status:** Accepted
**Amends the deployment horizon described in CLAUDE.md and in the
submitted project description. Does not affect ADR-004, ADR-005 or
ADR-006, which concern how a schedule block is compiled rather than how
far ahead it is compiled.**

**Context**
The project description and CLAUDE.md both stated that the AI generates a
full year of hourly signal timings in advance. Two of the model's
thirteen features make that impossible:

    lag_168       vehicle count 168 hours (7 days) earlier
    roll_mean_24  mean of the previous 24 hours

Neither value exists when planning a year ahead. Predicting 14:00 next
July requires the count from seven days before next July, which has not
happened. The model as built can forecast roughly one week ahead, not one
year.

The obvious alternative, dropping both features so only calendar features
remain, was measured on 2026-08-04:

    Road    mean actual    calendar-only prediction    MAE
    North       64.94              31.05              33.90
    South       21.34              10.63              10.73
    East        17.56              11.59               7.13
    West         7.25               7.23               2.27

North is predicted at less than half its actual volume. The allocation
layer would give North roughly half the green time it needs, every hour,
all year.

The cause is structural rather than a tuning problem. Demand roughly
tripled across the dataset (ADR-008), and a Random Forest predicts the
mean of the training rows in a leaf, so it can never output a value above
its training range. Without lag_168 and roll_mean_24 nothing carries the
level forward, and the model predicts the 2016 average into 2017. No
hyperparameter changes this, because averaging leaf values is what a
forest does. West appears unaffected only because its training data is
synthetic and flat, so there is no trend to miss.

**Decision**
The schedule is regenerated weekly (or monthly, to be fixed at Stage 3)
from the most recent available data, rather than once annually. The word
"annually" is removed from the project's description of its own
deployment model.

**Alternatives rejected**
Keeping the annual horizon and accepting calendar-only accuracy. Rejected
because North being underpredicted by 52 percent makes the Stage 3
allocation layer meaningless, and every downstream result would rest on
numbers already known to be wrong.
Recursive forecasting: predict week 1, feed those predictions back in as
the lag features for week 2, and iterate. Rejected because error compounds
over 52 iterations and the complexity is not justified at this scope.
Training two models, a short horizon one with lag features and a long
horizon calendar-only one, and reporting both. Not rejected on merit, and
the calendar-only measurement above is retained as evidence. Rejected as a
deployment design because a schedule that is known to be wrong should not
be deployable at all, even as an option.

**Consequences**
The academic core of the project is unaffected. The unbreakable rule is
that signal timing is PRE-PLANNED and never reacts to a live incident. A
weekly regenerated schedule is still compiled offline, still human
authenticated before deployment, and still blind to accidents, queues and
anomalies. SignalController still holds no timing logic. Only the
regeneration interval changes.
The security architecture arguably strengthens. A schedule redeployed
weekly passes through the authentication and signing workflow fifty two
times a year instead of once, so replay protection and sequence numbering
become load bearing rather than decorative.
CLAUDE.md must be corrected: it currently states the annual horizon in
the "one rule that must never break" section, which risks a future
session treating the wrong horizon as the protected invariant.
The methodology chapter must state this as a deliberate amendment to the
submitted project description, alongside ADR-007 and ADR-008, and should
present the calendar-only measurement as the evidence for it. This is a
finding, not a retreat: it demonstrates empirically that annual
pre-planning is not achievable on a non-stationary series with a
tree-based model.

**Sources**
None, design decision. Calendar-only measurement performed on 2026-08-04
using the same data, split and model configuration as Stage 2.

---

## ADR-013: Model hyperparameters tuned on a validation split, artefact not committed

**Date:** 2026-08-04
**Status:** Accepted

**Context**
Stage 2 trained with n_estimators=300 and sklearn defaults for max_depth
and min_samples_leaf. Default min_samples_leaf=1 with unlimited depth
grows every tree until each leaf holds a single training row, so the
forest effectively stores the 40,320 row training set 300 times. The saved
artefact was 896 MB, above GitHub's 100 MB limit for plain git, and
unwieldy for a "sign and deploy" workflow.

Raising min_samples_leaf reduces the artefact substantially, but choosing
its value by comparing test set error would turn the held-out future into
training data and would destroy the honest evaluation established in
ADR-008 and ADR-011.

**Decision**
min_samples_leaf and any other hyperparameter are chosen on a validation
split carved TEMPORALLY out of the training set (November and December
2016), never on the test set. The test set is evaluated exactly once,
after the configuration is fixed.
The model is saved with joblib compress=3.
models/count_model.joblib is added to .gitignore.
models/model_card.json IS committed.

**Alternatives rejected**
Choosing min_samples_leaf by comparing test set error across candidate
values. Rejected as test set leakage. Note for the record: on 2026-08-04
test set error WAS observed across four candidate values during
investigation of the artefact size problem. Those observations are
therefore contaminated and are not used to select the value. The
validation procedure above is run independently.
Git LFS for the artefact. Rejected as unnecessary complexity for a file
that is fully reproducible from committed code, committed data and a
pinned random_state.
Committing the compressed artefact. Rejected because it would still be
large, would grow the repository on every retrain, and adds nothing that
the model card plus a pinned seed does not already provide.

**Consequences**
The model is reproducible rather than stored: data_prep.py, train_model.py,
data/traffic_final_cleaned.csv and random_state=42 together regenerate it
in roughly 13 seconds. The model card records the training provenance,
which is what Stage 7 needs in order for an operator approval to mean
anything.
Reported accuracy will change from the Stage 2 figures once
min_samples_leaf is fixed, so the Stage 2 numbers are superseded and must
not be quoted as final results.
A separate finding emerged and is recorded here because it constrains
tuning: North's error RISES as min_samples_leaf rises. North is smooth and
strongly trending, so larger leaves mean more averaging, and averaging
pulls predictions toward the lower training mean. This is why no
hyperparameter setting makes the model beat the naive lag_168 baseline on
North. The problem is not overfitting.

**Sources**
None, design decision.

---

## ADR-014: Stage 2 result recorded, decision on the model deferred

**Date:** 2026-08-04
**Status:** Accepted

**Context**
Stage 2 measured the Random Forest against a seasonal naive baseline
(predict each hour as the same hour one week earlier, which is lag_168
used directly). Result on the test set, min_samples_leaf=1:

    Road    Baseline MAE/RMSE    Model MAE/RMSE    Beat baseline?
    East       6.49 / 12.54       4.69 /  8.86          YES
    North      6.30 /  9.31       7.27 / 10.62          NO
    South      2.88 /  3.73       3.08 /  4.23          NO
    West       2.65 /  3.66       2.03 /  2.76          YES
    OVERALL    5.22 /  9.27       5.02 /  8.35          marginal

The model loses to a one line baseline on two of the four roads.

The explanation is consistent across both roads. North and South are
smooth and trending. lag_168 alone already carries the daily shape and the
current level exactly. A forest averages the training rows in each leaf,
and that averaging pulls every prediction toward the training mean, which
is lower than the test period. North's mean actual is 64.94 against a mean
prediction of 61.26. East, which is spiky (maximum 180 against a mean of
17), is where averaging helps and the model wins clearly.

**Decision**
Record the result. Do not decide the model's fate yet. Re-measure after
ADR-013's validation-based tuning is complete, then decide.

**Alternatives rejected**
Discarding the Random Forest now and using the naive baseline throughout.
Premature: the tuning in ADR-013 has not been run, and the model already
wins clearly on East and West.
Quietly proceeding to Stage 3 without recording that the model loses on
two roads. Rejected outright. The comparison against a baseline exists
precisely so this cannot pass silently, and a model that fails to beat a
one line rule is a reportable finding rather than an embarrassment.
Tuning until the model beats the baseline everywhere. Rejected because
that is selecting on the outcome, and because ADR-013 shows the direction
of tuning that helps East and South makes North worse.

**Consequences**
A hybrid remains open as a Stage 3 option: use the seasonal naive
prediction on smooth roads and the model on spiky ones. It would need its
own ADR and its own justification, and it complicates the single-artefact
signing story in ADR-013, so it is not adopted by default.
One test row is worth carrying forward to Stage 6: East on 2017-02-23 at
19:00, actual 180 against a prediction of 25.7. The model was not merely
capped at its training maximum of 134, it predicted 26, meaning neither
lag_168 nor roll_mean_24 gave any warning. That is an anomaly rather than
a forecasting failure, and it is exactly the class of event the Isolation
Forest exists to detect.
A note on an apparent inconsistency in the records: ADR-008 states that
0.1 to 1.5 percent of test rows per road exceed the training maximum,
while the Stage 2 run reports 0.06 percent. Both are correct. ADR-008
measured each road against that road's own training maximum; the trained
model has one global training maximum of 134 across all four roads. The
per-road figure is the relevant one when reasoning about a per-road model,
the global figure when reasoning about the single model actually built.

**Sources**
None, measurement record. Figures produced on 2026-08-04 by
train_model.py at min_samples_leaf=1.
---

## ADR-015: Feature set corrected for the schedule-generation horizon

**Date:** 2026-08-04
**Status:** Accepted
**Amends the FEATURE_COLUMNS list established at Stage 1. Supersedes the
conclusion of ADR-014, which was measured on a feature set that cannot be
deployed. Does not affect ADR-008 (the temporal split) or ADR-012 (the
weekly horizon), both of which stand.**

**Context**
The Stage 1 feature set was chosen by reasoning about traffic behaviour in
general, not by inspecting this dataset. Exploratory analysis was
therefore performed after modelling rather than before it, which is the
wrong order. This entry records what that analysis found when it was
finally done, on 2026-08-04.

The central finding concerns feature LEGALITY rather than feature quality.
Under the weekly regeneration horizon fixed in ADR-012, a schedule for
Monday to Sunday is compiled in one batch at generation time. A feature is
only usable if it is computable for the FURTHEST hour in that week, which
is 168 hours after generation:

    lag_1           needs data 1h before the target      ILLEGAL
    lag_24          needs data 24h before the target     ILLEGAL
    roll_mean_24    needs the 24h immediately before     ILLEGAL
    lag_168         needs data 168h before the target    LEGAL (just)
    lag_336         needs data 336h before the target    LEGAL
    roll_168_lag168 168h mean ending 168h before target  LEGAL

roll_mean_24 is therefore unusable in deployment. It was the highest
ranked feature in the Stage 2 permutation importance, at 0.6029. The Stage
2 model's strongest predictor cannot exist when the model is actually run.

**Decision**
Replace roll_mean_24 with lag_336 and roll_168_lag168. FEATURE_COLUMNS
goes from 13 entries to 14:

    hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos,
    is_weekend, road_East, road_North, road_South, road_West,
    lag_168, lag_336, roll_168_lag168

roll_168_lag168 is defined as s.shift(168).rolling(168).mean() per road:
the mean of the 168 hours ending 168 hours before the target hour.

**Alternatives rejected**
Keeping roll_mean_24 and accepting that the model cannot be deployed.
Rejected outright.
Replacing roll_mean_24 with lag_168 alone. Measured on validation and
rejected: overall MAE rises from 3.51 (illegal set) to 6.65, which is
worse than the seasonal naive baseline at 4.55. Removing the level
information entirely is not survivable.
Replacing it with a 24-hour mean shifted by 168 (roll_24_lag168) rather
than a 168-hour one. Measured at 5.67, worse than the 168-hour window at
4.54. The wider window carries the demand level more stably.
Dropping to calendar features only, which is the configuration an annual
horizon would require. Measured at 15.10 overall, with North predicted at
32.07 MAE. This is the measurement that also underpins ADR-012.

**Consequences**
Validation results, fit on rows before 2016-11-01 and validated on
November and December 2016. The test set was NOT touched. MAE, lower is
better:

    Feature set                            North  South  East  West  EXNS
    Current (illegal): lag168+roll_mean_24  4.91   1.97  3.64  2.31  3.51
    lag_168 only                            9.46   4.84  5.65  2.36  6.65
    lag_168 + roll_24_lag168                8.40   3.84  4.78  2.34  5.67
    lag_168 + roll_168_lag168               6.07   2.94  4.62  2.34  4.54
    lag_168 + lag_336 + roll_168_lag168     5.97   2.51  4.28  2.35  4.25
    Calendar only                          32.07   5.44  7.79  2.33 15.10
    Seasonal naive baseline (lag_168)       6.06   2.51  5.07  3.14  4.55

The chosen set costs accuracy against the illegal set, 4.25 against 3.51,
which is the honest price of a deployable model. It nonetheless beats the
seasonal naive baseline overall (4.25 against 4.55), wins clearly on East
and West, and ties on North and South. It does not lose on any road.

This materially changes the picture recorded in ADR-014, which found the
model losing to the baseline on North and South. That result was measured
on the illegal feature set. It has NOT yet been re-measured on the test
set with the corrected features, and must not be quoted as though it had.
The validation figures above are validation figures.

lag_168 is confirmed as the correct lag among the legal options, by
detrended autocorrelation (672-hour centred rolling mean removed, because
the raw series is inflated at every lag by the demand trend):

    Road    lag 24   lag 168   lag 336
    North     0.71      0.90      0.89
    South     0.59      0.77      0.76
    East      0.49      0.33      0.29
    West      0.36      0.36      0.38

North and South are strongly weekly-periodic. East is more strongly
DAILY-periodic (0.49 at lag 24 against 0.33 at lag 168), but lag_24 is
illegal, so lag_168 remains the best available choice rather than the best
possible one. This limitation should be stated in the evaluation: East is
the road the feature set serves least well, and it is also the road where
the model most clearly beats the baseline, because its spikiness is where
averaging helps.

Stage 2 must be re-run in full after this change. The existing
models/model_card.json and its reported metrics are superseded.

**Sources**
None, measurement record. All figures produced on 2026-08-04 by
explore_features.py and by validation sweeps over the same data, split and
model configuration.

---

## ADR-016: month_sin, month_cos and is_weekend retained

**Date:** 2026-08-04
**Status:** Accepted

**Context**
The Stage 2 permutation importance scored month_sin, month_cos and
is_weekend at or below zero, which suggested they contributed nothing and
could be removed. An ablation was run to test that.

**Decision**
Keep all three. FEATURE_COLUMNS retains month_sin, month_cos and
is_weekend.

**Alternatives rejected**
Dropping month_sin, month_cos and is_weekend, reducing the feature set
from 14 to 11. Rejected on measurement.

**Consequences**
A first ablation at 100 trees showed every variant landing between 4.25
and 4.29 validation MAE, which appeared to show the calendar features
contributed nothing. That reading was wrong: the differences were smaller
than the seed-to-seed noise, which had not been measured.

Re-run at 300 trees across five random seeds:

    Full, 14 features                  MAE 4.2021, sd 0.0119
    Without month and is_weekend, 11   MAE 4.2627, sd 0.0086

The gap of 0.06 is roughly five standard deviations, so it is real.
Dropping the three features makes the model worse.

The lesson is worth recording separately from the result: a difference
cannot be called negligible until the noise floor is known. The first
ablation would have removed three useful features on the strength of
noise.

Two further points follow. First, permutation importance at or below zero
does not mean a feature is useless; it means the feature's contribution is
smaller than the noise in that particular measurement. Second, is_weekend
justifies itself differently on different roads. On North the weekend is
simply quieter (49.77 against 33.20 mean vehicles per hour). On East the
means are nearly identical (13.79 against 13.44) but the SHAPES differ
sharply: the weekend runs lower through the day, then spikes to 26.5 at
hour 20 against a weekday 18.8. A comparison of means alone hides that
entirely, which is why the feature earns its place on East too.

**Sources**
None, measurement record. Ablation and seed-variance figures produced on
2026-08-04.

---

## ADR-017: Exploratory analysis figures retained as report artefacts

**Date:** 2026-08-04
**Status:** Accepted

**Context**
The exploratory analysis behind ADR-015 and ADR-016 produced eight
figures. They are evidence for feature choices that the methodology
chapter must justify, so they need to be reproducible rather than
one-off screenshots.

**Decision**
explore_features.py is committed and produces all eight figures
deterministically from data/traffic_final_cleaned.csv. figures/ is
committed. export_features.py writes data/feature_table.csv, the full
Stage 1 feature frame with a split column, so individual rows can be
inspected by eye.

**Alternatives rejected**
Generating the figures once by hand and keeping only the images. Rejected
because a figure whose generating code is lost cannot be corrected or
regenerated when the feature set changes, and the feature set is changing
in ADR-015.
Gitignoring figures/ and regenerating on demand. Rejected because the
figures are report deliverables, they total under 1.5 MB, and a reader of
the repository should be able to see what the report cites.

**Consequences**
The figures must be regenerated after the ADR-015 feature change, because
Figure 8 currently labels roll_mean_24 as "currently in data_prep.py",
which will no longer be true.

Two defects were found in these figures during review and are recorded
because they illustrate what automated checking cannot catch:

Figure 8 originally drew roll_168_lag168 as a 24-hour window when it is a
168-hour window. All ten items of the automated review passed regardless,
because a schematic diagram has no numbers to verify against. Only a human
read found it.

Figure 4 originally shaded the whole plot area from 2015-11 to 2017-01 and
labelled it "West synthetic period". Only West is synthetic in that
period. The shading placed North's genuine rise from 20 to 58 vehicles per
hour inside a band captioned as synthetic, which worked against the
honest disclosure required by ADR-002. It was replaced by drawing West's
synthetic months as a dashed line, which cannot be misread because it only
touches West.

A caption on Figure 2 also stated that all four roads peak in the evening.
West peaks at midday (hour 12), which the script's own printed output
showed. Corrected.

The general lesson: numeric review items can audit data plots but cannot
audit explanatory diagrams or caption text. Both need a human read, and
both are exactly where errors survived here.

A fourth independent signature of the synthetic West segment emerged from
Figure 6, alongside the three already recorded in ADR-002. West's scatter
of lag_168 against actual vehicles forms a near-uniform filled rectangle,
where North forms a tight diagonal cloud. Real traffic does not fill a box
evenly. This is the most visually obvious of the four signatures.

**Sources**
None, design decision.

---

## ADR-018: ADR-015 applied, and the train-side figures it changed

**Date:** 2026-08-11
**Status:** Accepted
**Records the application of ADR-015 and amends measured figures in
ADR-008, ADR-009, ADR-010 and ADR-013. Does not change any decision.
The test-side figures in ADR-011 and ADR-014 are unaffected and stand.**

**Context**
ADR-015 decided the feature set correction. It did not apply it.
data_prep.py was corrected on 2026-08-11 and committed as ac695a1.

Applying it changed more than the feature list. lag_336 needs 336 hours of
history where lag_168 needed 168, so the warm-up drop doubled. Every
figure previously measured on the train side of the split was computed on
168 more rows per road than the script now produces. Those figures are
recorded across four earlier entries. Leaving them unamended would mean a
reader running data_prep.py sees six numbers that do not match the log.

**Decision**
Record the application, and amend the affected figures here rather than
editing the earlier entries, which are append only.

Pipeline figures, before and after:

    Rows dropped for NaN            672        1344
    Final row count               57696       57024
    Final rows per road           14424       14256
    First retained hour      2015-11-08  2015-11-15
    Train rows                    40320       39648
    Test rows                     17376       17376

Amended figures, by the entry that recorded them:

    ADR-008, train mean Vehicles
        North   36.92 -> 37.20
        South   11.31 -> 11.36
        East    12.17 -> 12.29
        West     7.27 -> 7.27 (unchanged)
    ADR-009, West Outlier_Flag total       131 -> 128
    ADR-009, agreement rate, South        94.2 -> 94.1
    ADR-009, agreement rate, West         99.4 -> 99.5
    ADR-009, rows with no trailing window 2016 -> 1344
    ADR-010, outlier filter removes from train
                                    347 (0.86%) -> 344 (0.87%)
    ADR-013, training set size           40320 -> 39648

Only West's Outlier_Flag total moved among the four roads. The 168 rows
dropped per road cover 2015-11-08 to 2015-11-14, and only West had flagged
hours in that week.

The trailing-window figure follows arithmetically: the fence needs 672
hours of warm-up per road, so 2688 rows never have one, and the drop now
removes 1344 of them instead of 672.

**Alternatives rejected**
Editing the six figures in place in ADR-008, ADR-009, ADR-010 and ADR-013.
Rejected because the log is append only, and because the earlier figures
are correct records of what the earlier code produced. The change is in
the code, not in the measurement.
Leaving the figures unamended on the grounds that none of them alters a
decision. Rejected because a reader running the script and finding six
mismatches has no way to tell whether they are stale records or a broken
pipeline.

**Consequences**
Verification. The corrected features were checked by a leakage test rather
than by inspection: the three lag features were recomputed for North at
2016-06-01 08:00 from a series blanked after 2016-05-25 08:00, and all
three returned values identical to a hand computation from the raw CSV
(lag_168 31, lag_336 25, roll_168_lag168 35.636905, being the mean of the
168 hours from 2016-05-18 09:00 to 2016-05-25 08:00). Because shift is
positional, any feature reaching into the blanked region would have
returned NaN. This is the check that makes the legality claim in ADR-015
testable rather than asserted.

Test comparability. The test set is unchanged at 17376 rows, 4344 per
road, because the warm-up drop falls entirely inside the training period.
Test metrics measured after the Stage 2 re-run are therefore directly
comparable to those in ADR-011 and ADR-014.

The ADR-015 validation table was produced by standalone sweeps rather than
by data_prep.py, so whether those sweeps applied the same 336-hour warm-up
drop is not recorded. The comparison between feature sets in that table is
internally consistent, but its absolute MAE values should not be assumed
to match what the corrected pipeline will produce. Treat the ordering as
the finding, not the numbers.

Three code debts introduced or exposed by this change, none affecting
correctness today:
The __main__ block recomputes lag_168, lag_336 and roll_168_lag168 outside
load_and_engineer in order to print the per-column NaN counts. The two
copies agree now. If load_and_engineer changes and that block does not,
the printed audit will report on logic the pipeline no longer uses, which
is worse than no audit because it looks like evidence.
LAG_HOURS, LAG_HOURS_336 and ROLL_WINDOW_168 are constants, but the column
names lag_168, lag_336 and roll_168_lag168 are hardcoded strings. Changing
a constant would make a column name false without raising anything.
Pre-existing, unrelated to this change: in the outlier_trailing
computation, .fillna(False) never fires, because comparing a value against
NaN already returns False. The stated intent is achieved by comparison
semantics rather than by that line.

Figure 8 in figures/ is now factually wrong. It labels the 24-hour rolling
mean as currently being in data_prep.py, which ac695a1 made false. ADR-017
anticipated this. The figures must be regenerated before the report cites
them.

One traceability loss. The correction task required that the superseded
feature name appear nowhere in data_prep.py, which was intended to catch
leftover code but also removed it from the explanatory docstring. The file
now describes the illegal feature without naming it, so a reader must come
to ADR-015 to learn what was replaced. Accepted rather than reverted,
because the docstring already cites ADR-015 by number.

Stage 2 must be re-run in full. models/model_card.json and every metric in
it are superseded, as ADR-015 already stated.

**Sources**
None, measurement record. All figures produced on 2026-08-11 by
data_prep.py at commit ac695a1.

---

## ADR-019: explore_features.py stays independent; feature_table.csv is sampled

**Date:** 2026-08-14
**Status:** Accepted
**Two decisions taken together because both concern how the exploratory
artefacts relate to the pipeline. Neither changes the model or the feature
set. ADR-013 established the reproducible-artefact reasoning this entry
applies to a second file.**

**Context**

Both questions became live once ADR-015 was applied.

First, explore_features.py computes lag_168, lag_336 and roll_168_lag168
itself rather than importing them from data_prep.py. Its docstring gave the
reason: it needed to examine candidate features data_prep.py had not adopted
yet. That reason expired when ADR-015 adopted them. The obvious move is to
delete the duplicate and import, and this is the same class of duplication
removed from data_prep.py's __main__ block in the preceding commit.

Second, data/feature_table.csv is tracked at roughly 11.5 MB. It is fully
reproducible from committed code and committed input data. Every feature set
change writes another full copy into git history permanently. This is the
argument ADR-013 used to gitignore models/count_model.joblib, and the same
argument that forced a git commit --amend when that file was committed by
accident.

**Decision**

Keep explore_features.py independent. Replace the expired rationale in its
docstring with the correct one: the figures are evidence, and evidence that
shares a code path with the thing it evidences cannot contradict it. If both
files imported the same shift, a wrong shift would draw a normal-looking
Figure 6 and the figure would confirm the bug rather than expose it. Add a
cross-check that prints both files' values for the same row and requires
agreement, so drift is caught the moment it appears.

Gitignore the full data/feature_table.csv. Commit a sample in its place, at
data/feature_table_sample.csv.

Sample window, measured on 2026-08-14:

    2016-12-19 00:00 to 2017-01-08 23:00
    504 hours per road, 2016 rows total, 23 columns
    train 1248 rows, test 768 rows
    approximately 450 KB

Three contiguous weeks is a minimum, not a round number. lag_336 reaches back
336 hours, so a row in the final week can only be checked against a row that
is present if the sample carries at least 336 hours before it. Two weeks
would make lag_336 uncheckable inside the sample, which defeats the point of
committing one. The window is positioned to straddle 2017-01-01 so both
values of the split column appear.

**Alternatives rejected**

Importing the features from data_prep.py. Rejected on the reasoning above.
Accepted cost: two implementations that can drift. The cross-check is what
makes that cost tolerable rather than what removes it.

Tracking the full feature_table.csv. Rejected on repository growth. Unlike
the model artefact, its size is not prohibitive, but the reproducibility
argument is identical and nothing is lost that a sample does not provide.

Gitignoring it with no sample at all. Rejected. A marker who wants to check
the feature engineering by eye should not have to clone the repository,
build the environment and run a script to do it.

**Consequences**

The independence is now a stated design property rather than an accident of
history, so a future contributor who sees the duplication has the reason in
front of them.

The sample cannot verify outlier_trailing. That flag uses a 672-hour
trailing window (ADR-009), so no row inside a 504-hour sample has a complete
window within the sample. The three lag features are verifiable; the outlier
flag is not. This limitation must be stated in the sample file's header
comment or in the README, not left for a reader to discover.

The full file must be removed from tracking with git rm --cached rather than
deleted, and it stays on disk for local inspection.

Existing history still contains the pre-ADR-015 copy of the full file at
57696 rows. That copy is stale and describes the illegal feature set. It is
not rewritten, because rewriting published history is worse than carrying a
superseded artefact, but it should not be cited.

**Sources**
None, design decision. Sample window figures measured on
data/traffic_final_cleaned.csv on 2026-08-14.

---

## ADR-020: Model family and hyperparameters selected by measurement

**Date:** 2026-08-16
**Status:** Accepted
**Supersedes the qualitative justification in ADR-007. Supersedes the test
metrics in ADR-011 and ADR-014.**

**Context**

ADR-007 chose Random Forest on qualitative grounds: it handles cyclical
features, is lighter than an LSTM, and yields feature importances. No
alternative was ever measured against it. That is not a defensible
justification for a dissertation.

After ADR-015 removed the leaking feature, the model lost to a seasonal
naive baseline on test, so the choice needed testing rather than assuming.

**Decision**

model_selection.py compares 39 configurations plus the seasonal naive
baseline. Four model families: Ridge, Decision Tree at depth 10, Random
Forest across n_estimators 100/200/300 and max_depth None/10/20, and
HistGradientBoosting. Plus a hybrid fitting Ridge for the level and a
Random Forest on its residual.

Each ran under three target modes: raw counts, diff (Vehicles minus
lag_168, added back at prediction time) and ratio (Vehicles divided by
roll_168_lag168, multiplied back). These are seasonal differencing and
multiplicative decomposition, both standard.

Selection used validation only, the last 12 weeks of the training period,
per ADR-013. The rule was fixed before results were seen: lowest
OVERALL_excl_West MAE, and where configurations tie within 0.1, prefer the
simpler by fewer trees, then shallower depth, then simpler target mode.

Selected: RandomForestRegressor(n_estimators=100, max_depth=10) on the diff
target, validation MAE 4.073.

**Alternatives rejected**

The hybrid. Best hybrid validation MAE was 4.245 against Random Forest's
4.073. It did not win and is recorded as a measured result, not omitted.
Ridge with the ratio target, worst of all 39 at 8.732. A linear model
extrapolates, so multiplying an extrapolated ratio by a rising level anchor
compounds the error rather than correcting it.
HistGradientBoosting, worse than the naive baseline on validation.
The raw target, which is what the collapse in ADR-015 exposed.
Re-sweeping hyperparameters during the corrected Stage 2 re-run. Rejected
so the only variable between ADR-014 and that run was the feature set.

**Consequences**

Two evaluation protocols were measured on test, and they disagree.

    STATIC, fit once on the whole training period
      overall excl West  6.69 MAE   naive baseline 5.22   LOSES
    ROLLING, refit weekly across the test period
      overall excl West  4.87 MAE   naive baseline 5.22   WINS

The static protocol is not how the system runs. ADR-012 regenerates the
schedule weekly, so the model retrains weekly. Evaluating it as though
trained once on six months measured something the design never does.

The rolling result does NOT beat the baseline on every road:

    East    5.31 vs 6.49   better by 1.18
    West    2.02 vs 2.65   better by 0.63
    South   2.80 vs 2.88   better by 0.08, effectively tied
    North   6.49 vs 6.30   WORSE by 0.19

The overall win is carried almost entirely by East. North, the busiest
road, still loses to copying last week. This must be stated wherever the
overall figure is quoted.

The 0.351 margin is roughly 30x the seed-to-seed variation recorded in
ADR-016 (0.0119 MAE). That is not a formal significance test, since one is
seed variance and the other a protocol comparison, but the margin is not an
artefact of randomness.

Validation could not have found this. Measured drift, South, is 1.31x from
inner-train to validation but 1.88x from train to test. The validation
period simply does not contain the distribution shift that breaks the
static model, which is why ADR-015's validation table showed South healthy
at 2.51 while test returned 6.28. Validation-based selection was performed
correctly and still selected a configuration that fails under the shift.
That is a limitation of the protocol, not an error in applying it.

Test evaluation count, disclosed rather than minimised: four evaluations
informed selection decisions (ADR-014, the corrected re-run, and the two
protocols here), plus one rolling protocol comprising 26 weekly refits on
which no selection was performed.

Full results are committed at results/MODEL_SELECTION.md with the
underlying tables as CSV, indexed in results/RESULTS_LOG.md.

**Sources**

R. J. Hyndman and G. Athanasopoulos, Forecasting: Principles and Practice,
2nd ed. Melbourne: OTexts, 2018, section 6.3, for classical additive and
multiplicative decomposition.
Improved Sales Forecasting using Trend and Seasonality Decomposition with
LightGBM, arXiv:2305.17201, for the inability of tree ensembles to
extrapolate beyond the training range.
J. Lu et al., "Learning under concept drift: A review," IEEE Transactions
on Knowledge and Data Engineering, vol. 31, no. 12, 2018.

---

## ADR-021: Cycle length 120 s, minimum green 12 s, proportional allocation

**Date:** 2026-08-16
**Status:** Accepted
**Extends ADR-006, which is not modified. Records ADR-006's change of
status from active to structurally unreachable.**

**Context**

generate_timeline.py compiled a hardcoded three hour plan. The model had
never driven the schedule, so the project's central claim was unevidenced.
Connecting them required deciding how predicted vehicle counts become green
seconds, which needed a cycle length, a minimum green and an allocation
rule. None of these had been decided.

**Decision**

    CYCLE_SECONDS      = 120
    AMBER_SECONDS      = 3, unchanged
    MIN_GREEN_SECONDS  = 12
    available green    = 120 - 4x3          = 108
    committed          = 4x12               = 48
    discretionary      = 108 - 48           = 60
    implied maximum    = 12 + 60            = 72

Each road receives 12 seconds plus its share of the 60 discretionary
seconds, where share is that road's predicted count over the four road
total. Rounding uses largest remainder: floor every share, then distribute
leftover seconds to the roads with the largest fractional parts.

**Alternatives rejected**

Deriving cycle length from Webster's formula. Measured on this dataset,
Y is 0.042 in a mean hour and 0.221 in the busiest, giving an optimal cycle
of 24 to 30 seconds. Both sit below the practical minimum used in real
installations. Rejected because the junction runs at a degree of saturation
of 0.05 to 0.25, where a signalised junction is normally designed around
0.85 to 0.95. There is almost no demand to optimise against, so the formula
returns a degenerate answer. Cycle length is therefore a documented design
assumption, not a derived value, and this must be stated in the
methodology.
A 60 second cycle. Four roads at a 12 second floor consume the entire 48
seconds of available green, leaving zero discretionary time. Every road
would receive 12 seconds regardless of prediction and the model would have
no influence at all. This is a hard lower bound the design cannot approach.
A 90 second cycle, 30 discretionary seconds. Rejected because the resulting
allocation compresses a 6.3 to 1 traffic ratio into 1.9 to 1, making the
model's output barely visible in the schedule.
A 7 second minimum green, the UK regulatory figure. Already rejected in
ADR-006 for the same reason it is rejected here: a 7 second phase is legal
but clears almost nothing.
Naive rounding. Measured across all 14592 hours in the dataset, round()
fails to sum to 108 in 33.7 percent of cases, which would leave a third of
cycles at 107 or 109 seconds and the hour would not close.

**Consequences**

3600 divided by 120 is exactly 30, so no hour can end mid phase. ADR-006's
boundary rule cannot fire. Verified: trigger count across 168 hours is 0.

A fencepost bug was found during that verification. The phase level
lookahead asks whether the next phase fits in the remaining budget, which
is trivially false at the last phase of any hour regardless of whether the
cycle divides evenly. It fired 168 times, once per hour, while producing
numerically identical output. Fixed by emitting whole cycles directly when
the cycle divides the hour exactly. ADR-006's path remains in the code and
is reachable only when it does not. The rule is therefore structurally
unreachable under this cycle length, not merely arithmetically dormant.
It is retained rather than removed because it becomes live again
immediately if the cycle changes to a value that does not divide 3600.

ADR-006's 12 second derivation remains load bearing. It is now the per
phase minimum green rather than only a boundary threshold. Its five vehicle
basis, previously justified by matching ANOMALY_QUEUE_MIN in
traffic_sim.py, is now supported externally: at 30 cycles per hour, North's
busiest hour in the dataset delivers 5.2 vehicles per cycle, requiring
2.0 + 5.2 x 1.9 = 11.9 seconds. The constant and the data agree.

Observed output over the 168 hour horizon:

    North  41 to 52 s      South  20 to 28 s
    East   17 to 26 s      West   15 to 22 s
    equal split baseline   27 s per road

The model gives the dominant approach roughly 1.7 times an equal share.
Hour to hour variation is modest, standard deviation 2.24 seconds for
North, because the proportional split between approaches is stable across
the day even though absolute volumes vary by a factor of six. North's mean
share ranges only from 53.9 percent at hour 17 to 57.4 percent at hour 10.
The model's contribution is in setting correct proportions, not in varying
them hourly, and the writeup must claim that rather than dynamic
responsiveness.

Worst case waiting time is 105 seconds, for a road on the 12 second floor.
This bound is reached in the dataset. It is the price paid for the 120
second cycle, which was chosen so the model has room to act.

44 percent of available green is committed to the floor before the model is
consulted. With four approaches and a 12 second minimum this is
unavoidable at any cycle length in the practical range.

The fixed cycle does not shorten at low demand, so a 3am hour with 13
vehicles and a 9am hour with 350 receive similar green times. A demand
responsive cycle length would address this and is recorded as future work.

**Sources**

Transportation Research Board, Highway Capacity Manual, 6th ed.
Washington, DC, 2016. Saturation headway 1.9 s per vehicle, yielding an
ideal saturation flow of 1900 veh/h of green; start up lost time default
2.0 s.
F. V. Webster, Traffic Signal Settings, Road Research Technical Paper
No. 39. London: HMSO, 1958, for the optimal cycle formula.
McTrans, University of Florida, Calibrating Driver Behavior at Signalized
Intersections, noting a University of Central Florida study proposing 3.5 s
start up lost time instead of the HCM default. A higher value would raise
the floor above 12 s, so 12 s is the conservative end of the range.

---

## ADR-022: Night time flashing amber operation considered and rejected

**Date:** 2026-08-16
**Status:** Accepted

**Context**

Many junctions in Sri Lanka switch to flashing amber on all approaches
overnight, allowing vehicles to cross at their own judgement. This is
established local practice and was considered as a night time efficiency
measure for the scheduler, since the fixed 120 second cycle in ADR-021
continues to run regardless of demand.

**Decision**

Not implemented. The schedule runs the same cycle structure for all 24
hours.

**Alternatives rejected**

Flashing amber on all four approaches between 22:00 and 04:00, as practised
locally.
Flashing amber on the dominant approach with flashing red on the other
three, the arrangement defined by MUTCD as an Intersection Control Beacon.

**Consequences**

Rejected on three measured grounds.

First, this dataset has no night lull. Mean total across all four
approaches is 74.9 vehicles per hour between 22:00 and 04:00 against 86.0
between 06:00 and 20:00, only 13 percent lower. The genuine minimum is
43.0 at 05:00, not the middle of the night, and midnight at 82.5 is busier
than 08:00 at 57.8. A 22:00 to 04:00 flashing window would remove control
during hours nearly as busy as the daytime.

Second, the safety evidence runs against the practice. FHWA reports an
estimated 78 percent reduction in right angle collisions and an estimated
32 percent reduction in all collisions from removing late night flash mode.
The practice is being withdrawn rather than extended.

Third, a consistency problem. If low volume justified reduced control at
night, the same argument would apply throughout. The busiest single hour
across all four approaches is 353 vehicles, and signal warrants are
normally based on volumes sustained across at least eight hours of an
average day. At these volumes a signalised junction may not be warranted at
any hour. This limitation belongs in the methodology whether or not
flashing operation is implemented.

The rejection is specific to this dataset and to the evidence cited. It is
not a claim that the local practice is wrong in general, and no
jurisdictional standard governing Sri Lankan installations was consulted.
MUTCD is a United States standard cited only for its definition of the
arrangements considered, and its prohibition on flashing amber facing
conflicting approaches has no force here. Establishing what Sri Lankan
practice permits, and whether the FHWA crash reduction transfers to local
conditions, would require sources not available for this project and is
recorded as a limitation rather than settled.

**Sources**

Manual on Uniform Traffic Control Devices, Chapter 4L, Flashing Beacons,
Federal Highway Administration.
https://mutcd.fhwa.dot.gov/HTM/2009/part4/part4l.htm
Missouri DOT Engineering Policy Guide 902.7, citing FHWA, Signalized
Intersections: Informational Guide, FHWA-HRT-04-091.
https://epg.modot.org/index.php?title=902.7_Flashing_Operation_of_Traffic_Control_Signals_(MUTCD_Chapter_4G)
MUTCD 11th Edition, Part 4, Chapter 4C signal warrants.
https://mutcd.fhwa.dot.gov/pdfs/11th_Edition/part4.pdf

## ADR-023: Security architecture — AES-256-GCM/bcrypt role separation, in-process channel, two-attack scope

**Date:** 2026-08-18
**Status:** Accepted

**Context**

The dissertation requires a demonstrable security architecture, not only a
described one. Before the sensor channel could be protected, three
questions needed settling: what protects a sensor reading in transit, how
is the human operator authenticated for manual override, and what is
realistically buildable on one laptop before 23 August. A further
complication emerged on inspection of traffic_sim.py: SensorSystem
(Section 12) reads vehicle state directly out of `self.vehicles` every
frame. There was no discrete sensor-reading message anywhere in the
codebase for a channel abstraction to sit in front of — one had to be
introduced, not merely wrapped around something existing.

**Decision**

AES-256-GCM (AEAD) protects a periodic per-arm sensor reading in transit
and sensor_log.csv at rest. bcrypt authenticates the human operator for
manual override. These solve different problems and are not
interchangeable: bcrypt is a deliberately slow password hash with no
freshness guarantee, unsuitable for authenticating a stream of readings
without making replay trivial; AES-GCM gives confidentiality and a
tamper-evident tag in one operation, which is what makes "tampering fails
to decrypt" demonstrable rather than only logged after the fact.

The introduced sensor-reading message is emitted at the existing
LOG_INTERVAL_S cadence (10 simulated seconds), inside `_maybe_log()`, not
per frame — this keeps AES-GCM overhead off the 60fps physics loop and
matches the granularity a real IoT sensor network would report at. The
reading carries the discharged-vehicle count for the window, since that
is the value an attacker would most plausibly want to fake, and is kept
strictly separate from SensorSystem's own internal counters: an attack can
corrupt what the dashboard displays to a human operator, but the project's
pre-planned-signal invariant already means nothing downstream of a sensor
reading is permitted to alter simulation behaviour, so this is the correct
attack surface, not a limitation.

The channel is modelled in-process (Option B in earlier design notes):
channel.py passes bytes through interceptor callables rather than opening
real UDP sockets. Scapy and packet-level attacks are out of scope; the
ethics form is being corrected to remove any claim of real network
traffic being generated or intercepted.

Exactly two attacks are implemented — false data injection and sensor
spoofing — both as channel interceptors. DoS/DDoS was considered and
dropped: a credible denial-of-service demonstration needs network
infrastructure and load generation not available for this project.
Confirmed absent from the codebase via
`Select-String -Path *.py -Pattern "dos|ddos|flood|denial"`.

Encryption is a runtime toggle (`channel.encryption_enabled`) bound to a
dashboard key, not a deletable code path, so the same attack demonstrably
succeeds and fails with one keypress and no restart.

**Alternatives rejected**

Wiring the channel into SensorSystem's per-frame vehicle reads directly.
Rejected: AES-GCM at 60fps across four arms adds cost with no
demonstrable benefit, and conflates "what physically happens in the
simulation" with "what a compromised sensor reports," which are supposed
to be separable.

Building a pygame login UI for auth.py tonight. Rejected on time budget —
auth.py exists and is unit tested (security/test_security.py, 12/12
passing) but is not wired into the dashboard. Recorded as unintegrated
rather than silently dropped.

**Consequences**

Positive: the encryption-off/on contrast is empirically verifiable, not
just asserted — security/test_security.py asserts both outcomes for both
attacks and passes. ISO 27001 controls A.8.24 (crypto), A.5.17/A.8.5
(authentication) are demonstrated by running code.

Negative: the in-process channel means attacker and defender share a
process and trust boundary. Sufficient to demonstrate the cryptographic
properties correctly, not evidence against a network-positioned
adversary — state this limitation explicitly rather than let the demo
imply more.

A stale comment at traffic_sim.py lines 228–229 ("real annual output"),
contradicting ADR-012's weekly regeneration horizon, was corrected as part
of this pass. This is a one-line comment fix, not a decision with a
rejected alternative, and is not itself the subject of this entry.

auth.py's dashboard integration, Isolation Forest anomaly detection
(distinct from the rule-based detector already in SensorSystem, Section
12), encrypted-storage wiring into the live sensor log, and the
HOURLY_DEMAND/ANOMALY_RATE_MIN correction remain open, per the existing
19–20 Aug plan.

**Sources**

None, design decision. Test results (12/12 passing) produced 2026-08-18
by security/test_security.py.

## ADR-024: HOURLY_DEMAND correction and ANOMALY_RATE_MIN recalibration

**Date:** 2026-08-18
**Status:** Accepted

**Context**

HOURLY_DEMAND (traffic_sim.py, Section 5) was fabricated, not derived from
data/traffic_final_cleaned.csv despite that file being the project's stated
source of demand. Summed across all four arms, the fabricated table's hour-8
values (North 780, South 700, East 520, West 540 — read directly from the
pre-correction array) total 2540 veh/h; the corrected, dataset-derived
values at the same hour (North 33, South 11, East 9, West 5) total 58 veh/h
— roughly 44x inflated. ANOMALY_RATE_MIN=0.22, and every queue length ever
observed in the simulation up to that point, had been tuned and interpreted
against that fiction.

**Decision**

Replace HOURLY_DEMAND with the mean Vehicles per (Road, hour-of-day) across
the full data/traffic_final_cleaned.csv, rounded to the nearest vehicle.
Verified independently before use (grouped by road+hour, mean, round): exact
match to the corrected table, zero discrepancy. Recalibrate ANOMALY_RATE_MIN
from 0.22 to 0.15, using the empirical CDF of a measured accident-blockage
rate distribution (60 sim-min headless, peak hour, real accident on North;
n=31885 qualifying frames; min=0.0, p10=0.0, median=0.0999, p90=0.15,
max=0.2198) — 0.15 flags 88.7% of blockage frames while staying under the
observed maximum.

**Alternatives rejected**

Recalibrating ANOMALY_QUEUE_MIN alongside ANOMALY_RATE_MIN, to compensate
for the corrected (much lower) demand. Rejected: out of scope for the pass
that produced this correction, and doing so blind — without first confirming
what queue lengths real demand can actually produce — risked tuning a
threshold to "look right" rather than to measurement. Left unchanged, with
its consequence (below) recorded rather than papered over.

Leaving ANOMALY_RATE_MIN at 0.22. Rejected: that value was tuned against the
fabricated demand's inflated queues; the measured real-demand blockage
distribution gives no basis for it, and the "normal, queue-present"
distribution at real demand is degenerate (rate=0.0 for every qualifying
frame observed), so 0.22 could not be justified as sitting between two real,
separable regimes.

**Consequences**

Positive: HOURLY_DEMAND is now traceable to the dataset the project claims
to model, with the verification method recorded so a reader can repeat it.

Negative, structural, not a bug: pinch_capacity_veh_per_h =
(CRAWL_SPEED*60/MIN_GAP)*3600 = 450, against peak_arm_demand_veh_per_h =
max(HOURLY_DEMAND) = 59 (North, hour 19). 450 >> 59: a lane blockage cannot
build a persistent queue at real demand. Measured directly: 90 sim-minutes
headless (normal operation at two hours of day, plus a real hour-long
accident at peak demand) never once reached ANOMALY_QUEUE_MIN=5; max queue
observed including during the accident was 4. The busiest single hour across
all four approaches in the dataset is 353 vehicles, degree of saturation
~0.25 — the junction runs far below saturation by construction of the real
data, not by a simulation defect. This directly motivated the
DEMAND_MULTIPLIER presentation parameter (ADR-026).

**Sources**

Recalibration evidence and the 450/59 veh/h arithmetic: commit fb379fe
("Replace fabricated HOURLY_DEMAND with real dataset means; recalibrate
ANOMALY_RATE_MIN") and commit 3458083 ("Add threat classification (S1-S5)
and a disclosed demo demand multiplier"), Phase 0 evidence section. Old/new
hour-8 per-arm values (780/700/520/540 vs 33/11/9/5) read directly from
commit fb379fe's diff of traffic_sim.py; the 2540/58/~44x sums were computed
this session directly from those repo-read values, not from memory.

## ADR-025: Threat classification S1-S5 and attack magnitude modes

**Date:** 2026-08-18
**Status:** Accepted

**Context**

crypto.py and channel.py (ADR-023) can answer "was this message tampered
with in transit" but not "is what the dashboard is showing right now a
cyberattack or a real traffic incident" — that requires combining the
channel's verdict with SensorSystem's physical state and the pattern of
readings across arms and time. Additionally, the only attack magnitude
available at the time (a fixed, obviously implausible constant) demonstrated
nothing beyond what a bounds check alone could catch, and encryption alone
stops mattering once a key is compromised, with no control left in the
codebase to demonstrate defence in depth against that scenario.

**Decision**

security/detection.py implements five signals as pure functions (no pygame,
no traffic_sim import): S1 INTEGRITY_FAIL (AEAD rejection, only meaningful
when encryption was enabled), S2 IMPLAUSIBLE (reported count exceeds
green_s/1.9s HCM saturation headway — same citation as ADR-006/ADR-021), S3
DIVERGENCE (reported != true, threshold 0), S4 SIMULTANEITY (3+ arms flagged
in one interval), S5 PHYSICAL (the existing, unmodified SensorSystem rule).
Classified in a fixed priority order into NORMAL / PHYSICAL_INCIDENT /
AMBIGUOUS / CYBER_LIKELY / CYBER_CONFIRMED.

security/attacks.py's FalseDataInjectionAttack gained a mode parameter:
"crude" (a fixed, physically impossible constant, default 999) versus
"stealthy" (scales the TRUE value by a small factor, default 1.3, minimum
absolute change of 1). Crude exists to show that naive attacks are caught by
plausibility checks (S2) alone, with no cryptography required — the weaker
demonstration, because a bounds check is a much smaller control to defeat
than a cipher. Stealthy stays inside the plausible envelope and evades S2,
so only S3 (or, with encryption on, S1) catches it — the stronger, more
realistic demonstration of what an attacker who has done their homework
would actually send.

Both attacks gained an optional crypto= constructor argument modelling a
stolen key: the attack decrypts the intercepted message, alters the
payload, and re-encrypts with the same key, producing a message that passes
AEAD verification. This is the scenario that justifies detection existing at
all — cryptography cannot help once the key is compromised, so a
stolen-key stealthy attack (encryption on, S1 does not fire) is caught only
by S3, the defence-in-depth case: press one key, the attack now passes
encryption, and detection catches it anyway.

**Alternatives rejected**

Giving S3 access to a genuinely independent measurement. Rejected for this
project: S3 compares the channel's reported value against `true_vehicles`,
which in this codebase is simulation ground truth — the same Simulation
object that sends the reading also knows exactly how many vehicles it sent.
A real deployment has no such oracle. S3's role would have to be played by a
redundant sensing modality (e.g. an inductive loop cross-checked against a
camera count) or by the Random Forest's forecast residual for that
arm/hour. Building either was out of scope; the simplification is stated
explicitly in security/detection.py's module docstring rather than left
implicit, so the signal is not presented as more realistic than it is.

A non-zero DIVERGENCE_THRESHOLD_VEHICLES, to model measurement noise.
Rejected: reported and true_vehicles are computed from the exact same
integer count in the same function call and transmitted synchronously, so
there is no independent noise source between them in this architecture — a
non-zero tolerance would be inventing slack that has no source in the
design.

**Consequences**

Positive: the crude/stealthy contrast and the key-compromise variant are
both empirically verified, not just argued: a stolen-key stealthy attack
correctly does not trip S1 (accepted=True) and is still caught by
S3_DIVERGENCE (e.g. South: reported=6 <= physical bound=10.54, evading S2,
while diverging from true=5).

Negative, measured and disclosed rather than tuned away: S2 produced false
positives on genuine, unattacked traffic, because its physical bound
(green_s/1.9) does not hold over short slivers of the 20s rolling discharge
window. Before the S2_MIN_GREEN_S guard (commit 0e869d0): 0.14-0.28% at
demand 1x, 9/120 (7.50%) at demand 10x. After the guard (mirroring the
existing ANOMALY_MIN_GREEN_S=6.0 precedent rather than inventing a new
number): 0/120 (0.00%) at 1x, 3/120 (2.50%) at 10x. The residual 2.50% is a
different, genuine phenomenon (real bursts of discharge exceeding the
idealised continuous-flow HCM bound, e.g. reported=true=7 against a bound of
5.22 at green_s=9.92), not the sub-guard artefact the fix targeted, and was
reported rather than suppressed.

**Sources**

Signal design, magnitude modes, key-compromise variant: commit 3458083
("Add threat classification (S1-S5) and a disclosed demo demand
multiplier"). S2_MIN_GREEN_S guard and before/after false positive rates:
commit 0e869d0 ("Add S2_MIN_GREEN_S guard to suppress a real S2
false-positive source"). S3 ground-truth simplification: security/
detection.py module docstring, "CRITICAL HONESTY REQUIREMENT" section,
introduced in commit 3458083.

## ADR-026: Demand multiplier and density levels as disclosed presentation parameters, and time speed as the honest alternative

**Date:** 2026-08-18
**Status:** Accepted

**Context**

ADR-024 established that real demand cannot produce a persistent queue:
pinch_capacity_veh_per_h (450) vastly exceeds peak_arm_demand_veh_per_h
(59), so SensorSystem's physical rule (S5) is structurally unreachable at
real demand, confirmed empirically (a real hour-long accident at peak
demand never once reached ANOMALY_QUEUE_MIN=5). Demonstrating
PHYSICAL_INCIDENT classification therefore requires either fabricating
demand or an impractical amount of wall-clock waiting for a rare event that
may not arrive at all within a fixed viewing session.

**Decision**

DEMAND_MULTIPLIER (default 1.0) scales `_spawn_probability` only, and is
documented as a presentation parameter whose value must never be used to
produce a reported result. DEMAND_LEVELS = (1.0, 10.0, 25.0, 50.0) gives
discrete selectable levels via dashboard buttons and a cycling key. The
choice of levels is grounded in a measured per-arm saturation table
(signal_timeline.csv, at each arm's own peak hour, not assumed;
capacity_veh_per_h = mean_green_s * cycles_per_hour / 1.9s): North (peak hr
19) capacity 733.1 veh/h against demand 59 -> 12.43x; South (hr 19) 381.2
against 18 -> 21.18x; East (hr 20) 329.3 against 20 -> 16.47x; West (hr 12)
291.0 against 11 -> 26.45x. 10x oversaturates no arm; 25x oversaturates
North, South and East but not West; 50x oversaturates every arm. Above
roughly 12x (North's own multiplier, the lowest of the four and so the
first to break), the junction is oversaturated by construction: S5 fires
from raw capacity limits rather than any simulated incident, and S2 fires
more often because genuine bursts routinely exceed the idealised HCM bound.
10x is therefore recorded as the highest DEMAND_LEVELS entry at which the
detectors remain meaningful; 25x and 50x exist only to force queue
formation for demonstration, and a result produced at those levels is
evidence of deliberate oversaturation, not of detector quality.

SPEED_LEVELS = (1, 5, 20, 50) scales the simulation clock only, via
sub-stepping in main() rather than a single scaled-dt update — the naive
approach was verified to let vehicles pass through each other at high
speed: at speed=50 with a single dt*speed step, MAX_SPEED*50=110px exceeds
MIN_GAP=48px; empirically, 20559 overlap-violation frame-instances were
observed in a 3600-frame run. Sub-stepping fixes physics at every level
(max single-substep step measured at 2.2px, identical to the 1x baseline,
at every SPEED_LEVELS value; zero overlap and zero overshoot violations at
S=50 after the fix). TIME fabricates nothing — HOURLY_DEMAND, CRAWL_SPEED,
MIN_GAP and every other physical constant are untouched by it — so it is
recorded as the preferred demonstration mechanism over DEMAND_LEVELS: the
honest way to see more traffic in a shorter viewing session. DEMAND_LEVELS
should only be reached for when TIME alone is not enough (forcing a queue
for an S5 demo).

**Alternatives rejected**

Changing CRAWL_SPEED, MIN_GAP, ANOMALY_QUEUE_MIN or HOURLY_DEMAND to make a
queue-forming demo possible without a multiplier. Rejected explicitly and
repeatedly across this work: these are the physical constants and the
verified dataset; tuning them to "make a demo work" would silently
reintroduce the same fabrication ADR-024 removed, this time in the
constants a false positive or a physical incident is measured against,
rather than in the demand feeding them.

A single DEMO x10 toggle (the first implementation). Rejected in favour of
selectable DEMAND_LEVELS once the saturation table above showed 10x alone
does not reach oversaturation on any arm, so a viewer wanting to force
queue formation for a demonstration had no way to do so without editing
code.

Reducing MAX_SUBSTEPS_PER_FRAME's cap, or growing sub-step size, when a
frame needs more sub-steps than the cap allows. Rejected: either would
corrupt physics (larger steps) or drop simulated time (fewer steps than
needed). Excess simulated time carries into the next rendered frame
instead, so only the achieved render framerate is affected, never physics.

**Consequences**

Positive: no reported result in this session used a density multiplier
other than 1.0 — the evidence behind ADR-024 and ADR-025 was gathered at
DEMAND_MULTIPLIER=1.0 throughout, with DEMAND_LEVELS used only for the
demonstration-specific reviews that are themselves about the multiplier's
own effect (e.g. confirming PHYSICAL_INCIDENT classification becomes
reachable at 10x).

Negative, measured and disclosed: at demand=1x, speed=50, achieved
framerate measured at 52.9fps (headless proxy, SDL dummy driver, draw calls
executed but not displayed) — below the 60fps target. Physics correctness
is unaffected (zero violations at S=50, see Decision above); only the
render rate dips, exactly as designed, and this was reported rather than
hidden or fixed by silently reducing sub-steps.

**Sources**

R1 saturation table and R2 blocked-at-entry figures: commit c974506
("Replace DEMO x10 toggle with selectable density levels (1x/10x/25x/50x)").
Sub-stepping bug verification, fix, and Review 2 figures (2a-2i, including
the 52.9fps measurement): commit 9947df6 ("Separate TIME (honest
fast-forward) from DENSITY (fabricated demand)"). Pinch capacity / peak
demand arithmetic: commit fb379fe and commit 3458083 (also cited in
ADR-024).

## ADR-027: Correcting ADR-026's "no reported result used density != 1.0" claim; S2 headway correction

**Date:** 2026-08-18
**Status:** Accepted
**Supersedes a specific claim in ADR-026, not the entry as a whole.**

**Context**

ADR-026's Consequences section states: "no reported result in this session
used a density multiplier other than 1.0 — the evidence behind ADR-024 and
ADR-025 was gathered at DEMAND_MULTIPLIER=1.0 throughout, with DEMAND_LEVELS
used only for the demonstration-specific reviews that are themselves about
the multiplier's own effect." This is contradicted by ADR-025's own
Consequences section, re-read directly before writing this correction:
"Before the S2_MIN_GREEN_S guard (commit 0e869d0): 0.14-0.28% at demand 1x,
9/120 (7.50%) at demand 10x. After the guard … 0/120 (0.00%) at 1x, 3/120
(2.50%) at 10x," and its Positive consequence citing "South: reported=6 <=
physical bound=10.54, evading S2" — both explicitly measured at demand 10x
and reported as findings, not caveated as demonstration-only.

Separately, this session's Phase A verified that S2's bound was itself
miscalibrated: it used the Highway Capacity Manual's 1.9s real-world
saturation headway to judge vehicle counts that this simulation's own
car-following physics produced, not real traffic. Computed this session:
sim_min_time_headway_s = MIN_GAP / (MAX_SPEED * 60) = 48 / (2.2*60) =
0.3636s, against the HCM's 1.9s — a factor of 5.225. The specific recorded
false positive from ADR-025 (green_s=9.917, discharged=7) exceeds the HCM
bound (5.219, flagged) but not the simulation's own bound (27.27, not
flagged): proof S2 was measuring the gap between simulation physics and
real-world physics, not attacker activity.

**Decision**

Two corrections, recorded together because the second changes numbers the
first must be read alongside.

1. ADR-026's "no reported result … used a density multiplier other than
1.0" is corrected to: no result in ADR-024 (the HOURLY_DEMAND/
ANOMALY_RATE_MIN correction) used a density multiplier other than 1.0.
ADR-025's S2 false-positive rates and its crude/stealthy evasion example
were measured at demand 10x, and that was necessary, not incidental: at
demand 1x, HOURLY_DEMAND (ADR-024) is low enough that within a review-sized
observation window (single-digit sim-minutes) genuine discharge counts are
usually zero or one. Both the S2 false-positive rate and the crude/stealthy
evasion example need a representative sample of true_vehicles large enough
to compare meaningfully against a green-time-derived bound — 10x reliably
produces that sample within the review's time budget; 1x does not
reliably produce it at all, not because the phenomenon is absent at 1x but
because it is impractical to observe there in finite review time.
DEMAND_LEVELS exists for exactly this reason (ADR-026's own Decision), so
using it for ADR-025's measurements was the mechanism working as designed,
not a violation of ADR-026's intent.

2. security/detection.py's S2 IMPLAUSIBLE now takes sim_saturation_headway_s
as a required parameter instead of using the HCM figure directly.
traffic_sim.py computes SIM_SATURATION_HEADWAY_S = MIN_GAP / (MAX_SPEED *
60) = 0.3636s and passes it at the Simulation._classify() call sites;
detection.py stays import-free of traffic_sim by design, so the value is
passed in rather than duplicated as a second literal that could silently
drift from MIN_GAP/MAX_SPEED. HCM_SATURATION_HEADWAY_S (1.9s) is retained
as a named constant, documented as the correct bound for a real deployment,
just not what this simulation should be judged against.

Measured before/after (this session, Review A1, headless, peak hour):
false positive rate at demand 1x: 0.00% (unchanged, was already 0.00%
after the Phase 1 guard). At demand 10x: 0.00%, down from the 2.50% (3/120)
recorded in ADR-025. Crude injection (999) at demand 10x still fires S2
(South: reported=999 against a new bound of 55.05). Stealthy injection at
demand 10x still evades S2 (South: reported=6 against the same 55.05 bound
— a wider margin than the 10.54 bound it evaded before), and the wider
bound did not additionally let crude through: 999 remains far beyond 55.05.

**Alternatives rejected**

Editing ADR-025 or ADR-026 directly to remove the contradiction. Rejected:
this project's own established convention (e.g. ADR-018 correcting
ADR-015, ADR-021 amending ADR-006's status) is that ADRs are append-only:
a later entry corrects or supersedes an earlier one's specific claim, it
does not rewrite history. Neither ADR-025 nor ADR-026 was edited by this
entry — confirmed via `git diff DECISIONS.md` showing insertions only.

Re-measuring the S2 false-positive rate and the crude/stealthy example at
demand 1x to make ADR-026's blanket claim literally true. Rejected: as
argued above, 1x cannot reliably exercise the S2 boundary within a
practical review window, so this would trade a real, useful measurement
at 10x for a technically-1x-only but practically uninformative one, and
would not actually fix the underlying imprecision in ADR-026's wording.

Treating ADR-026's cited demand=1x/speed=50 framerate figure (commit
9947df6) as confirmation that speed=50 has a real, repeatable framerate
cost. Rejected: the same commit measured a HIGHER framerate at ten times
the demand (more vehicles, more draw calls, faster result) — physically
backwards if the number reflected a real cost rather than measurement
noise from the headless SDL dummy-driver proxy. That figure is not
restated here; ADR-026 readers should treat it as unreliable and
unrepeated, not as a measured speed-dependent cost.

**Consequences**

Positive: ADR-026's claim now precisely matches what was actually measured
where — a reader checking "was this number measured at real demand" has a
correct answer to check against, rather than a blanket statement one
paragraph away from its own exception. The S2 physics correction removes a
real, measured source of false positives on genuine traffic at demand 10x
(2.50% to 0.00%) without weakening true-positive detection: the crude
attack (999) remains trivially caught under the new, wider bound, by two
orders of magnitude.

Negative: security/detection.py's compute_channel_signals signature grew a
required parameter (sim_saturation_headway_s), which is a breaking change
for any caller not updated alongside it — test_security.py's
`_channel_signals` helper and two of its bound-dependent test assertions
needed updating to the new bound (green=20s bound moved from ~10.5 to
55.0; green=6s bound moved from ~3.16 to 16.5). Any future test written
against the old HCM-derived bound values would now fail silently wrong
rather than loudly — reviewers should check bound arithmetic against
SIM_SATURATION_HEADWAY_S, not 1.9, when reading S2 test assertions from
before this ADR.

**Sources**

Contradicting sentences: DECISIONS.md ADR-025 (Consequences section) and
ADR-026 (Consequences section), re-read directly this session before
writing this entry. Physics verification (0.3636s headway, 5.225 ratio,
the green_s=9.917/discharged=7 bound comparison): computed this session
per Phase A's instructions. Before/after false-positive rates and the
crude/stealthy bound comparison at demand 10x: this session's Review A1,
headless runs against the corrected security/detection.py and
traffic_sim.py. Framerate noise observation: commit 9947df6, both cited
figures read directly from its own commit message, not restated as new
measurement.

## ADR-028: Schedule approval gate — hash-bound operator sign-off before playback

**Date:** 2026-08-18
**Status:** Accepted

**Context**

ADR-005 established that the schedule CSV is the single artefact that
determines signal behaviour, and recorded directly in its own
Consequences section that this "makes it the artefact that must be
authenticated, integrity protected and replay protected before
deployment." auth.py (ADR-023) has existed since that entry, unit tested,
but never wired into the dashboard - a login proves who clicked ACCEPT,
but says nothing about WHAT they accepted, and nothing enforced that the
file on disk at playback time was the same file a human ever looked at.

**Decision**

An approval modal (traffic_sim.py, Section 15) runs in main(), before
Simulation() is constructed and before any simulation loop step: it
displays the schedule file name, the period it covers, its row count,
model provenance read from models/model_card.json, and the first 16 hex
characters of its SHA-256, then takes a username/password pair and calls
OperatorAuth.verify(). On success it appends a security/approval.py
ApprovalRecord (timestamp, username, schedule_path, sha256, decision) as
one JSON line to security/approvals.jsonl and only then starts the
simulation; on failure it shows "authentication failed", clears the
password field, and allows retry with a visible attempt count.

The approval target is APPROVAL_TARGET_PATH = signal_schedule_plan.csv
(hour, road, predicted_count, green_seconds - the model's direct output),
NOT SIGNAL_TIMELINE_PATH (the expanded phase-by-phase CSV
SignalController actually plays back). generate_timeline.py's
compile_timeline() expands the plan into the timeline deterministically -
same plan in, same timeline out, every time. Approving the expansion
would be approving a derived artefact that carries no information the
plan does not already carry; the plan is the authored artefact a human
reviewer can meaningfully assess (it is what the model produced), so it
is what gets hashed and signed off. APPROVAL_TARGET_PATH is written as
the one line to change when the annual plan
(data/signal_schedule_plan_annual.csv) replaces the weekly one.

The hash binding is the point, not a formality: per ADR-005, the schedule
CSV alone determines signal behaviour, so an approval recorded against a
username with no binding to the exact bytes of that file is an approval
of nothing in particular - the file could be regenerated, edited, or
swapped between the click and playback (or between playback and a later
audit) and the record would not know. sha256_file(path) is recomputed and
compared, not trusted from a prior run; verify_still_valid(schedule_path,
record) lets anyone re-check that binding later using only the file on
disk and the log entry, with no other state required.

ISO 27001 mapping (see security/README.md for the fuller table): A.5.17
(authentication information) - the username/password pair, stored and
verified via bcrypt (auth.py, ADR-023), never in this module. A.8.5
(secure authentication) - failed attempts are rejected with no
information disclosure beyond "authentication failed" (see the timing-
safety note in auth.py's verify(), exercised for unknown usernames too -
this session's Review C3), and the password field is cleared on failure
rather than left for a shoulder-surfer to read. A.8.24 (use of
cryptography) - SHA-256 binds the approval decision to the exact bytes
approved, using the same reasoning already applied to sensor readings in
ADR-023, now applied to the schedule file itself.

**Alternatives rejected**

Approving SIGNAL_TIMELINE_PATH (the expanded phase timeline) instead of
the plan. Rejected: it is a deterministic function of the plan, so
hashing it binds approval to the SAME information the plan's hash already
carries, at ~30x the file size for no additional integrity guarantee, and
it invites a reviewer to read tens of thousands of phase rows instead of
the single weekly table the model actually produced.

Signing the approval (e.g. an operator-held private key) rather than
hashing plus an authenticated append-only log. Rejected: signing requires
key generation, distribution, and protection - a PKI this project has no
mechanism to build or manage, and introducing one just for this control
would be security theatre, a cryptographic primitive with no supporting
infrastructure behind it. A SHA-256 hash bound to a username inside an
append-only log, combined with the existing bcrypt-authenticated login
that produced the record, achieves the integrity-binding property this
control needs (was THIS file approved by THIS operator) without
inventing key management the project does not have.

Storing the approval decision as a mutable field on the schedule file
itself (e.g. a header row or sidecar flag). Rejected: a mutable field
sits inside the same trust boundary as the thing being approved - an
attacker (or an innocent re-generation) that can change the schedule can
change the flag alongside it. A separate, append-only log outside that
boundary is what makes "was this approved" a question with an answer
that survives the schedule file being touched.

**Consequences**

Positive: playback is now gated on a specific, hash-verified file and a
specific, authenticated human - not merely on a file with the right name
existing on disk. Constructing Simulation() directly, which every
headless test does, requires no authentication and touches no
credential or approval file (confirmed this session, Review C7:
operators.json's mtime is unchanged across a Simulation() construction).
The gate is confined to main() and the approval modal function it calls,
before Simulation exists; grep confirms zero references to approval,
auth, or sha256 inside SignalController, Vehicle.update, or
Simulation._leaders (Review C8) - approval gates whether playback starts,
never schedule content or phase advancement, preserving this file's
central invariant.

Negative, stated plainly rather than implied away: bcrypt authenticates a
local operator against a local credential file (security/operators.json,
gitignored, created only by security/setup_operator.py). There is no
certificate authority, no session management (the modal re-authenticates
once at launch and nothing checks the operator is still present or still
authorised for the rest of the run), and no revocation mechanism - a
credential, once registered, is valid until someone manually edits
operators.json. Nothing in this design protects security/approvals.jsonl
or operators.json against an attacker who already has filesystem write
access to the machine running the simulation: such an attacker could
append a forged approval record or a new operator credential directly,
bypassing the modal entirely. This is sufficient to demonstrate the
control - authenticated human sign-off bound to a specific artefact,
logged append-only - for a dissertation project running on one laptop. It
is not sufficient for a real deployment, which would need centralised
identity (a CA or equivalent), session-scoped authorisation, credential
revocation, and a tamper-evident log store outside the reach of local
filesystem access - none of which exists here and none of which should
be assumed from this control's presence.

**Sources**

Design decision plus this session's Review C evidence: C1 (correct
credentials append a record with the right username, path and a 64-
character hex hash), C2/C3 (wrong password and unknown username both
rejected, retry offered, attempt count shown, security/approvals.jsonl
byte-identical before and after - confirmed via direct byte comparison),
C3's additional check that auth.py's dummy-hash timing-safety branch is
actually exercised for an unknown username (bcrypt.checkpw call count
verified as 1 via a call-counting patch), C4 (verify_still_valid True
before a one-byte modification to the live signal_schedule_plan.csv,
False after, True again after restoring the original bytes - restoration
confirmed exact via `git diff --stat` showing no change), C5 (operators.
json moved aside: no crash, the modal names the setup command, restored
after), C7 (37/37 tests; Simulation() construction confirmed not to
touch operators.json), C8 (grep, as above). ADR-005 and ADR-023, quoted
directly where cited above.

## ADR-029: Encryption at rest for sensor_log.csv not implemented, superseding that part of ADR-023

**Date:** 2026-08-19
**Status:** Accepted
**Supersedes:** the at-rest half of ADR-023's Decision. ADR-023 otherwise
stands: in-transit AES-256-GCM, bcrypt operator authentication, the
in-process channel, and the two-attack scope are unchanged.

**Context**

ADR-023's Decision section states: "AES-256-GCM (AEAD) protects a periodic
per-arm sensor reading in transit and sensor_log.csv at rest." The first
half of that sentence is true and demonstrable. The second half was never
true of the running system.

A read-only audit on 2026-08-19 against the tree at commit abe3811 traced
the log write path and found:

  - Simulation._maybe_log (traffic_sim.py:1342-1363) appends plain dict
    rows to self.log_rows.
  - Simulation.write_log (traffic_sim.py:1457-1464) writes those rows with
    csv.DictWriter directly. There is no crypto call anywhere in that path.
  - crypto.py provides encrypt_log_line and decrypt_log_line
    (crypto.py:144-155). A grep for both names across every .py file in the
    repository returns call sites only in security/test_security.py:61,63.
    There are zero call sites in traffic_sim.py.
  - The first two lines of sensor_log.csv on disk are a plaintext CSV header
    followed by a plaintext data row beginning
    10.0,08:00,North,0,0,0,0,10.01.

The primitive is correct and tested. test_log_line_round_trip_at_rest passes
as part of the 38-test suite. It is simply not connected to anything.

The gap survived a week and a full Review C pass because every test written
for the at-rest path tested the function, and no test asserted anything
about the bytes that actually reach disk. A unit test on a primitive says
nothing about whether the primitive is called.

**Decision**

Do not implement encryption at rest before submission. Record here that
sensor_log.csv is written as plaintext, that ADR-023's claim to the contrary
was incorrect, and that crypto.py's at-rest functions exist and pass their
unit tests but have no production call site.

Every submitted document, and every section of the dissertation, describes
encryption in this project as protecting sensor readings in transit only.
No document may claim at-rest protection.

The residual risk is stated rather than mitigated. sensor_log.csv contains
simulated vehicle counts and timestamps from a Pygame junction. It holds no
personal data, no credentials, and no real infrastructure telemetry. It is
gitignored and never leaves the development machine. The exposure created by
leaving it plaintext is therefore local disk read access to synthetic data.
This is a real gap against ISO/IEC 27001:2022 A.5.33 and A.8.24, and a small
one in consequence.

**Alternatives rejected**

Wiring encrypt_log_line into write_log. This is roughly twenty lines and the
primitive already works, so the implementation cost is not the objection.
The objection is the read side: every consumer of sensor_log.csv would need
a decrypt path, and an encrypted evidence file that cannot be opened and
read during a viva is worse for demonstrating the project than a plaintext
one. With four days remaining and the dissertation unwritten, the schedule
cost of the change plus its regression surface is not justified by the risk
it removes on synthetic data. Recorded as future work.

Silently amending ADR-023 to delete the at-rest clause. Rejected outright.
DECISIONS.md is append-only. An examiner reading a corrected history learns
nothing; an examiner reading a superseding entry learns that the project
audited itself and found its own false claim.

Leaving ADR-023 unamended and simply not mentioning at-rest encryption in
the dissertation. Rejected: the contradiction would still sit in a committed
artefact submitted as evidence, and would be found by anyone who read the
decision log alongside the repository.

**Consequences**

Positive: the decision log now matches the code. The in-transit claim, which
is the one the attack demonstrations actually depend on, is unaffected and
remains demonstrable by keypress. The discovery method is itself reportable:
a decision was recorded, believed, and left unimplemented for a week, and
was caught only by a read-only audit that grepped for call sites rather than
for definitions. That is a finding about the limits of ADR discipline and it
belongs in the Evaluation chapter, not buried here.

Negative: the project demonstrates one of two standard cryptographic
postures rather than both. Data at rest is unprotected. State this as a
limitation in Evaluation and as future work in the Conclusion, in those
words, before anyone else does.

Process consequence: unit-testing a cryptographic primitive is not evidence
that data is protected. Any future claim of the form "X protects Y" requires
a test that inspects Y, not a test that exercises X. The audit item that
caught this (A4) worked because it read the bytes on disk.

**Sources**

Read-only audit A4, run 2026-08-19 against commit abe3811, tree clean.
Evidence: write-path trace, grep for encrypt_log_line/decrypt_log_line call
sites across all .py files, and the first two lines of the on-disk file.
ISO/IEC 27001:2022 Annex A, A.5.33 Protection of records and A.8.24 Use of
cryptography.

## ADR-030: Single project title fixed across all submitted documents

**Date:** 2026-08-19
**Status:** Accepted

**Context**

Three documents carried three different project titles.

  - Computing Project Proposal Form, signed 17/05/2026: "Secure AI Driven
    Smart Traffic Control System with Integrated Cybersecurity and
    Governance."
  - Research Ethics Application, signed 29/04/2026: "Intelli Flow AI: Secure
    and Adaptive Smart Traffic Control System".
  - Working title used in CLAUDE.md and the repository README: a third
    variant.

Beyond the inconsistency, the ethics form title contains the word "Adaptive",
which is architecturally false for this system. ADR-005 fixes the invariant:
the signal timeline is compiled offline and the simulation plays it back
without making scheduling decisions. SignalController holds no reference to
queues, sensors, accidents or anomalies, verified by scoped grep in audit
item A5. A title claiming adaptivity contradicts the project's central claim
in its first line.

All three deliverables are submitted together on 23 August 2026, so the
proposal and ethics form are resubmitted rather than fixed historically.
Correcting them is possible and required.

**Decision**

The title is, in all documents, the repository README, CLAUDE.md, the
dissertation title page, and any presentation:

  Secure AI Driven Smart Traffic Control System with Integrated
  Cybersecurity and Governance

**Alternatives rejected**

"Intelli Flow AI: Secure and Adaptive Smart Traffic Control System".
Rejected on two grounds. "Adaptive" contradicts ADR-005 and the pre-planned
invariant. "Intelli Flow AI" is a product name for a system that is a
simulation and a dissertation artefact, not a product, and it displaces the
word "Governance", which names a third of the project's actual contribution.

Retaining "Adaptive" and redefining it in the text to mean "the schedule
adapts between weekly regenerations". Rejected: a title should not need a
definition to stop being misleading, and the reading a marker brings to the
word is the reading that counts.

**Consequences**

Positive: one title across every artefact. The word most likely to invite
the question "so does it react to live traffic or not?" is removed from the
first line an examiner reads, which means the pre-planned architecture is
presented as a deliberate design position rather than as a defence.

Negative: the resubmitted ethics form will differ in title from the version
signed on 29/04/2026. This must be mentioned to the supervisor rather than
changed quietly, alongside the other scope corrections being made to that
form.

**Sources**

Computing Project Proposal Form, S.M. Sasiru N. Senadhiparhi, signed
17/05/2026. Wrexham University Research Ethics Application for Partner
Institutions, Appendix 2, signed 29/04/2026. ADR-005. Audit item A5,
2026-08-19.

## ADR-031: Approval modal review pane - scroll-gated ACCEPT, pivoted from one read

**Date:** 2026-08-19
**Status:** Accepted

**Context**

ADR-028 built the approval gate: a human operator authenticates and a hash
binds their decision to the exact bytes of signal_schedule_plan.csv. It did
not put the plan itself in front of the operator. Before this change, an
operator could authenticate and click ACCEPT having seen only a filename,
an hour range, a row count and sixteen hex characters of a hash - never a
single green-seconds value the schedule would actually deliver. A signature
bound to unseen bytes is a hash check with a human's name attached to it,
not a review.

The plan file is one row per (hour, road): 672 rows, 168 hours * 4 roads
(confirmed directly against the file at STEP 0 of this task: header
`hour,road,predicted_count,green_seconds`, 673 lines including header).
Rendering all 672 rows in that shape would mean reading the same four-road
group of numbers under four different row labels for every hour scrolled
past. A pivoted view - one row per hour, one column per road - shows the
same information in 168 rows instead of 672, and each row is the actual
unit an operator would reason about ("hour 19: North 49s, South 23s, East
20s, West 16s"), not a slice of it.

**Decision**

The approval modal gained a scrollable review pane, above the credential
fields, showing the pivoted view (`_pivot_plan_rows`, traffic_sim.py) 14
rows at a time, with UP/DOWN/PAGEUP/PAGEDOWN/mouse-wheel scrolling and a
"hour X-Y of 167 (rows A-B of 168)" position indicator.

ACCEPT is drawn visibly disabled (grey, with a "scroll to the last row to
enable" hint) until a `scrolled_to_end` flag is set, which happens once
`scroll_offset` has reached the last page - a ratchet, not reset by
scrolling back up, so an operator who scrolls once and then re-checks an
earlier row without incident does not have to scroll to the end a second
time. `_attempt_submit()`, the single closure ADR-028 already required
ENTER and a mouse click on ACCEPT to share, gained one more early-exit
check: `if not scrolled_to_end: return None`. Both submit paths therefore
inherit the gate for free; there is no second place to add or forget it.

The plan file is read exactly once per gate invocation, as raw bytes.
`hashlib.sha256` runs over those bytes; `_parse_plan_rows` and
`_pivot_plan_rows` parse the SAME bytes for the summary line and the
table. `_run_approval_gate` no longer calls `sha256_file()` or the old
path-based `_read_plan_summary()` (which each opened the file
independently) - both are collapsed into one `open(...).read()`. A second,
independent read could observe a different file if something rewrote
`signal_schedule_plan.csv` between the two opens (a regenerate mid-review,
a concurrent write); at that point the hash and the table would each be
honest about a different file, and the operator would be approving neither
one correctly. `_read_plan_summary(path)` is kept, unchanged in contract,
for callers that do not need to share a read with a hash (currently only
the test suite's throwaway rect-geometry lookups).

**Why an operator who cannot read the artefact is not meaningfully
approving it:** ADR-028 already established that binding a decision to an
unread file's hash approves nothing in particular if the file could differ
from what was intended. The same reasoning applies one level up: a hash
faithfully bound to a file the operator never looked at is still a
decision made blind. Sign-off is supposed to be evidence a human judged the
schedule reasonable for the roads and hours it covers - underpowering
West at 3am, say, or over-favouring North all week - and that judgement is
only possible if the numbers were in front of them. The scroll gate does
not verify judgement occurred; it verifies the minimum precondition
without which judgement cannot occur.

**Why the scroll gate exists, specifically as a gate rather than a
suggestion:** an ACCEPT button that is merely present next to an
unscrolled table is, in practice, indistinguishable from ADR-028's
original modal - nothing stops an operator opening the app, typing
credentials, and clicking ACCEPT within the first second, exactly as
before this change. Disabling the control until the last row has been
displayed is the only mechanism available in this UI (no click-tracking
per row, no eye-tracking, no reading-time heuristic) that forces at least
the display of every row into the sequence of events leading to approval.

**Alternatives rejected**

Showing a summary (min/max/mean green seconds per road, or a chart) instead
of full rows. Rejected: a summary is a second, independently-computed
artefact, not the artefact being hashed - it reintroduces exactly the
two-reads-can-diverge risk this change eliminates for the raw rows, this
time between "what was approved" (the file) and "what was shown"
(statistics about the file). A summary can also smooth over the single bad
hour a scroll would surface; the approval is supposed to be of the actual
schedule, not of its shape in aggregate.

Allowing ACCEPT without scrolling, with scrolling merely offered as
optional. Rejected on the reasoning above: an optional review pane changes
nothing about what an operator can get away with, and the entire point of
this feature is to make "I approved it without looking" structurally
harder than "I approved it after looking", not merely possible either way.

A one-shot "I have reviewed this schedule" checkbox instead of scroll
tracking. Rejected: a checkbox can be ticked in the same second the modal
opens, which is exactly the failure mode ADR-028 already has and this
change exists to close. Scroll position is not self-report; it is the one
piece of state in this UI that cannot be true unless the rows were
actually paged through.

**Consequences**

Positive: the hash, the summary line and the table now derive from one
`open()` call, closing a double-read that existed in the pre-change code
(`_read_plan_summary(APPROVAL_TARGET_PATH)` and `sha256_file
(APPROVAL_TARGET_PATH)` were two independent opens). Verified this session
(E3): `hashlib.sha256` over the bytes `_parse_plan_rows`/`_pivot_plan_rows`
consumed equals `sha256_file(path)`'s independent result, and the pivoted
row count times four roads equals the underlying row count exactly
(168 * 4 = 672). Verified (E4): the pivoted view's first and last rows
agree field-for-field with the file's hour-0 and hour-167 rows. Verified
(E1/E2, both the mouse and keyboard submit paths): ACCEPT and ENTER are
both refused with `approvals.jsonl` byte-identical before/after when
attempted before scrolling to the end, and both succeed, with a record
correctly appended, once scrolled to the last row.

Negative, and the honest limit of this control: **scrolling past every row
evidences that the rows were displayed on screen, not that they were read
or understood.** An operator can page down at speed without registering a
single value, exactly as a person can click through a EULA without reading
it. This control raises the minimum physical interaction required before
ACCEPT is reachable; it cannot, and does not claim to, verify comprehension
or judgement. This limitation must be stated plainly in the dissertation
wherever this feature is described as a governance control, in the same
terms used here, not overstated as "the operator reviewed the schedule".

Negative: the modal grew from 560x460 to 760x660 to fit the pane without
clipping (verified by screenshot at three scroll states: top, mid-scroll,
and bottom-with-ACCEPT-enabled - box.y and box.bottom leave a 30px margin
to the 1280x720 window at both the top and bottom in the worst-case text
layout, error message and attempt count both present). A future change to
`APPROVAL_PANE_VISIBLE_ROWS` (currently 14) must re-check this margin
rather than assume it still fits.

**Sources**

This session's STEP 0 (header and row-count confirmation against
signal_schedule_plan.csv) and Review E1-E4, E6, E7 evidence. ADR-028,
whose `_attempt_submit()` single-submit-path design this change extends
rather than duplicates.

## ADR-032: Provenance header on sensor_log.csv, and always writing a file

**Date:** 2026-08-19
**Status:** Accepted

**Context**

ADR-029 recorded that sensor_log.csv reaches disk as plaintext. A separate
problem exists one level up from confidentiality: even a fully readable
sensor_log.csv carried no indication of which commit produced it, which
schedule was running, whether it had been approved, or whether the
encryption toggle or an attack button had been touched during the run. A
reader - a supervisor, an examiner, a future session picking the file up
cold - had no way to tell a fresh, honest run from a stale file left over
from an earlier one, or from a run where an attack was fired and then
cleared before quitting. `write_log()` also returned silently when
`self.log_rows` was empty (`ENABLE_LOGGING = False`, or a run quit before
the first `LOG_INTERVAL_S`), which meant a zero-row run left whatever file
was already on disk untouched - a stale file with no marking that it was
stale.

**Decision**

`write_log()` now always writes a file, and prefixes it with a provenance
block - one `# key: value` line per field, so the file still parses as CSV
via `pandas.read_csv(path, comment='#')` (verified this session, G8: row
count and first/last rows identical between `pandas` and the in-memory
`log_rows` list). Fields: `run_started_utc`, `run_started_local`,
`run_ended_utc`, `simulated_duration_s`, `rows_written`, `git_commit`,
`git_tree`, `schedule_file`, `schedule_sha256`, `approved_by`,
`approved_at`, `encryption_at_end`, `encryption_toggled_during_run`,
`attacks_fired`, `demand_multiplier`, `density`. Every field is either a
real value or the literal string `"unavailable (reason)"` - never a blank
line, never the Python string `"None"` - so a reader can tell "this field
could not be determined, here is why" apart from "this field was silently
dropped".

The zero-row branch was changed from an early `return` to writing the
provenance header plus the CSV column header with zero data rows and
`rows_written: 0`. A run that recorded nothing now produces a file that
visibly says so, rather than leaving a previous run's file in place with
nothing to distinguish it from a fresh one.

Two fields needed new, minimal state on `Simulation`, added exactly where
the brief allowed and nowhere else: `self.encryption_toggled` (set `True`
the first time the E key / ENCRYPT button fires, in `main()`'s
`_toggle_encryption()`) and `self.attacks_fired` (a set, added to by
`_make_false_data_attack()`/`_make_spoof_attack()`, both already-named
functions in `main()` that needed one line each, not new plumbing).
Neither restructures the toggle or attack code; both are bookkeeping
alongside it.

**Why an unlabelled evidence file cannot be shown as evidence:** a
dissertation, a viva, or a supervisor meeting will want to point at
sensor_log.csv and say "this is what the system recorded." Before this
change, that sentence was unverifiable from the file alone - nothing in it
said which code produced it, whether the schedule behind it was ever
approved, or whether the run included an attack demonstration or was
clean baseline traffic. A file that cannot answer "which run is this" is
not usable as evidence of anything specific; at best it is evidence that
some run happened at some point under some configuration. The provenance
header exists to make sensor_log.csv self-describing enough to cite.

**Why `encryption_toggled_during_run` exists as a field distinct from
`encryption_at_end`:** `encryption_at_end` alone cannot distinguish "this
run was clean the whole time" from "this run had encryption off for
attack demonstration purposes and was switched back on right before
quitting" - both end in the same state. The two together answer two
different, both useful, questions: what was the state when the run ended,
and did the state change at all during the run. A reader checking whether
a false-data-injection demo actually ran with encryption off at some point
needs the second question answered, not just the first.

**Why the zero-row branch was changed:** the old early-`return` behaviour
meant a viewer could open sensor_log.csv, see a well-formed file with
plausible-looking rows, and have no way to know those rows were written
minutes or days ago by a different run, not the one just quit. Silently
preserving stale output under the name of a run that produced nothing is
worse than an empty file, because an empty file at least prompts the
question "why is this empty," while a stale-but-plausible file prompts no
question at all.

**The limitation, stated plainly:** the provenance header is self-reported
by the same program that wrote the data below it. Nothing computes a
checksum over the header, nothing signs it, and nothing prevents a text
editor from changing `git_commit` or `attacks_fired` after the fact. This
is categorically different from ADR-028's approval record, which is
hash-bound to the exact bytes of a separate file and independently
re-checkable via `verify_still_valid()` using nothing but the file on disk
and the append-only log entry. The provenance header answers "what does
this program claim about the run that produced this file"; it does not
answer, and must never be presented as answering, "can this claim be
trusted against tampering." Any future use of sensor_log.csv as
tamper-evident evidence - as opposed to descriptive metadata - would need
the same hash-binding treatment ADR-028 gave the schedule file, not an
extension of this header.

**Alternatives rejected**

Per-run timestamped filenames (e.g. `sensor_log_2026-08-19T100459Z.csv`)
instead of a fixed `LOG_PATH` that is overwritten every run. Rejected:
this was explicitly out of scope for this change (`LOG_PATH` and the `"w"`
open mode were both marked do-not-touch), and rejected on merit too - a
growing pile of per-run files still leaves each individual file exactly as
unlabelled as before unless it also carries a provenance header, so the
header is the change that actually solves the problem; a naming scheme
without it would only rename the same gap.

Splitting traffic telemetry from security audit evidence into two files -
one plain CSV of sensor readings for traffic analysis, one separately
secured file (hash-bound, or encrypted at rest per ADR-029's deferred
work) carrying run provenance and attack/approval history. This is
recorded as the deployment-grade design and was deliberately not built at
this scale: a real installation would not want a security audit trail
sharing a write path, a schema, or a trust level with routine traffic
counts, and would want the audit file access-controlled separately from
data an ordinary traffic engineer might read. Building that split, plus
the access control it implies, was judged disproportionate to a
single-file, single-machine student project four days from submission. A
single provenance-headed CSV is the scope-appropriate version of the same
idea: labelled, not access-separated.

**Consequences**

Positive: verified this session (Reviewer G1-G10). G1: all 16 fields
populated on a real headless run, none blank or `"None"`. G2: `git_commit`
matches `git rev-parse --short HEAD` exactly; `git_tree` observed as
`clean` against a genuinely unmodified tree at commit 0eb17ac and as
`dirty` after a real one-line change to a tracked file, restored exactly
afterward - both states observed directly, not inferred. G3:
`encryption_toggled_during_run` reads `yes` only on a run where the toggle
fired, `no` otherwise, with `encryption_at_end` independently correct in
both. G4: `attacks_fired` names a fired attack and reads `none` when
nothing fired. G5: `demand_multiplier`/`density` match the live
`Simulation.demand_multiplier` and its `DENSITY_BUTTON_LABELS` entry
exactly. G6: a decoy file with recognisable prior content is fully
replaced by a zero-row provenance-and-header-only file, never left
untouched. G7: `schedule_sha256` and `approved_by` in the header match the
corresponding fields of the actual last line appended to
`security/approvals.jsonl` by a real run of `_run_approval_gate`. G8: the
pandas comment-parsing round trip is exact. G9: 42/42 tests pass (40
before this change, 2 added: the pandas round-trip and the zero-row decoy
replacement). G10: zero hits for `log`, `provenance`, `git`, `commit`,
`approved`, `encryption_toggled` on executable lines inside
`SignalController` (686-753), `Vehicle.update` (992-1036) or
`Simulation._leaders` (1301-1370) - the only near-hits were the ordinary
English word "committed" inside `_leaders`' own docstring ("a vehicle
committed to a turn"), not code, and not related to this change.

Negative: sixteen lines of header now sit above the CSV in every
sensor_log.csv, so any external tool that reads the file with a bare
`csv.reader` or `pandas.read_csv()` without `comment='#'` will misparse
the first sixteen rows as data. Nothing in this repository does that
today (verified: `write_log` and the new tests are the only writers, and
G8 is the only reader, both updated together), but this must be stated if
sensor_log.csv is ever handed to a tool outside this repository.

**Sources**

This session's STEP 0 (write_log, encryption state, attack-button
tracking, demand/density reachability, approval-record reachability, all
confirmed by direct read before any code was written) and Reviewer G1-G10
evidence, run against commit 0eb17ac. ADR-028 and ADR-029, whose
reasoning about hash-binding and honest disclosure this entry extends
rather than duplicates.

## ADR-033: Dated schedule generated over held-out historical data, not forecast forward - amends ADR-012's horizon

**Date:** 2026-08-19
**Status:** Accepted
**Amends:** ADR-012's weekly regeneration horizon. Does not reverse it -
ADR-012's reasoning (lag_168/lag_336/roll_168_lag168 cannot be computed
past 168 hours ahead, so a schedule cannot be forecast further forward
than that without recursive forecasting or fabrication) is unaffected and
still governs any FORWARD schedule. This entry adds a second, different
kind of schedule - one generated over dates that have already happened -
which sidesteps that constraint entirely rather than solving it.

**Context**

The brief that produced this entry asked for an annual, dated schedule
covering 2017-07-01 to 2018-06-30, generated forward from the existing
trained model without retraining, fabricating, or imputing any data.
STEP 0 checked this against the data before writing any code, as
instructed, and found it impossible as specified:
data/traffic_final_cleaned.csv ends at 2017-06-30 23:00 (confirmed
directly: `df['DateTime'].max()`); the requested window starts exactly
one hour later and runs a full year past it. Every one of the requested
35,040 rows would need a feature value with no real row behind it
anywhere in the source data. This is not a partial gap to work around -
it is the entire window. Per the task's own instruction ("If any of a-e
fails, stop and report rather than working around it"), this stopped
before any code was written, and the finding was reported rather than
silently substituted.

Offered a choice between using the model's real 6-month held-out test
window instead (2017-01-01 to 2017-06-30, 17,376 rows), stopping
entirely, or something else, the operator chose the real test window.
This is also the honest answer to a question implicit in the original
"annual" framing: no full 12-month window in this dataset is
simultaneously real (non-synthetic) data AND held out from training -
the real test split per ADR-008 is six months, not twelve, because the
dataset itself only spans 2015-11-01 to 2017-06-30.

**Decision**

`generate_dated_schedule.py` (new file, independent of
`generate_timeline.py`, which is unmodified) builds a DATED schedule -
`signal_schedule_dated.csv`, one row per (real datetime, road), 17,376
rows - over 2017-01-01 00:00 to 2017-06-30 23:00: every hour in
`model_card.json`'s own `test_row_count: 17376`, i.e. every row the
model's `split_date: "2017-01-01"` already marks as held out from
training. `models/count_model.joblib` is loaded and only ever `.predict()`-ed,
never `.fit()`-ed. Every feature value is read via `data_prep.load_and_engineer()`
from real rows already present in `traffic_final_cleaned.csv` - the
target window sits inside the dataset, not past its end, so no future
row ever needs to be fabricated or appended (contrast
`generate_timeline.py`'s `build_plan_from_model()`, which DOES append
future rows with `Vehicles=NaN`, because ADR-012's weekly window is
genuinely in the future relative to generation time). Counts are
converted to green seconds by the SAME, unmodified `allocate_green()`
(ADR-021: `CYCLE_SECONDS=120`, `AMBER_SECONDS=3`, `MIN_GREEN_SECONDS=12`,
`AVAILABLE_GREEN=108`, largest-remainder rounding) - confirmed this
session (J3/J4): every hour's four `green_seconds` values sum to exactly
108, and the observed minimum across all 17,376 rows is 14s, above the
12s floor.

**What generating over held-out historical data does, and does not,
demonstrate:** this is NOT a forecast. Every predicted count in
`signal_schedule_dated.csv` is the model's output for an hour that has
already happened, using lag features computed from real prior hours that
have also already happened - nothing here required the model to predict
into a future no data exists for. What it DOES demonstrate: the model,
frozen exactly as trained (no retraining, no leakage, `split_date`
respected), applied at genuine scale (17,376 rows, six months, not a
three-hour demo plan) to data it never saw during training, running
through the unmodified ADR-021 allocation layer end to end, and now
sitting behind the same hash-bound, scroll-gated approval control
(ADR-028, ADR-031) as any other schedule this project produces. What it
does NOT demonstrate: that the model can forecast a real future week or
year. That question is ADR-012's, and remains open exactly as ADR-012
left it - dropping the illegal features costs accuracy so badly
(calendar-only MAE 15.10 against 4.25) that no annual horizon is honestly
deployable from this feature set, a finding this entry does not disturb.

**The J5 baseline result:** measured this session, model `predicted_count`
against a `lag_8760` seasonal naive baseline (same hour, exactly one
year - 8760 hours - earlier; fully computable here since one year before
the earliest window hour, 2016-01-01, sits inside the dataset with zero
missing lookups). Per road: East 5.70 vs 8.68, North 9.72 vs 33.82, South
4.65 vs 10.80, West 2.06 vs 3.09 (model MAE listed first in each pair).
OVERALL_excl_West (ADR-011 convention): model 6.69 vs naive 17.77 - the
model wins clearly, by a wide margin, on every individual road as well as
overall. This is reported as measured, per the task's own instruction to
report a naive win plainly if that had been the result; here the finding
runs the other way, by more than the margin ADR-020's rolling-vs-static
comparison needed to call meaningful. (These per-road MODEL figures are
not a new measurement - they reproduce `model_card.json`'s pre-recorded
"static" protocol test MAEs exactly, which is itself a cross-check that
this generation path computes what the model card already claims it
does.)

**The weakened scroll gate, stated as a real consequence, not
minimised:** ADR-031's gate required scrolling to literally the last of
168 pivoted rows - about 12 PAGEDOWNs, a small enough number that
reaching it was a reasonable proxy for "the rows were at least paged
past". At 4,344 pivoted rows, the same PAGEDOWN-only mechanism would take
over 300 presses - not a review, an endurance test nobody would actually
perform, which would make the gate a control that exists on paper and is
routinely defeated by the operator disabling their own patience. HOME,
END and a visible "END: jump to last row" button (returned from
`draw_approval_modal` as `jump_end_rect`, hit-tested exactly like
`accept_rect`/`u_rect`/`p_rect`) now make reaching the end a single
keypress or click. This is an explicit, honest weakening of the gate from
"was shown every row" (true at 168 rows, where paging through all of them
was a low enough cost that an operator plausibly would) to "was shown the
artefact and confirmed its extent" (true at any row count, but a strictly
weaker claim) - not a fix, a trade against scale. ADR-031's own stated
limitation - scrolling evidences display, not comprehension - already
applied even at 168 rows; this entry does not make that limitation worse
in kind, only easier to reach without the comprehension ADR-031 was
already honest about not being able to verify.

**The weekly template stays on disk:** `signal_schedule_plan.csv` (673
lines, the ADR-012 hour-of-week template `generate_timeline.py`
produces) is untouched by this change - not deleted, not regenerated, not
read by anything this entry adds. Confirmed this session: absent from
`git status`, present on disk at its prior line count, `git log` shows no
commit touching it after this work began. `APPROVAL_TARGET_PATH` now
points at `signal_schedule_dated.csv` instead, so the weekly template is
no longer what gets approved or played - but it exists, unmodified, for
`generate_timeline.py` to keep producing independently whenever that
separate, still-live code path is run.

**Alternatives rejected**

Recursive multi-step forecasting: predict week 1 of the requested annual
window, feed those predictions back in as the lag features for week 2,
and iterate for 52 weeks. This was already rejected once, in ADR-012, for
the general annual-horizon problem, and is rejected again here for the
same reason: error compounds across iterations with nothing in this
project's scope to bound it, and STEP 0 already showed the requested
window doesn't need this technique to be honestly built at all once
corrected to real dates - reaching for a compounding-error technique to
avoid stopping and reporting a data gap would have been solving the wrong
problem.

Synthesising, imputing, or otherwise fabricating feature values for the
missing 2017-07-01-to-2018-06-30 window so the ORIGINAL annual brief
could be satisfied literally. Rejected outright, and was the explicit
reason STEP 0 stopped rather than working around the gap: this project's
West-road history (ADR-002) already carries one disclosed synthetic
segment and the cost of disclosing it honestly across a dozen later ADRs;
manufacturing a second one to make an annual demo file exist would be the
exact failure this project's own audit culture (ADR-025 through ADR-029)
exists to catch, self-inflicted.

Keeping the ORIGINAL annual window and simply degrading the claim (e.g.
labelling it "illustrative, not evidential"). Rejected: a file with
35,040 rows of fabricated-by-necessity numbers, sitting behind the same
hash-bound approval control used for genuine schedules, invites exactly
the confusion a disclaimer buried in an ADR would not prevent - the
control's whole design (ADR-028, ADR-031) is that what gets approved
looks and behaves like what gets played. A schedule that cannot honestly
carry that weight should not be built merely because it was asked for
before the data gap was known.

**Consequences**

Positive: `signal_schedule_dated.csv` and the review pane that displays
it are traceable end to end to real rows in
`data/traffic_final_cleaned.csv`, verified this session (J1, J2): exactly
17,376 rows, first datetime 2017-01-01T00:00:00, last
2017-06-30T23:00:00, zero rows in the source window carrying
`Synthetic_Segment_Unverified=True`. Full suite: 43/43 (was 42 - two
tests updated for the new `datetime`/`hour_of_week` schema and the END
key, two added: HOME-after-END ratchet persistence, and the earlier
pivot/row-count check re-targeted at the new file). Scoped grep (J10):
zero hits for `annual`, `datetime`, `schedule`, `predict` on executable
lines inside `Vehicle.update` or `Simulation._leaders`; one hit inside
`SignalController` - `current_datetime_label`, a pre-existing,
unmodified, display-only getter for the dashboard (SECTION 15) that
returns already-loaded timeline metadata and touches nothing this entry
added - not a violation of the reactivity invariant, and flagged here
rather than quietly excluded from the count.

Negative: this is a second schedule-generation code path
(`generate_dated_schedule.py`) alongside `generate_timeline.py`, and they
will drift unless deliberately kept in sync - they already share
`allocate_green()`, `ROTATION`, `MODEL_PATH` and `CYCLE_SECONDS` by
import rather than duplication, but the row-building logic itself (dated
vs hour-of-week) is necessarily separate, and a future change to one
that should apply to both (e.g. a change to how `hour_of_week` or the
plan's date semantics work) must be made in both files or checked
against both.

Negative, disclosed rather than smoothed over: the approval modal's
scroll gate is measurably weaker at this scale than ADR-031 described it
- see above. Any dissertation text describing the scroll gate as a
governance control must state the row-count-dependent difference between
"scrolled past every row" (168-row weekly template) and "confirmed the
artefact's extent via a jump control" (4,344-row dated schedule), not
present both as the same strength of control.

**Sources**

This session's STEP 0 (date-range and coverage check against
`traffic_final_cleaned.csv`, run before any code was written, which is
what surfaced the annual window's infeasibility) and Reviewer J1-J10
evidence, run against the real generated `signal_schedule_dated.csv`.
`model_card.json`'s `split_date`/`test_row_count` fields, cross-checked
against this session's own computation. ADR-008 (the temporal split this
window reuses), ADR-011 (the OVERALL_excl_West convention applied to
J5), ADR-012 (the horizon reasoning this entry amends), ADR-021 (the
allocation constants quoted and reused unchanged), ADR-028/ADR-031 (the
approval gate and review pane this schedule now flows through).

---

## ADR-034: Recursive annual forecast built as a disclosed, separate demo artefact

**Date:** 2026-08-19
**Status:** Accepted
**Amends ADR-012 and ADR-033's rejection of recursive multi-step
forecasting as a technique. Does not reverse either entry's underlying
decision: ADR-012's weekly regeneration horizon stands, and ADR-033's
choice of `signal_schedule_dated.csv` as the approval target stands
unchanged - `APPROVAL_TARGET_PATH` is deliberately NOT repointed at the
artefact this entry describes. This entry adds a second, clearly
separate, explicitly degraded demonstration artefact alongside both,
rather than replacing what either decided.**

**Context**

The brief that produced this entry asked, again, for the literal annual
window ADR-033 already found impossible to build from real data alone
(`traffic_final_cleaned.csv` ends 2017-06-30 23:00; 2017-07-01 to
2018-06-30 is entirely past it) - but this time specifying the technique
ADR-012 and ADR-033 both rejected for it: recursive forecasting, predict
one step, feed the prediction back in as a lag input, repeat. Both
earlier entries gave the same reason: error compounds across iterations
with nothing in this project's scope to bound it.

Told this directly, the operator's instruction was to build it anyway,
for a specific, stated reason that this entry records rather than
smooths over: this project has no live weekly-cadence deployment loop.
ADR-012's real design - regenerate weekly from the most recent real
data - never needs a lag value more than 168 hours old, because a new
week of real data arrives before the next schedule is generated. The
compounding-error problem recursive forecasting creates is therefore a
property of manufacturing a single, one-off, 8,760-hour demonstration
file from a dataset that stops 8,592 hours short of covering it - not a
property of the system ADR-012 actually describes running. Recursive
forecasting is the only way to produce that literal file without
retraining (disallowed by this task) or fabricating feature inputs
outright (rejected in ADR-033 for the same file, on the same grounds
academic honesty required rejecting it there).

This is a narrower claim than "recursive forecasting is fine here." It
is "recursive forecasting's cost is fine here, PROVIDED it is measured,
disclosed in full, and kept out of the approval path" - which is what
the rest of this entry and `generate_annual_forecast.py` do.

**Decision**

`generate_annual_forecast.py` (new file, independent of
`generate_timeline.py` and `generate_dated_schedule.py`, neither of
which is modified) adds a THIRD schedule artefact,
`signal_schedule_annual.csv`, alongside `signal_schedule_plan.csv` (the
ADR-012 weekly template) and `signal_schedule_dated.csv` (the ADR-033
dated schedule) - neither existing file is deleted, overwritten, or
read by this script. `models/count_model.joblib` is loaded and only
ever `.predict()`-ed, never `.fit()`-ed, per this task's hard
constraint.

Two modes, sharing one `recursive_forecast()` function:

`--mode deliverable` seeds on all real data through 2017-06-30 23:00 and
forecasts recursively to 2018-06-30 23:00 (the literal window originally
requested), in 168-hour blocks - the exact size at which
lag_168/lag_336/roll_168_lag168 stay legal within a block (ADR-015),
so no block ever needs a value from later in itself. Each block's own
predictions are appended to the per-road history buffer before the next
block is computed. Verified this session: 35,040 rows exactly (8,760
hours x 4 roads), first datetime 2017-07-01T00:00:00, last
2018-06-30T23:00:00.

`--mode validation` seeds on real data through 2016-12-31 23:00 only -
deliberately withholding the six months of real data that follow - and
forecasts the same way across 2017-01-01 to 2017-06-30, where real
actuals already exist (ADR-008's held-out test period), then compares.
This is the only source of evidence for how fast the recursion's error
actually grows on this dataset; `--mode deliverable` alone would produce
35,040 numbers with no way to judge them.

Green seconds are computed by the SAME, unmodified `allocate_green()`
(ADR-021: `CYCLE_SECONDS=120`, `MIN_GREEN_SECONDS=12`,
`AVAILABLE_GREEN=108`, largest-remainder rounding), imported from
`generate_timeline.py`, not reimplemented. Verified this session: every
one of the 8,760 hours in the deliverable file sums its four
`green_seconds` to exactly 108 (zero exceptions), and the observed
minimum green across all 35,040 rows is 14s, above the 12s floor.

**The changeover from real to predicted lags, STEP 0(b) computed and
this session confirmed exactly:** `lag_168` and `roll_168_lag168` both
resolve to real data only while `hour - 168h <= 2017-06-30 23:00`, i.e.
for the first 168 hours of the forecast window (2017-07-01 00:00
through 2017-07-07 23:00). At 2017-07-08 00:00, `lag_168` needs
`Vehicles[2017-07-01 00:00]`, which is itself a prediction. Verified:
`lags_real=true` for exactly 672 of 35,040 rows (168 hours x 4 roads);
`lags_real=false` for the remaining 34,368 rows (8,592 hours), with the
changeover falling exactly at 2017-07-08T00:00:00 as computed. 98.1% of
the deliverable file's hours are therefore forecasts of forecasts to
some depth, not single-step predictions from real inputs.

**The diff-to-level reconstruction, verified against the actual model
class rather than assumed:** `model_wrapper.DiffTargetRandomForest`
trains on `Vehicles - lag_168` and its `.predict()` returns
`diff_pred + X["lag_168"]` internally - the diff representation never
crosses the class boundary, so `recursive_forecast()` calls `.predict()`
once per block and receives vehicle counts directly. The running level
is carried forward by appending each block's own `predicted_count`
values into the same per-road history buffer used to compute
`lag_168`/`lag_336`/`roll_168_lag168` for the next block, exactly as if
they were real `Vehicles` rows (`Outlier_Flag=False`,
`Synthetic_Segment_Unverified=False` - they are forecasts, not part of
the ADR-002 synthetic segment). Hand-verified this session, independent
implementation, first 5 hours x 4 roads (20 values): e.g. East hour 0,
`lag_168` anchor 16, `diff_pred` 3.3348, hand-computed
`16 + 3.3348 = 19.3348`, matching the script's own CSV output to 1e-6.
All 20 values matched exactly.

**West inherits the ADR-002 synthetic segment, stated plainly:** the
seed history (all of `traffic_final_cleaned.csv`, since `--mode
deliverable` seeds through 2017-06-30) carries 10,248 West rows marked
`Synthetic_Segment_Unverified=True` (70% of West's 14,592 rows; East,
North, South carry zero), confirmed by direct count this session. West's
`lag_336` reaches back up to 336 hours from the forecast's first block,
which for early forecast hours can still land inside real West data
(the synthetic segment ends 2017-01-01, well before the seed cutoff), so
West's very first predictions are not synthetic-derived - but every
West prediction from block 2 onward (2017-07-08 onward) is built in
part from a history buffer whose own deep history includes that
synthetic segment, the same way any West-derived quantity in this
project does. This is not a new problem this entry creates; it is the
existing ADR-002 disclosure propagating into a new artefact, and is
recorded here so a reader of `signal_schedule_annual.csv` alone, without
also having read ADR-002, is not misled about what "West" means in this
file.

**K6, the degradation measurement, in full (validation protocol, MAE,
OVERALL_excl_West per ADR-011):**

    Week   Hours (horizon)      East    North   South    West   OVERALL_excl_West
    1      1-168                8.299    6.806   2.558   2.340   5.888
    2      169-336              3.682   12.522   3.405   1.716   6.536
    4      505-672              4.257   11.108   3.822   1.963   6.395
    8      1177-1344           12.611   13.405  10.088   2.204  12.035
    13     2017-2184            6.290   10.130  10.927   1.818   9.116
    26     4201-4344 (partial)  5.760   24.764  13.816   3.170  14.780

Week 26 / week 1 ratio, OVERALL_excl_West: 14.780 / 5.888 = **2.51x**.
Stated as measured: this is under the 3x threshold this project uses
elsewhere to call an effect large, so it is reported as a real but
moderate degradation, not a collapse - and the per-road breakdown shows
it is not evenly spread. North (the busiest road, and the one ADR-020
already flagged as the road the model most often loses on) degrades
worst and least steadily, 6.806 at week 1 rising to 24.764 at week 26,
a 3.6x rise on North alone even though the excl-West blend stays under
3x. East and West stay comparatively stable across the full six months
measured. The curve is not monotonic (week 8 is a local peak above week
13), which the committed plot at
`results/recursive_degradation_mae_by_horizon.png` shows directly -
flagged REQUIRES HUMAN READ in `results/RECURSIVE_DEGRADATION.md`
itself, per this project's own rule (ADR-017) that a plot's shape needs
a human read and cannot be cleared by a numeric check alone.

**K7, sanity check against the real same-season period, also measured
this session:** deliverable-file `predicted_count` per road across all
8,760 hours, against real `Vehicles` for 2016-07-01 to 2017-06-30:

    Road    Forecast min/max/mean       Real min/max/mean
    East      5.7 /  44.0 / 16.2         1 / 180 / 16.1
    North    14.0 /  83.3 / 51.9         5 / 156 / 56.1
    South     7.4 /  27.5 / 14.0         1 /  48 / 17.1
    West      3.4 /  24.1 /  7.2         1 /  36 /  7.3

The forecast neither collapses to a flat line (every road keeps a wide
min-max spread relative to its mean) nor diverges unboundedly. What it
does show plainly: means stay close to the real same-season figures on
every road, but the extremes are compressed, most visibly on East
(real max 180, forecast max 44) and North (real max 156, forecast max
83). East is the "spiky" road (ADR-015, ADR-020) precisely because
Random Forest averaging helps there against genuine volatility;
recursive forecasting compounds exactly that averaging tendency across
53 successive blocks, so the file systematically underrepresents spike
events on the roads whose real behaviour is spikiest. This is the same
tree-cannot-extrapolate-and-averages-toward-the-mean mechanism ADR-012
and ADR-013 already documented for a single-step prediction; recursion
does not introduce a new failure mode, it repeatedly re-applies the
existing one.

**What horizon this supports, deployment versus presentation:** the
measured degradation (2.51x over 26 weeks, non-monotonic, North alone
at 3.6x) says the first 1-4 weeks of `signal_schedule_annual.csv` carry
error broadly comparable to the model's already-recorded single-step
performance (`model_card.json`'s rolling-protocol test MAEs), while
error beyond roughly 8-13 weeks is measurably worse and, on North
specifically, close to doubling again by week 26. None of this file is
fit to deploy: ADR-033's own dated schedule already covers the model's
one genuinely-evaluated real-data horizon (six months, single-step, no
recursion), and stands as the artefact this project would actually
put behind an approval gate. `signal_schedule_annual.csv` exists to
demonstrate, for a written report, what an annual artefact built this
way looks like and costs - a presentation artefact with its cost
measured and disclosed, not a deployment candidate. `APPROVAL_TARGET_PATH`
is left unchanged for exactly this reason.

**Alternatives rejected**

Retraining the model on a schedule that would let it predict a full
year natively. Disallowed by this task's own constraints (`models/count_model.joblib`
is used as-is); also does not solve the underlying problem, since
ADR-012 already established that dropping the illegal lag features to
reach an annual-legal feature set collapses North's accuracy (calendar-only
MAE 15.10 against 4.25) - retraining for a longer native horizon would
have to make the same trade.

Direct multi-horizon modelling - training a separate model per forecast
step (or per week) so no step ever consumes another step's own output.
Standard alternative to recursive forecasting (Hyndman & Athanasopoulos,
"Forecasting: Principles and Practice", 2nd ed., section 11.3, on
recursive versus direct multi-step strategies: direct strategies avoid
error accumulation but require training - and thus a labelled target -
per horizon, and typically show higher variance per individual step).
Rejected here for the same reason as retraining: this task disallows
retraining, and 52 separately-trained per-week models is retraining by
another name.

Fabricating or imputing feature values for the missing window instead
of forecasting them. Already rejected once in ADR-033 for this literal
window, on academic-honesty grounds this entry does not revisit or
weaken.

Building the artefact without a `--mode validation` degradation
measurement, on the reasoning that recursive forecasting's cost is
"well known" in the abstract. Rejected: this project's own standing
rule (`CLAUDE.md`, DECISIONS.md throughout) is that a difference is not
negligible, and a technique is not costed, until it is actually
measured on this data - the 2.51x figure, and North's non-uniform 3.6x
within it, are both real findings this session would not otherwise
have on record.

**Consequences**

Positive: the artefact and its cost are both on record.
`generate_annual_forecast.py`'s `--mode validation` path is the only
thing in this project that has ever measured how recursive error grows
on this dataset, and the figure is disclosed in full rather than
asserted from general principle. Full suite unaffected: 43/43 (K9,
unchanged from before this session - `traffic_sim.py` was not touched).
Scoped grep (K10): zero hits for `forecast`, `recursive`, `annual`,
`predict` on executable lines inside `SignalController`,
`Vehicle.update`, or `Simulation._leaders` (line ranges 697-770,
1003-1052, 1312-1383 respectively) - guaranteed by construction, since
this entry's code changes touch no file `traffic_sim.py` imports from
or is read by.

Negative, disclosed rather than smoothed over: this is now a THIRD
schedule-generation code path
(`generate_timeline.py`/`generate_dated_schedule.py`/
`generate_annual_forecast.py`), sharing `allocate_green()`, `ROTATION`,
`CYCLE_SECONDS` and `MODEL_PATH` by import, but each building its own
rows independently - a future change to shared allocation logic must be
checked against all three, not just the two ADR-033 already flagged
this risk for.

Negative: this entry does NOT fully resolve the tension it opens with.
The operator's own stated reasoning - that ADR-012's real weekly-cadence
deployment never hits this problem - is sound for the deployed system,
but `signal_schedule_annual.csv` itself still exists on disk, still
looks like a schedule file, and nothing at the file-format level
distinguishes it from `signal_schedule_dated.csv` to a reader who has
not also read this entry. Keeping `APPROVAL_TARGET_PATH` off it is a
control at the traffic_sim.py boundary, not a mark on the file itself;
a future session extending the approval gate's file-selection logic
must re-read this entry before doing so, not assume any schedule CSV in
the repository is fit for that gate by default.

**Sources**

R. J. Hyndman and G. Athanasopoulos, "Forecasting: Principles and
Practice," 2nd ed. Melbourne: OTexts, 2018, section 11.3, "Some practical
forecasting issues" / recursive vs. direct multi-step forecasting
strategies.
This session's STEP 0 (feature legality table, changeover computation,
diff-target arithmetic hand-check, synthetic-segment count - all
computed before `generate_annual_forecast.py` was written) and Reviewer
K1-K11 evidence, run against the real generated `signal_schedule_annual.csv`
and `results/RECURSIVE_DEGRADATION.md`. ADR-002 (the West synthetic
segment this entry's West section discloses), ADR-008 (the temporal
split validation mode reuses), ADR-011 (the OVERALL_excl_West
convention applied to K6/K7), ADR-012 and ADR-015 (the horizon and
feature-legality reasoning this entry amends), ADR-017 (the plot-needs-a-human-read
rule applied to K11), ADR-020 (`model_card.json`'s recorded single-step
test MAEs, referenced for the deployment-vs-presentation comparison),
ADR-021 (the allocation constants quoted and reused unchanged), ADR-033
(the dated-schedule decision this entry amends but does not reverse).
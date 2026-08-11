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
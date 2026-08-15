"""
DiffTargetRandomForest lives in its own module, separate from
train_model.py, purely so joblib.load() works from any caller.

If this class were defined inside train_model.py instead, its pickled
__module__ would be "__main__" whenever train_model.py is run the normal
way (`python train_model.py`), because that is how Python's `__main__`
mechanism works for the script actually being executed. Any OTHER script
later doing joblib.load(MODEL_PATH) - a future Stage 3 allocation layer,
an evaluation script, anything - would then fail with
"AttributeError: Can't get attribute 'DiffTargetRandomForest' on
<module '__main__'>", because ITS __main__ is not train_model.py. The
model would be unloadable outside the exact process that trained it.

Keeping the class in a plain importable module sidesteps this: its
__module__ is "model_wrapper" regardless of how train_model.py itself is
invoked, so `from model_wrapper import DiffTargetRandomForest` (which
joblib does automatically while unpickling) works the same way from any
caller.
"""

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.ensemble import RandomForestRegressor


class DiffTargetRandomForest(RegressorMixin, BaseEstimator):
    """
    Wraps a RandomForestRegressor trained on Vehicles - lag_168 (the "diff"
    target selected in results/MODEL_SELECTION.md) so that fit() takes
    actual vehicle counts and predict() returns actual vehicle counts -
    the diff representation never crosses this class's boundary.

    lag_168 is read directly from the input X, since it is already one of
    FEATURE_COLUMNS - no second argument is needed to invert the
    prediction, so a caller building X the normal way (feature_matrix())
    gets vehicle counts back with no extra step.

    Inherits BaseEstimator/RegressorMixin (sklearn convention: __init__
    only stores raw parameters, the real estimator is built in fit() as
    self.model_) rather than being a plain class, because sklearn's own
    tooling - permutation_importance, in train_model.py - requires
    __sklearn_tags__ to identify an estimator as a regressor, which only
    BaseEstimator provides.
    """

    def __init__(self, n_estimators=100, max_depth=10, random_state=42):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.random_state = random_state

    def fit(self, X, y_vehicles):
        self.model_ = RandomForestRegressor(
            n_estimators=self.n_estimators, max_depth=self.max_depth,
            random_state=self.random_state, n_jobs=-1,
        )
        diff_target = np.asarray(y_vehicles) - X["lag_168"].to_numpy()
        self.model_.fit(X, diff_target)
        return self

    def predict(self, X):
        diff_pred = self.model_.predict(X)
        return diff_pred + X["lag_168"].to_numpy()

    @property
    def feature_importances_(self):
        return self.model_.feature_importances_

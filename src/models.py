"""Survival models for the QRT MDS/AML challenge.

Two architectures:

- ``BaselineModel`` (v12): Random Survival Forest trained on ~1800 features.
- ``SpecialistModel`` (v22): smaller RSF (500 trees) trained on the 23-feature
  reduced biologically-robust matrix from ``features.build_specialist_features``.

Both expose ``fit(X, y)`` and ``predict(X)`` returning a 1-D risk score whose
larger values correspond to higher mortality risk (the IPCW-C convention used
by the challenge).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sksurv.ensemble import RandomSurvivalForest
from sksurv.util import Surv


def make_survival_target(
    durations: pd.Series,
    events: pd.Series,
) -> np.ndarray:
    """Build the sksurv structured-array survival target."""
    return Surv.from_arrays(
        event=events.astype(bool).values,
        time=durations.astype(float).values,
    )


class BaselineModel:
    """v12 baseline: Random Survival Forest on the wide ~1800-feature matrix."""

    def __init__(
        self,
        n_estimators: int = 5000,
        min_samples_leaf: int = 10,
        max_depth: int = 20,
        random_state: int = 42,
        n_jobs: int = 2,
    ) -> None:
        self.rsf_kwargs = dict(
            n_estimators=n_estimators,
            min_samples_leaf=min_samples_leaf,
            max_features="sqrt",
            max_depth=max_depth,
            random_state=random_state,
            n_jobs=n_jobs,
        )
        self.scaler_: StandardScaler | None = None
        self.rsf_: RandomSurvivalForest | None = None
        self.feature_cols_: list[str] | None = None

    def fit(
        self,
        X: pd.DataFrame,
        durations: pd.Series,
        events: pd.Series,
    ) -> "BaselineModel":
        feature_cols = [c for c in X.columns if c != "ID"]
        self.feature_cols_ = feature_cols

        self.scaler_ = StandardScaler()
        X_scaled = self.scaler_.fit_transform(X[feature_cols].values)
        y = make_survival_target(durations, events)

        self.rsf_ = RandomSurvivalForest(**self.rsf_kwargs).fit(X_scaled, y)
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        assert self.scaler_ is not None and self.rsf_ is not None, (
            "Model must be fit before calling predict()."
        )
        X_aligned = X.reindex(columns=["ID"] + self.feature_cols_, fill_value=0)
        X_scaled = self.scaler_.transform(X_aligned[self.feature_cols_].values)
        risk = np.asarray(self.rsf_.predict(X_scaled), dtype=float)
        return pd.Series(risk, index=X_aligned["ID"].values, name="risk_score")


class SpecialistModel:
    """v22 specialist: small RSF (500 trees) on the 23-feature reduced matrix.

    Lower variance than the baseline because the feature set is curated, dense,
    and shared across cohorts. Trained on QRT + a propensity-selected Tazi
    subset (see ``selection.py``); ranks blend complementarily with the
    baseline because the two error patterns are partially independent.
    """

    def __init__(
        self,
        n_estimators: int = 500,
        min_samples_leaf: int = 15,
        max_depth: int = 12,
        random_state: int = 42,
        n_jobs: int = 2,
    ) -> None:
        self.rsf_kwargs = dict(
            n_estimators=n_estimators,
            min_samples_leaf=min_samples_leaf,
            max_features="sqrt",
            max_depth=max_depth,
            random_state=random_state,
            n_jobs=n_jobs,
        )
        self.scaler_: StandardScaler | None = None
        self.rsf_: RandomSurvivalForest | None = None
        self.feature_cols_: list[str] | None = None

    def fit(
        self,
        X: pd.DataFrame,
        durations: pd.Series,
        events: pd.Series,
    ) -> "SpecialistModel":
        feature_cols = [c for c in X.columns if c != "ID"]
        self.feature_cols_ = feature_cols
        self.scaler_ = StandardScaler()
        X_scaled = self.scaler_.fit_transform(X[feature_cols].fillna(0).values)
        y = make_survival_target(durations, events)
        self.rsf_ = RandomSurvivalForest(**self.rsf_kwargs).fit(X_scaled, y)
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        assert self.scaler_ is not None and self.rsf_ is not None, (
            "Model must be fit before calling predict()."
        )
        X_aligned = X.reindex(columns=["ID"] + self.feature_cols_, fill_value=0)
        X_scaled = self.scaler_.transform(X_aligned[self.feature_cols_].fillna(0).values)
        risk = np.asarray(self.rsf_.predict(X_scaled), dtype=float)
        return pd.Series(risk, index=X_aligned["ID"].values, name="risk_score")

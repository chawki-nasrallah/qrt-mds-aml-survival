"""Rank-blending of survival risk scores from two heterogeneous models.

The baseline (v12) outputs are on the RSF-scale ~[218, 2039]; the specialist
(v22) outputs are on a different RSF scale ~[127, 1204]. Raw-score averaging
would let one architecture dominate the blend by scale alone, so we convert
both predictions to ranks and average those.

The optimal alpha was determined by a public-LB sweep: alpha=0.30 (30% weight
on baseline, 70% on specialist) maximized public-LB IPCW-C in the v22 family.
"""
from __future__ import annotations

import pandas as pd


def rank_blend(
    baseline_scores: pd.Series,
    specialist_scores: pd.Series,
    alpha: float = 0.30,
) -> pd.Series:
    """Blend two risk-score series by averaged ranks.

    ``final = alpha * rank(baseline) + (1 - alpha) * rank(specialist)``

    Indices must match (typically by patient ID). Output is on the same ID
    index, named ``risk_score``, in rank-space (1..N; ties averaged).
    """
    if not (0.0 <= alpha <= 1.0):
        raise ValueError(f"alpha must be in [0, 1]; got {alpha}")

    baseline = baseline_scores.copy()
    specialist = specialist_scores.reindex(baseline.index)
    if specialist.isna().any():
        missing = int(specialist.isna().sum())
        raise ValueError(
            f"specialist_scores missing values for {missing} IDs present in baseline_scores"
        )

    blended = alpha * baseline.rank() + (1 - alpha) * specialist.rank()
    blended.name = "risk_score"
    return blended

"""Propensity-based selection of external cohort patients for augmentation.

The challenge train set has ~5% AML; the public test has ~19% AML — a 4x
enrichment of higher-blast patients in the target distribution. To soften
this shift we draw additional patients from the Tazi 2022 NEJM Evidence
cohort (publicly available MDS/AML survival data) and ask which Tazi patients
look most like the QRT high-blast test population.

The mechanism: train a LightGBM classifier to discriminate (QRT high-blast
test, label=1) vs. (Tazi pool, label=0) on the *specialist's raw input
features only* — no engineered scores. The Tazi patients with the highest
predicted propensity are the ones most clinically similar to the underserved
QRT test region. Take the top K=583 (matching the historical v20d count).

Using raw inputs only — not the engineered IPSS-M score, TP53 multihit, or
FLT3-ITD — avoids a feedback loop where the selection criterion is
double-dipping on features the model itself computes. This was the v22
"Option C" refinement over the original v20d general-feature selection,
worth +0.0011 on the public LB.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

from .features import (
    CLINICAL_NUMERICS,
    parse_cytogenetics,
)

warnings.filterwarnings("ignore", category=UserWarning)


PROPENSITY_CYTO = [
    "cyto_normal", "cyto_complex", "cyto_very_complex",
    "cyto_minus7", "cyto_del5q", "cyto_del7q", "cyto_minus17", "cyto_plus8",
]
PROPENSITY_GENES = ["TP53", "NPM1", "FLT3", "RUNX1", "ASXL1", "IDH1", "IDH2"]


def build_propensity_features(
    clinical: pd.DataFrame,
    molecular: pd.DataFrame,
) -> pd.DataFrame:
    """Raw-input feature matrix used by the propensity classifier.

    Returns clinical numerics + curated cytogenetic flags + curated gene flags
    + mutation count. No engineered scores, no IPSS-M, no multihit aggregates.
    """
    feats = clinical[["ID"] + CLINICAL_NUMERICS].copy()
    cyto = clinical["CYTOGENETICS"].apply(parse_cytogenetics).apply(pd.Series)
    for col in PROPENSITY_CYTO:
        feats[col] = cyto[col].values if col in cyto.columns else 0

    mol = molecular.copy()
    mol["GENE_UP"] = mol["GENE"].astype(str).str.upper()
    for g in PROPENSITY_GENES:
        flag = (mol["GENE_UP"] == g).groupby(mol["ID"]).any().astype(int)
        feats[f"gene_{g}"] = feats["ID"].map(flag).fillna(0).astype(int).values

    feats["n_mut"] = feats["ID"].map(molecular.groupby("ID").size()).fillna(0).astype(int)
    return feats


def select_tazi_patients(
    qrt_test_clinical: pd.DataFrame,
    qrt_test_molecular: pd.DataFrame,
    tazi_clinical: pd.DataFrame,
    tazi_molecular: pd.DataFrame,
    *,
    n_select: int = 583,
    high_blast_threshold: float = 20.0,
    n_splits: int = 5,
    random_state: int = 42,
) -> tuple[set[str], float]:
    """Pick Tazi patients most similar to the QRT high-blast test population.

    Returns the set of selected Tazi IDs and the mean cross-validated AUC of
    the propensity model (a sanity check — should be substantially > 0.5
    indicating real distributional difference).
    """
    try:
        import lightgbm as lgb
    except ImportError as e:
        raise ImportError(
            "lightgbm is required for propensity selection. "
            "Install it via `pip install lightgbm`."
        ) from e

    test_aml_mask = qrt_test_clinical["BM_BLAST"].fillna(0) >= high_blast_threshold
    test_aml_clin = qrt_test_clinical[test_aml_mask].reset_index(drop=True)
    test_aml_mol = qrt_test_molecular[qrt_test_molecular["ID"].isin(test_aml_clin["ID"])]

    tazi_pf = build_propensity_features(tazi_clinical, tazi_molecular)
    test_pf = build_propensity_features(test_aml_clin, test_aml_mol)

    joint = pd.concat(
        [
            test_pf.assign(_label=1, _origin="qrt_test_aml"),
            tazi_pf.assign(_label=0, _origin="tazi"),
        ],
        ignore_index=True,
    )
    X = joint.drop(columns=["ID", "_label", "_origin"])
    X = X.fillna(X.median(numeric_only=True))
    y = joint["_label"].values

    oof = np.zeros(len(joint))
    aucs: list[float] = []
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    for train_idx, valid_idx in skf.split(X, y):
        model = lgb.LGBMClassifier(
            n_estimators=300, max_depth=5, num_leaves=15, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_samples=20,
            random_state=random_state, n_jobs=2, verbose=-1,
        )
        model.fit(
            X.iloc[train_idx], y[train_idx],
            eval_set=[(X.iloc[valid_idx], y[valid_idx])],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )
        oof[valid_idx] = model.predict_proba(X.iloc[valid_idx])[:, 1]
        aucs.append(roc_auc_score(y[valid_idx], oof[valid_idx]))

    tazi_mask = joint["_origin"].values == "tazi"
    tazi_scored = joint.loc[tazi_mask].copy()
    tazi_scored["propensity"] = oof[tazi_mask]
    tazi_scored = tazi_scored.sort_values("propensity", ascending=False)
    selected = set(tazi_scored.head(n_select)["ID"].values)

    return selected, float(np.mean(aucs))

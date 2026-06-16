"""End-to-end pipeline: from raw QRT data to the final blended submission.

Steps:
1. Load QRT clinical and molecular train/test.
2. Build the v12 baseline feature matrix and train the RSF baseline.
3. (Optional, requires Tazi data) Run propensity selection to pick ~583
   Tazi patients most similar to the QRT high-blast test population.
4. Build the 23-feature specialist matrix on QRT + selected Tazi, train the
   small RSF specialist.
5. Rank-blend baseline and specialist at alpha=0.30.
6. Write ``outputs/final_submission.csv``.

Usage::

    python -m src.run_pipeline --data-dir /path/to/qrt --output outputs/final_submission.csv

If the Tazi cohort is not available locally, pass ``--no-augmentation`` to
train the specialist on QRT only. This will degrade public-LB performance
relative to the augmented version, but the methodology and code structure
are unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .blend import rank_blend
from .features import build_baseline_features, build_specialist_features
from .models import BaselineModel, SpecialistModel
from .selection import select_tazi_patients


def run(
    data_dir: Path,
    output_path: Path,
    *,
    tazi_dir: Path | None = None,
    alpha: float = 0.30,
) -> pd.Series:
    qrt_clin = pd.read_csv(data_dir / "X_train" / "clinical_train.csv")
    qrt_mol = pd.read_csv(data_dir / "X_train" / "molecular_train.csv")
    qrt_tgt = pd.read_csv(data_dir / "target_train.csv").dropna(subset=["OS_YEARS", "OS_STATUS"])
    qrt_test_clin = pd.read_csv(data_dir / "X_test" / "clinical_test.csv")
    qrt_test_mol = pd.read_csv(data_dir / "X_test" / "molecular_test.csv")

    train_ids = qrt_tgt["ID"].values
    qrt_clin = qrt_clin[qrt_clin["ID"].isin(train_ids)].reset_index(drop=True)
    qrt_mol = qrt_mol[qrt_mol["ID"].isin(train_ids)].reset_index(drop=True)

    print(f"Loaded QRT train: {len(qrt_clin)} patients | test: {len(qrt_test_clin)} patients")

    print("\n[1/4] Baseline (v12): building features and training RSF (5000 trees)...")
    X_train_base, imputer = build_baseline_features(qrt_clin, qrt_mol, fit_imputer=True)
    X_test_base, _ = build_baseline_features(
        qrt_test_clin, qrt_test_mol, fit_imputer=False, imputer=imputer,
    )
    X_train_base = X_train_base.merge(qrt_tgt[["ID", "OS_YEARS", "OS_STATUS"]], on="ID")
    baseline = BaselineModel().fit(
        X_train_base.drop(columns=["OS_YEARS", "OS_STATUS"]),
        durations=X_train_base["OS_YEARS"],
        events=X_train_base["OS_STATUS"],
    )
    baseline_scores = baseline.predict(X_test_base)
    print(f"  baseline scores range: [{baseline_scores.min():.2f}, {baseline_scores.max():.2f}]")

    print("\n[2/4] Specialist (v22): building 23-feature reduced matrix...")
    qrt_train_spec = build_specialist_features(qrt_clin, qrt_mol)
    test_spec = build_specialist_features(qrt_test_clin, qrt_test_mol)

    if tazi_dir is not None and tazi_dir.exists():
        print(f"\n[3/4] Propensity selection on Tazi cohort at {tazi_dir}...")
        tazi_clin = pd.read_csv(tazi_dir / "clinical.csv")
        tazi_mol = pd.read_csv(tazi_dir / "molecular.csv")
        tazi_tgt = pd.read_csv(tazi_dir / "target.csv").dropna(subset=["OS_YEARS", "OS_STATUS"])
        selected_ids, mean_auc = select_tazi_patients(
            qrt_test_clin, qrt_test_mol, tazi_clin, tazi_mol,
        )
        print(f"  propensity model mean OOF AUC: {mean_auc:.4f}")
        print(f"  selected {len(selected_ids)} Tazi patients")
        tazi_sel_clin = tazi_clin[tazi_clin["ID"].isin(selected_ids)].reset_index(drop=True)
        tazi_sel_mol = tazi_mol[tazi_mol["ID"].isin(selected_ids)].reset_index(drop=True)
        tazi_sel_tgt = tazi_tgt[tazi_tgt["ID"].isin(selected_ids)].reset_index(drop=True)
        tazi_spec = build_specialist_features(tazi_sel_clin, tazi_sel_mol)
        train_spec = (
            pd.concat([qrt_train_spec, tazi_spec], ignore_index=True)
            .merge(
                pd.concat([qrt_tgt, tazi_sel_tgt], ignore_index=True)[
                    ["ID", "OS_YEARS", "OS_STATUS"]
                ],
                on="ID",
            )
        )
    else:
        print("\n[3/4] No Tazi data provided — training specialist on QRT only.")
        train_spec = qrt_train_spec.merge(qrt_tgt[["ID", "OS_YEARS", "OS_STATUS"]], on="ID")

    print(f"  specialist training set: {len(train_spec)} patients, 23 features")
    specialist = SpecialistModel().fit(
        train_spec.drop(columns=["OS_YEARS", "OS_STATUS"]),
        durations=train_spec["OS_YEARS"],
        events=train_spec["OS_STATUS"],
    )
    specialist_scores = specialist.predict(test_spec)

    print(f"\n[4/4] Rank-blending baseline + specialist at alpha={alpha}...")
    final = rank_blend(baseline_scores, specialist_scores, alpha=alpha)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.rename_axis("ID").to_csv(output_path)
    print(f"\nWrote {output_path} ({len(final)} rows).")
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, required=True,
        help="QRT challenge data directory (containing X_train/, X_test/, target_train.csv).",
    )
    parser.add_argument(
        "--tazi-dir", type=Path, default=None,
        help="Optional Tazi cohort directory (clinical.csv, molecular.csv, target.csv).",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/final_submission.csv"),
        help="Destination CSV path for the blended submission.",
    )
    parser.add_argument(
        "--alpha", type=float, default=0.30,
        help="Blend weight: alpha*baseline + (1-alpha)*specialist (default 0.30).",
    )
    args = parser.parse_args()
    run(args.data_dir, args.output, tazi_dir=args.tazi_dir, alpha=args.alpha)


if __name__ == "__main__":
    main()

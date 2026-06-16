"""Generate data-distribution figures for the slides.

Outputs PNGs to ``docs/figures/``. Reads from the QRT challenge data directory
(adjust ``DATA`` below to point at your local copy).

Run: python docs/_build_plots.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

DATA = Path(r"c:/Users/User/Desktop/PHD work/JobSearch/Challenges/qrt data science/Oncology")
OUT = Path(__file__).parent / "figures"
OUT.mkdir(exist_ok=True)

CHARCOAL = "#1A1A1F"
GRAY = "#555A66"
LIGHT_GRAY = "#C9CDD4"
TRAIN_C = "#3B6FA5"
TEST_C = "#B83A3A"
ACCENT = "#B83A3A"
MDS_C = "#3B6FA5"
AML_C = "#B83A3A"
BG = "#FAFAF8"

plt.rcParams.update({
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "axes.edgecolor": GRAY,
    "axes.labelcolor": CHARCOAL,
    "axes.titlecolor": CHARCOAL,
    "axes.titleweight": "bold",
    "axes.titlesize": 14,
    "axes.labelsize": 12,
    "xtick.color": GRAY,
    "ytick.color": GRAY,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.family": "Calibri",
    "font.size": 12,
    "legend.frameon": False,
    "legend.fontsize": 11,
})


clin_tr = pd.read_csv(DATA / "X_train" / "clinical_train.csv")
clin_te = pd.read_csv(DATA / "X_test" / "clinical_test.csv")
tgt = pd.read_csv(DATA / "target_train.csv").dropna(subset=["OS_YEARS", "OS_STATUS"])

train = clin_tr.merge(tgt[["ID", "OS_YEARS", "OS_STATUS"]], on="ID", how="inner")
train["is_aml"] = (train["BM_BLAST"].fillna(0) >= 20).astype(int)
clin_te["is_aml"] = (clin_te["BM_BLAST"].fillna(0) >= 20).astype(int)


def kaplan_meier(durations: np.ndarray, events: np.ndarray):
    """Return Kaplan-Meier estimator (times, survival) as right-continuous steps."""
    order = np.argsort(durations)
    t = np.asarray(durations)[order]
    e = np.asarray(events)[order].astype(int)
    times = [0.0]
    surv = [1.0]
    n_at_risk = len(t)
    i = 0
    while i < len(t):
        j = i
        while j < len(t) and t[j] == t[i]:
            j += 1
        deaths = e[i:j].sum()
        if deaths > 0 and n_at_risk > 0:
            surv.append(surv[-1] * (1 - deaths / n_at_risk))
            times.append(t[i])
        n_at_risk -= (j - i)
        i = j
    return np.array(times), np.array(surv)


def plot_cohort_composition():
    fig, ax = plt.subplots(figsize=(6.5, 4.6), dpi=200)

    train_pct = train["is_aml"].mean() * 100
    test_pct = clin_te["is_aml"].mean() * 100
    train_pct_mds = 100 - train_pct
    test_pct_mds = 100 - test_pct

    labels = [f"Train\nn = {len(train):,}", f"Public test\nn = {len(clin_te):,}"]
    x = np.arange(len(labels))
    width = 0.55

    ax.bar(x, [train_pct_mds, test_pct_mds], width, color=MDS_C, alpha=0.85,
           label="MDS  (BM blast < 20%)")
    ax.bar(x, [train_pct, test_pct], width,
           bottom=[train_pct_mds, test_pct_mds], color=AML_C,
           label="AML  (BM blast ≥ 20%)")

    for i, (mds_v, aml_v) in enumerate([(train_pct_mds, train_pct), (test_pct_mds, test_pct)]):
        if mds_v > 8:
            ax.text(x[i], mds_v / 2, f"{mds_v:.0f}%", ha="center", va="center",
                    color="white", fontweight="bold", fontsize=14)
        if aml_v >= 4:
            ax.text(x[i], mds_v + aml_v / 2, f"{aml_v:.0f}%", ha="center", va="center",
                    color="white", fontweight="bold", fontsize=12)
        else:
            ax.annotate(f"{aml_v:.0f}%",
                        xy=(x[i], mds_v + aml_v),
                        xytext=(x[i] + 0.35, mds_v + aml_v - 3),
                        fontsize=12, color=AML_C, fontweight="bold",
                        arrowprops=dict(arrowstyle="-", color=AML_C, lw=0.8))

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Share of cohort")
    ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_ylim(0, 105)
    ax.set_title("Cohort composition", pad=10)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.30), ncol=2, frameon=False)

    fig.tight_layout()
    fig.savefig(OUT / "cohort_composition.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote cohort_composition.png  (train AML = {train_pct:.1f}%, test AML = {test_pct:.1f}%)")


def plot_bm_blast_density():
    fig, ax = plt.subplots(figsize=(6.5, 4.2), dpi=200)
    bins = np.linspace(0, 100, 41)

    tr_v = train["BM_BLAST"].dropna().clip(0, 100).values
    te_v = clin_te["BM_BLAST"].dropna().clip(0, 100).values
    tr_miss = len(train) - len(tr_v)
    te_miss = len(clin_te) - len(te_v)

    ax.hist(tr_v, bins=bins, density=True, alpha=0.60, color=TRAIN_C,
            label=f"Train  ({len(tr_v):,} observed,  {tr_miss} missing)",
            edgecolor="white", linewidth=0.5)
    ax.hist(te_v, bins=bins, density=True, alpha=0.55, color=TEST_C,
            label=f"Public test  ({len(te_v):,} observed,  {te_miss} missing)",
            edgecolor="white", linewidth=0.5)

    ax.axvline(20, color=GRAY, linestyle="--", linewidth=1.0)
    ymax = ax.get_ylim()[1]
    ax.text(20.5, ymax * 0.88, "AML threshold\n(blast ≥ 20%)", fontsize=10, color=GRAY)

    ax.set_xlabel("Bone marrow blast %")
    ax.set_ylabel("Density")
    ax.set_title("Bone-marrow blast distribution", pad=10)
    ax.set_xlim(0, 100)
    ax.legend(loc="upper right")

    fig.tight_layout()
    fig.savefig(OUT / "bm_blast_density.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote bm_blast_density.png  (train median = {np.median(tr_v):.1f}%, test median = {np.median(te_v):.1f}%)")


def plot_survival_by_cohort():
    fig, ax = plt.subplots(figsize=(6.5, 4.2), dpi=200)

    mds = train[train["is_aml"] == 0]
    aml = train[train["is_aml"] == 1]

    for cohort_df, color, label in [
        (mds, MDS_C, f"MDS  (n = {len(mds):,})"),
        (aml, AML_C, f"AML  (n = {len(aml):,})"),
    ]:
        t, s = kaplan_meier(cohort_df["OS_YEARS"].values, cohort_df["OS_STATUS"].astype(int).values)
        ax.step(t, s, where="post", color=color, linewidth=2.0, label=label)

    ax.set_xlim(0, 8)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("Years from diagnosis")
    ax.set_ylabel("Estimated survival  S(t)")
    ax.set_title("Survival in train by cohort", pad=10)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.legend(loc="upper right")
    ax.grid(True, axis="y", alpha=0.25, linestyle=":")

    fig.tight_layout()
    fig.savefig(OUT / "survival_by_cohort.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    med_mds_event = mds[mds["OS_STATUS"] == 1]["OS_YEARS"].median()
    med_aml_event = aml[aml["OS_STATUS"] == 1]["OS_YEARS"].median()
    print(f"Wrote survival_by_cohort.png  (median time-to-death: MDS = {med_mds_event:.2f}y, AML = {med_aml_event:.2f}y)")


def plot_feature_kdes():
    """Optional: small multiples of WBC, HB, PLT train vs test."""
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.0), dpi=200)
    cols = [
        ("WBC", "White blood cell count (×10⁹/L)", (0, 100)),
        ("HB", "Hemoglobin (g/dL)", (3, 18)),
        ("PLT", "Platelets (×10⁹/L)", (0, 500)),
    ]
    for ax, (col, label, xlim) in zip(axes, cols):
        tr_v = train[col].dropna().clip(*xlim).values
        te_v = clin_te[col].dropna().clip(*xlim).values
        bins = np.linspace(xlim[0], xlim[1], 30)
        ax.hist(tr_v, bins=bins, density=True, alpha=0.6, color=TRAIN_C,
                label="Train", edgecolor="white", linewidth=0.5)
        ax.hist(te_v, bins=bins, density=True, alpha=0.55, color=TEST_C,
                label="Test", edgecolor="white", linewidth=0.5)
        ax.set_xlabel(label)
        ax.set_xlim(*xlim)
        if ax is axes[0]:
            ax.set_ylabel("Density")
            ax.legend(loc="upper right")
        ax.set_title(col, pad=6)

    fig.tight_layout()
    fig.savefig(OUT / "feature_kdes.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("Wrote feature_kdes.png")


if __name__ == "__main__":
    plot_cohort_composition()
    plot_bm_blast_density()
    plot_survival_by_cohort()
    plot_feature_kdes()
    print("\nAll figures written to docs/figures/")

# Survival Prediction under Distribution Shift

*[QRT Data Challenge — Overall Survival Prediction for patients diagnosed with Myeloid Leukemia](https://challengedata.ens.fr/)*

> Public LB **0.7685 (rank 11/743)**
> Private LB **0.7124 (rank 189)** — and the gap between those two numbers is the most interesting thing in this repo.

This was my first online data-science competition: the QRT Data Challenge in Oncology, a survival-prediction task in Myelodysplastic Syndromes (MDS) and Acute Myeloid Leukemia (AML). The repo documents the modelling pipeline I submitted, the methodology behind it, and — most importantly — what I learned.

---

## 1. The problem

Predict overall survival in a cohort of MDS/AML patients from clinical and genetic features. Metric: **IPCW-C** (Inverse-Probability-of-Censoring-Weighted concordance index) — a censoring-aware variant of Harrell's C that ranks patients by predicted risk and rewards correct pairwise orderings.

**Data:**

- Clinical: blood counts (BM blast %, WBC, hemoglobin, platelets), cytogenetics string (ISCN format)
- Molecular: per-mutation records — gene, protein change, effect (missense, nonsense, ITD, …), variant allele frequency
- Target: (`OS_YEARS`, `OS_STATUS`) — time to death or censoring

**Constraint:** 2 leaderboard submissions per day. This matters for the story.

## 2. Distribution shift was the central problem

The first non-trivial thing I noticed was that train and test were drawn from _different_ mixtures:

| Cohort      | MDS share | AML share | Notes                                                        |
| ----------- | --------- | --------- | ------------------------------------------------------------ |
| Train       | 92%       | 5%        | bulk of patients are lower-risk MDS                          |
| Public test | 71%       | 19%       | ~4× AML enrichment — the high-blast, high-mortality fraction |

This is a covariate shift problem masquerading as a survival problem. A model fit faithfully to the training distribution underweights the patients the leaderboard cares most about. I called this the **KYW shift** (after the test-set ID prefix).

The methodology described in [docs/methodology.md](docs/methodology.md) is structured around adapting to this shift without overfitting to the public test set's specific composition. The post-mortem in [docs/results.md](docs/results.md) explains why that last clause was harder than I thought.

## 3. The pipeline (two layered models)

The final submission is a **rank-blend of two survival models** with complementary error patterns:

```
final_risk = 0.30 · rank(baseline) + 0.70 · rank(specialist)
```

### Baseline (v12) — wide feature matrix, single Random Survival Forest

A Random Survival Forest (5000 trees) trained on a ~1800-feature matrix that combines:

- Clinical numerics (log-transformed, MICE-imputed)
- Per-gene one-hot encodings weighted by max VAF
- Cytogenetic features via two parsers: a broad regex parser (one-hot every abnormality) and a targeted parser (IPSS-R/IPSS-M flags)
- EFFECT and PROTEIN_CHANGE residue-position one-hots
- A dense **IPSS-M score** feature, computed from the Bernard et al. 2022 NEJM Evidence formula
- Two interaction flags: NPM1+/FLT3−, and TP53+ × complex-karyotype

**Public LB: 0.7637.** Code: [src/features.py](src/features.py) (`build_baseline_features`) and [src/models.py](src/models.py) (`BaselineModel`).

RSF was selected over linear Cox PH (~0.01+ LB worse), Gradient Boosting Survival Analysis (~0.004 LB worse) and ExtraSurvivalTrees (~0.010 LB worse) by direct empirical comparison on this feature set. The choice is also supported by literature precedent — Tazi et al. 2022 (*Nat. Commun.*) uses RSF for AML stratification on the same external cohort we augment with, and RSF has become the standard tree-based survival method in haematology prognostic modelling since Ishwaran et al. 2008. The Cox-based IPSS-M formula (Bernard et al. 2022, *NEJM Evidence*) enters the matrix as a single curated `ipssm_score` feature rather than as the architecture itself — capturing the validated linear prognostic signal *and* allowing RSF to learn non-linear corrections on top. Full justification in [docs/methodology.md](docs/methodology.md).

### Specialist (v22) — reduced feature set + cross-cohort augmentation

A small RSF (500 trees) trained on a 23-feature **biologically robust** matrix:

- Clinical numerics
- 8 curated cytogenetic flags (the ones used in clinical IPSS-R scoring)
- 7 curated gene flags
- Engineered scores: `ipssm_score`, `tp53_multihit`, `flt3_itd`, `n_mut`

Crucially, the specialist is trained on **QRT + 583 patients from the Tazi 2022 NEJM Evidence MDS cohort**. The Tazi patients are selected by a LightGBM **propensity classifier** trained to discriminate "QRT high-blast test patients" vs. "all Tazi patients" — keeping the Tazi patients most similar to the underserved high-blast region of the QRT test distribution.

A subtle but important refinement: the propensity classifier uses _raw inputs only_ (clinical, cytogenetic, gene-presence flags), **not** engineered features like `ipssm_score`. This breaks a double-dipping feedback loop where selection criteria would otherwise correlate with the specialist's own scoring system.

**Public LB: 0.7645 (specialist alone) → 0.7685 (blended at α=0.30).** Code: [src/features.py](src/features.py) (`build_specialist_features`), [src/selection.py](src/selection.py), [src/models.py](src/models.py) (`SpecialistModel`), [src/blend.py](src/blend.py).

### Why rank-blending (not raw)?

The two models live on incompatible scales: baseline ~[218, 2039], specialist ~[127, 1204]. Raw averaging would let one architecture dominate by scale alone. Converting to ranks makes the blend scale-invariant and lets the IPCW-C metric (which cares only about pairwise ordering) reward complementarity directly.

### Ablation: each piece earning its keep

Three ways to read the contribution of the specialist and the cross-cohort augmentation:

| Variant | Specialist training | Blend α | Public LB | Δ vs v12 |
|---|---|---:|---:|---:|
| v12 baseline alone | — | — | 0.7637 | — |
| Specialist alone | QRT + Tazi-583 | (no blend) | 0.7645 | +0.0008 |
| Specialist + v12 blend, **no augmentation** | QRT only | 0.50 | **0.7584** | **−0.0053** |
| Specialist + v12 blend, with augmentation | QRT + Tazi-583 | 0.50 | 0.7674 | +0.0037 |
| **Final submission** (v22, Option C selection) | QRT + Tazi-583 | 0.30 | **0.7685** | **+0.0048** |

Two clean apples-to-apples points:

- **The data augmentation is what makes the specialist useful in the blend.** At identical blend weight (α=0.50), augmenting the specialist's training set with the 583 propensity-selected Tazi patients moves the blended public LB from **0.7584 → 0.7674 (+0.0090)**. Without augmentation, the specialist actively *hurts* the blend (−0.0053 below v12 alone).
- **The specialist alone (0.7645) is barely better than v12 alone (0.7637).** The win is in the *complementarity* — specialist and baseline make different errors, and the blend keeps the right one most of the time.

## 4. Results

**Public leaderboard:** rank **11 / 743** with IPCW-C **0.7685** — within 0.0054 of the top public score.

**Private leaderboard:** rank **189** with IPCW-C **0.7124**. For reference, the organisers' baseline benchmark scored 0.6411 on private (rank 678) — my submission beat the benchmark by **+0.0713**.

The private/public gap of −0.0561 looks bad in isolation — but the entire leaderboard shook up. Top public dropped from 0.7739 → top private 0.7231, a 5-point C-index collapse across the board. Full breakdown in [docs/results.md](docs/results.md).

## 5. What I learned (the real point of this writeup)

This was my first online data-science competition. The public/private divergence taught me something I'd only read about: **late-stage optimization against a public test set is itself a form of overfitting**, even when each iteration shows a "real" gain on the held-out leaderboard score.

My final 10+ submissions chased ~0.001 improvements on public LB. The "improvements" I was getting late-stage were partly real and partly an encoding of the specific composition of the public test set.

**What I'd do differently next time:**

1. Weight cross-validation more heavily than LB once per-iteration deltas on the public LB drop into the noise band (< ~0.003 here).
2. Pick a final submission earlier and not let yourself nudge it for marginal LB gains.
3. Use the public LB as a **sanity check**, not an optimization target.

The shake-up is a teachable moment, not an excuse — the methodology in this repo is the same methodology I'd defend in a job interview. The lesson is about _when to stop iterating_, not about the iterations themselves.

## 6. Repository structure

```
.
├── README.md                  ← you are here
├── docs/
│   ├── methodology.md         ← detailed walkthrough of the modelling approach
│   ├── results.md             ← full LB results + shake-up post-mortem
│   ├── slides.pdf             ← self-explanatory presentation deck
│   └── slides.pptx            ← editable version of the deck
├── src/
│   ├── features.py            ← feature engineering (baseline + specialist)
│   ├── models.py              ← BaselineModel (v12), SpecialistModel (v22)
│   ├── selection.py           ← Tazi propensity selection
│   ├── blend.py               ← rank-blending
│   └── run_pipeline.py        ← end-to-end orchestrator
├── outputs/
│   └── final_submission.csv   ← the actual submitted file
├── data/
│   └── README.md              ← how to obtain the challenge data
├── requirements.txt
└── LICENSE
```

## 7. Reproducing

```bash
pip install -r requirements.txt
python -m src.run_pipeline --data-dir /path/to/qrt-data --output outputs/final_submission.csv
```

Add `--tazi-dir /path/to/tazi-cohort` to enable cross-cohort augmentation. Without it, the specialist trains on QRT only, which gives a slightly lower public-LB score but lets you reproduce the full pipeline without the external cohort. See [data/README.md](data/README.md) for data access.

## References

- Bernard, E. et al. (2022). _Molecular International Prognostic Scoring System for Myelodysplastic Syndromes._ **NEJM Evidence** 1(7).
- Tazi, Y. et al. (2022). _Unified classification and risk-stratification in Acute Myeloid Leukemia._ **Nature Communications** 13.
- Greenwell, B. et al. _scikit-survival_: A Python library for survival analysis built on top of scikit-learn.

## Author

Chawki Nasrallah — PhD Researcher (Biomedical Engineering, AI/Data science in Health). Contact: chawki.gnasrallah@gmail.com.

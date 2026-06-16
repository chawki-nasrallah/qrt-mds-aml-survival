# Methodology

A deeper walkthrough of the modelling approach. Companion to the top-level [README.md](../README.md); reads best after it.

## 1. Framing: this is a covariate-shift problem in disguise

The competition is framed as survival prediction, but the binding constraint isn't the survival model — it's the gap between training and test distributions.

| Cohort      | n     | MDS share | AML share | BM blast median |
|-------------|-------|-----------|-----------|-----------------|
| Train       | 3173  | 92%       | 5%        | low             |
| Public test | 1193  | 71%       | 19%       | substantially higher |

The test set has ~4× the AML representation of train. AML patients have higher BM blasts, more TP53 mutations, more complex karyotypes — and they account for a disproportionate share of pairwise comparisons in the IPCW-C metric because they die earlier. A model fit faithfully to the training distribution underweights exactly the patients the leaderboard cares about most.

I call this the **KYW shift** after the test ID prefix.

### What I tried and ruled out

A long sequence of training-distribution adjustments (sample reweighting, phenotype-weighted loss, far-patient removal, far-center removal, density-ratio drop) all came back null or marginal — the covariate-shift literature's standard remedies didn't move the leaderboard. The shift is real but not the kind that survives standard reweighting fixes. My working interpretation is that the shift is in the joint (X, T) distribution including the survival times, not in marginal feature distributions alone — and reweighting can only correct the marginal piece.

This is what eventually pushed me toward **cross-cohort augmentation** (Tazi) rather than internal reweighting: pull in extra patients from the high-blast region rather than try to upweight what little of it I already have.

## 2. The baseline (v12): wide features, single Random Survival Forest

### Feature engineering

The baseline uses a wide ~1800-column matrix that combines five sources:

1. **Clinical numerics** (BM blast, WBC, hemoglobin, platelets, ANC, monocytes when present). Log-transformed (`log1p`) to compress right-skewed distributions. Missing values imputed with `IterativeImputer` over a small RandomForest.

2. **Per-gene presence weighted by VAF.** For each gene observed in `molecular_train.csv`, one column with the **maximum** VAF observed for that patient (0 if absent). This captures both "did this gene mutate?" and "with what allelic burden?".

3. **Cytogenetic features from two parsers** (see [src/features.py](../src/features.py)):
   - `parse_cytogenetics_broad`: a permissive regex one-hot that extracts every numeric +/−, every translocation `t(c1;c2)`, every inversion, and tags `Complex_Karyotype` if any clone has ≥3 abnormalities. This is intentionally noisy — it surfaces rare lesions that the targeted parser misses.
   - `parse_cytogenetics`: a curated parser producing IPSS-R/IPSS-M-aligned flags (monosomy 7, del(5q), del(7q), trisomy 8, i(17q), …). Higher precision, lower recall than the broad parser.

4. **EFFECT and PROTEIN_CHANGE one-hots.** Mutation type (missense, nonsense, frameshift, ITD, PTD, …) and residue position (extracted from HGVS strings like `p.R882H` → `882`) one-hot per patient.

5. **IPSS-M score as a single dense feature.** The Bernard et al. 2022 NEJM Evidence prognostic formula, with the corrected cytogenetic group calculation that was wrong in an earlier iteration:

```
score ∝ wHB · (HB − μHB)
      + wPLT · (transf_PLT − μPLT)
      + wBLAST · (BLAST5 − μBLAST)
      + wCYTO · (cyto_group − μcyto)
      + wTP53m · TP53_multihit
      + ... (per-gene contributions)
```

Implementation: `src/features.py::compute_ipssm_score`. This single feature carries a disproportionate share of the signal — in a tree-ensemble feature-importance analysis from one mid-iteration model, `ipssm_score` alone accounted for ~42% of the total importance.

### Architecture

A single Random Survival Forest:

- 5000 trees
- `min_samples_leaf=10`, `max_depth=20`, `max_features='sqrt'`
- `random_state=42` for reproducibility

```
v12_score = RSF.predict(X)
```

**Public LB: 0.7637.**

### Why a Random Survival Forest? (architecture selection)

The choice combined empirical comparison on this feature set with literature precedent in MDS/AML survival modelling.

**Alternatives tried and rejected (empirical):**

- **Linear models (Cox proportional-hazards with elastic-net regularisation)** — underperformed by ≈ 0.01+ LB. The feature matrix is wide and sparse (≈ 1,800 columns from gene-VAF and cytogenetic one-hots), and the linearity / proportional-hazards assumptions don't capture the multiplicative gene-gene and gene-cytogenetic interactions that dominate AML prognosis (e.g. TP53 × complex karyotype, NPM1+/FLT3−).
- **Gradient Boosting Survival Analysis** (Cox loss, sksurv) — came in ≈ 0.004 LB worse on the same feature set. The wide one-hot matrix penalises gradient-based optimisation: many columns are near-zero with high-VAF outliers, which gradient steps push into low-importance noise rather than treating as structured signal.
- **ExtraSurvivalTrees** — came in ≈ 0.010 LB worse. The extra randomisation in split selection blurs the meaningful clinical split points (e.g. BM blast ≥ 20%) that a standard RSF criterion identifies cleanly.
- **Random Survival Forest** — selected.

**Why RSF suits this problem (theoretical):**

- Handles mixed feature types — clinical numerics, categorical cytogenetic flags, sparse one-hot gene presence, engineered scores — uniformly through tree splits.
- Captures non-linear interactions natively through tree depth; no need to hand-engineer cross terms.
- Robust to outliers in blood counts (extreme WBC, blast %) that would pull a regression-style fit.
- Reasonable missing-value handling through forest aggregation, even when individual trees see slightly different surrogate splits.

**Literature precedent:**

- **Tazi et al. 2022** (*Nat. Commun.*) uses Random Survival Forest as the core method for AML risk stratification — on the same external cohort we use for augmentation. Direct precedent.
- **Bernard et al. 2022** (*NEJM Evidence*) defines the IPSS-M scoring formula as a Cox proportional-hazards model. We don't use it as our architecture, but we incorporate its formula as a single dense `ipssm_score` feature — so the linear prognostic signal validated over decades of MDS research enters the RSF as one curated input, and tree-learned non-linear corrections sit on top.
- **Ishwaran et al. 2008** (*Ann. Appl. Stat.*) — original RSF paper. The method has since become the standard tree-based survival approach in haematology prognostic modelling, including most recent MDS/AML risk-stratification studies.

The combined design — **RSF architecture with the Cox-based IPSS-M formula as a curated dense feature** — captures both: the linear prognostic signal validated by 30+ years of MDS/AML research, plus the tree-learned non-linear residuals from the wider feature set that no fixed linear formula could express.

## 3. The specialist (v22): reduced features, cross-cohort augmentation

The specialist is a deliberate contrast to the baseline: fewer features, more patients (via augmentation), and a clinical-meaning bias in feature choice.

### 23 features, all with direct prognostic meaning

```python
features = (
    ["BM_BLAST", "WBC", "HB", "PLT"]                                    # clinical
    + ["cyto_normal", "cyto_complex", "cyto_very_complex",              # cyto: IPSS-R
       "cyto_minus7", "cyto_del5q", "cyto_del7q",
       "cyto_minus17", "cyto_plus8"]
    + [f"gene_{g}" for g in                                              # curated genes
       ["TP53", "NPM1", "FLT3", "RUNX1", "ASXL1", "IDH1", "IDH2"]]
    + ["tp53_multihit", "flt3_itd", "ipssm_score", "n_mut"]              # engineered
)
```

Every feature here is something a hematologist would expect to influence prognosis in MDS/AML — no sparse one-hots, no high-cardinality residue positions, no automatically-extracted features that might be data-artifact rather than biology.

### Why fewer features can help in this setting

The baseline's wide matrix gives the RSF lots of room to memorize training-specific patterns. On a 92%-MDS training set, that's largely MDS-specific memorization. When the test distribution shifts toward AML, those patterns don't transfer.

The specialist is more *biased* (in the bias-variance sense) — it can't memorize the same way. Its predictions on AML patients look like its predictions on similar MDS patients with the same dense risk markers, which is closer to the "right" cross-distributional behavior.

Empirically: specialist alone scored 0.7645 public LB, well below the baseline's 0.7637 + the small specialist-blend addition. But when **blended** with the baseline, it added a complementary error signal that the baseline alone couldn't capture.

### Cross-cohort augmentation via propensity selection

The specialist trains on QRT (3173 patients) **plus** 583 selected patients from the [Tazi 2022 NEJM Evidence](https://www.nature.com/articles/s41467-022-31419-9) MDS/AML cohort. The selection logic is a propensity classifier:

1. Take all QRT *test* patients with BM blast ≥ 20% (the high-blast region the leaderboard cares about), label them `1`.
2. Take all Tazi patients, label them `0`.
3. Train LightGBM 5-fold to discriminate them on the 20 raw input features.
4. Sort Tazi patients by predicted P(label=1), keep the top 583.

**Why K = 583?** The number is chosen to match the public test set's AML share. QRT train has ~5% AML (≈156 / 3173 patients); the public test has ~19% AML. Adding the top 583 high-blast Tazi patients to QRT lifts the augmented training cohort's AML share to (156 + 583) / (3173 + 583) ≈ 19.7% — the same regime as the test set. So the specialist trains on a cohort whose AML representation roughly matches what it will be scored against.

**The "Option C" refinement:** the propensity classifier uses *raw* features (clinical numerics + cytogenetic flags + gene presence) only — **not** engineered features like `ipssm_score`, `tp53_multihit`, or `flt3_itd`. This avoids a feedback loop where selection criteria correlate with the specialist's own scoring system, which would narrow the selected distribution toward Tazi patients whom the specialist already scores similarly to its target.

Code: [src/selection.py](../src/selection.py)::`select_tazi_patients`. Propensity model OOF AUC: ~0.87 (the distributions are genuinely different, so the selection is meaningful).

### Why the augmentation alone fails but the blended specialist wins

Direct addition of Tazi patients to the *baseline* training set ("v20d") cost −0.0033 on the leaderboard. The baseline's wide feature matrix has structural mismatches across cohorts (Tazi has different gene assay coverage, different EFFECT vocabulary, different protein-change conventions) and the noise from missing/zero-padded columns drowns the signal.

The specialist's 23-feature matrix is **shared across cohorts** by construction — every column is either a clinical lab value or a clean binary flag derivable from either dataset. The cohort merge is invisible to the model in feature-space.

Then the blend captures both pieces of information:

```
final = 0.30 · rank(baseline_v12) + 0.70 · rank(specialist_v22)
```

- Baseline has rich within-QRT signal but transfers poorly across cohorts.
- Specialist has noisier within-QRT signal but transfers cleanly to augmented data.
- Their errors are partially independent — the blend is better than either alone.

### Ablation: isolating the augmentation effect

I have public-LB scores for each of these variants:

| Variant | Specialist training set | Blend α | Public LB |
|---|---|---:|---:|
| v12 baseline alone | — | — | 0.7637 |
| Specialist alone | QRT + Tazi-583 | (no blend) | 0.7645 |
| Specialist + v12 blend, **no augmentation** | QRT only | 0.50 | 0.7584 |
| Specialist + v12 blend, with augmentation | QRT + Tazi-583 | 0.50 | 0.7674 |
| Final submission (v22, Option C) | QRT + Tazi-583 | 0.30 | 0.7685 |

Three observations the table supports:

1. **The augmentation is the load-bearing element.** At identical blend weight (α=0.50), augmenting the specialist with 583 Tazi patients moves the public LB from 0.7584 → 0.7674 — a **+0.0090** swing from the augmentation alone.
2. **Without augmentation, the specialist is harmful in blend.** Trained on QRT only, the specialist drags the blend below the v12 baseline (0.7584 vs. 0.7637). It contributes noise the baseline doesn't have, and that noise dominates whatever complementarity there might be.
3. **The specialist alone (0.7645) is only marginally better than v12 alone (0.7637).** The +0.0048 final-submission gain over v12 is mostly *complementarity in the blend*, not the specialist's standalone strength.

The α=0.50 vs α=0.30 difference is a confound for the final-row comparison (best-α moves between configurations because the optimal blend weight depends on how good the specialist is). The α=0.50 row pair is the cleanly controlled apples-to-apples ablation: same architecture, same blend weight, only the augmentation varies.

## 4. Rank-blending: why ranks, not raw scores

The RSF in the baseline produces scores in roughly [218, 2039]; the RSF in the specialist produces scores in roughly [127, 1204]. Raw averaging would let the baseline dominate by scale alone — a 0.5/0.5 raw blend would in practice be ~0.7 baseline by influence on the IPCW-C metric.

Converting both predictions to ranks (via `pandas.Series.rank()`) makes the blend scale-invariant. Since IPCW-C cares only about pairwise orderings, rank-blending is also the natural geometry of the metric.

The blend coefficient α was swept on the public LB across {0.30, 0.40, 0.50, 0.65, 0.75, 0.85, 0.90}. α = 0.30 gave the best public score; the gradient in the sweep was monotone-improving toward more specialist weight.

(In hindsight, the gradient was probably an artifact — see the [post-mortem in results.md](results.md). But at the time, the sweep was a defensible procedure.)

## 5. What got tried and dropped

Things I built that didn't make the final pipeline, briefly, so the methodology is honest:

- **Split-cohort modelling** (separate models for AML vs MDS, then blend by `is_aml` indicator): rejected at −0.0062 LB. The cohort boundary is too soft to gate cleanly.
- **AlphaMissense pathogenicity features** (50 per-gene AM scores joined onto the molecular file): rejected at −0.0023 LB. Cohort had hg19 coordinates; AM was hg38. Even after liftover, the AM signal was confounded with the corrected IPSS-M score.
- **ExtraSurvivalTrees** as a third architecture: rejected at −0.0104. The wide one-hot matrix punishes the ET's random-split bias.
- **Beat-AML cohort augmentation**: rejected. Beat-AML's clinical schema is too different (treatment-arm patients, different lab references) — the propensity selector pushed mass into a clinical region that hurt rather than helped.
- **Direct Tazi addition to the baseline** (v20d): rejected at −0.0033. The cross-cohort schema mismatch killed the baseline's high-dim features.

The full iteration history is not in this repo (it's iteration sprawl that obscures rather than clarifies), but the *informative* failures above are kept in this writeup so the methodology is reproducible and defensible.

## 6. Validation strategy and its limits

I used **5-fold cross-validation** on QRT-train for every model variant. The CV-LB calibration on the baseline was:

| Architecture     | CV score | LB score | Calibration gap |
|------------------|----------|----------|-----------------|
| RSF (baseline)   | 0.7151   | 0.7637   | +0.049          |

A consistent ~+0.05 gap between CV and the public LB is itself a warning sign — CV is supposed to be a noisy but unbiased estimator of held-out performance, and a one-sided gap that large is telling you the train/test distributions differ in a way the CV split can't reflect. (Which is exactly the KYW shift documented in Section 1.)

**Architecture-search caveat.** Comparing alternative architectures (e.g. gradient-boosted variants) on this CV inverted the LB ordering at the 0.004 level on more than one occasion: CV ranked some variant above RSF, then the public LB ranked it below. So CV-based architecture choice within this family was unreliable. The RSF was selected by LB.

**What I should have done differently** in light of the private-LB shake-up: trusted CV more strongly *between* architectures despite the inversion, on the principle that CV is a noisier but better-calibrated estimate of out-of-distribution performance than a single public test set. See the [post-mortem](results.md) for the full reflection.

## 7. Pointers into the code

| What | Where |
|------|-------|
| `parse_cytogenetics` (targeted) | [src/features.py](../src/features.py) |
| `parse_cytogenetics_broad` (one-hot) | [src/features.py](../src/features.py) |
| `compute_ipssm_score` | [src/features.py](../src/features.py) |
| `build_baseline_features` | [src/features.py](../src/features.py) |
| `build_specialist_features` | [src/features.py](../src/features.py) |
| `BaselineModel` (RSF, v12) | [src/models.py](../src/models.py) |
| `SpecialistModel` (RSF, v22) | [src/models.py](../src/models.py) |
| `select_tazi_patients` (propensity) | [src/selection.py](../src/selection.py) |
| `rank_blend` | [src/blend.py](../src/blend.py) |
| End-to-end pipeline | [src/run_pipeline.py](../src/run_pipeline.py) |

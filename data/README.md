# Data

No data files are committed to this repository. To reproduce the pipeline, you need two cohorts.

## QRT Challenge data (required)

The official challenge data is hosted at the École Normale Supérieure's `challengedata.ens.fr` platform.

- Challenge page: https://challengedata.ens.fr/
- Search for the QRT challenge in oncology (MDS / AML survival prediction).
- After signing up and accepting the challenge's data-use agreement, you can download:
  - `X_train/clinical_train.csv`
  - `X_train/molecular_train.csv`
  - `target_train.csv`
  - `X_test/clinical_test.csv`
  - `X_test/molecular_test.csv`

Place them under a directory of your choice and point the pipeline at it via `--data-dir`.

## Tazi 2022 cohort (optional, for the specialist's augmentation)

The augmented specialist uses a publicly available external MDS/AML cohort from Tazi et al. 2022:

> Tazi, Y. et al. (2022). *Unified classification and risk-stratification in Acute Myeloid Leukemia.* **Nature Communications** 13, 4622. https://doi.org/10.1038/s41467-022-31419-9

Supplementary data tables (clinical features, mutation calls, survival outcomes) are downloadable from the publication's supplementary materials. You will need to harmonize the schema into:

- `clinical.csv` — columns: `ID, BM_BLAST, WBC, HB, PLT, CYTOGENETICS`
- `molecular.csv` — columns: `ID, GENE, PROTEIN_CHANGE, EFFECT, VAF`
- `target.csv` — columns: `ID, OS_YEARS, OS_STATUS`

with `ID` prefixed by `T_` to distinguish from QRT IDs. Pass via `--tazi-dir`.

If you don't have the Tazi data, the pipeline still runs end-to-end with `--tazi-dir` omitted; the specialist trains on QRT only, giving a slightly lower public-LB score but the same methodology.

## Reproducibility note

The cleaned pipeline in this repository is a refactored version of the original iteration code. Running it should produce a submission within ~0.001 IPCW-C of the committed `outputs/final_submission.csv`, but exact byte-identical reproduction depends on `numpy` and `scikit-survival` versions and is not guaranteed.

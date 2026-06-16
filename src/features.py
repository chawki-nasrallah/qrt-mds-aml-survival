"""Feature engineering for MDS/AML survival prediction.

Two feature sets are exposed:

- ``build_baseline_features`` — wide ~1800-column matrix combining clinical
  variables, gene one-hot encodings weighted by VAF, cytogenetic flags from a
  broad regex parser, and a corrected IPSS-M score. Used by the v12 baseline.
- ``build_specialist_features`` — 23-column reduced biologically-robust matrix
  (clinical numerics + curated cytogenetic flags + curated gene flags +
  engineered scores). Used by the v22 specialist.

The IPSS-M score follows Bernard et al. 2022 (NEJM Evidence). Coefficients
``BETAS`` and reference means ``MEANS`` reproduce the published values.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer
from sklearn.ensemble import RandomForestRegressor


CLINICAL_NUMERICS = ["BM_BLAST", "WBC", "HB", "PLT"]

CYTO_FLAGS = [
    "cyto_normal", "cyto_complex", "cyto_very_complex", "cyto_monosomal",
    "cyto_minus7", "cyto_del7q", "cyto_del5q", "cyto_plus8", "cyto_del20q",
    "cyto_del11q", "cyto_i17q", "cyto_inv3", "cyto_minusY", "cyto_minus17",
    "cyto_plus21",
]

CURATED_GENES = [
    "TP53", "NPM1", "FLT3", "RUNX1", "ASXL1", "IDH1", "IDH2",
]

NRES_GENES = [
    "BCOR", "BCORL1", "CEBPA", "ETNK1", "GATA2", "GNB1", "IDH1", "NF1",
    "PHF6", "PPM1D", "PRPF8", "PTPN11", "SETBP1", "STAG2", "WT1",
]

BETAS: Mapping[str, float] = dict(
    HB1=-0.171, TRANSF_PLT100=-0.222, BLAST5=0.352, CYTOVEC=0.287,
    TP53multi=1.180, FLT3=0.798, MLL_PTD=0.798, SF3B1_5q=0.504,
    NPM1=0.430, RUNX1=0.423, NRAS=0.417, ETV6=0.391, IDH2=0.379,
    CBL=0.295, EZH2=0.270, U2AF1=0.247, SRSF2=0.239, DNMT3A=0.221,
    ASXL1=0.213, KRAS=0.202, SF3B1_alpha=-0.0794, nRes2=0.231,
)

MEANS: Mapping[str, float] = dict(
    HB1=9.87, TRANSF_PLT100=1.41, BLAST5=0.922, CYTOVEC=1.39,
    TP53multi=0.0710, FLT3=0.0108, MLL_PTD=0.0247, SF3B1_5q=0.0166,
    NPM1=0.0112, RUNX1=0.1260, NRAS=0.0362, ETV6=0.0216, IDH2=0.0429,
    CBL=0.0473, EZH2=0.0588, U2AF1=0.0866, SRSF2=0.1580, DNMT3A=0.1610,
    ASXL1=0.2520, KRAS=0.0271, SF3B1_alpha=0.1860, nRes2=0.388,
)

LOG2 = math.log(2)


def parse_cytogenetics(iscn: str | float) -> dict[str, int]:
    """Parse ISCN cytogenetic string into IPSS-R/IPSS-M clinical flags.

    Looks for the specific abnormalities used in MDS risk stratification:
    monosomy 7, del(5q), del(7q), trisomy 8, isochromosome 17q, etc.
    Also computes complex/very-complex/monosomal karyotype counts.
    """
    out = {k: 0 for k in CYTO_FLAGS + ["cyto_n_clones", "cyto_n_abn"]}
    if not isinstance(iscn, str):
        return out
    s = iscn.upper().replace(" ", "")
    if not s:
        return out

    if "46,XX" in s or "46,XY" in s:
        is_simple_diploid = (
            "/" not in s and ",+" not in s and ",-" not in s
            and "DEL" not in s and "INV" not in s
            and "T(" not in s and "I(" not in s
        )
        if is_simple_diploid:
            out["cyto_normal"] = 1
            out["cyto_n_clones"] = 1
            return out

    clones = s.split("/")
    out["cyto_n_clones"] = len(clones)
    max_n_abn = 0
    for clone in clones:
        n_abn = len(re.findall(r"([+-]\d+|DEL|ADD|INV|T\(|I\(|DER)", clone))
        max_n_abn = max(max_n_abn, n_abn)
    out["cyto_n_abn"] = max_n_abn

    if max_n_abn >= 3:
        out["cyto_complex"] = 1
    if max_n_abn >= 5:
        out["cyto_very_complex"] = 1

    monos = sum(
        1 for clone in clones
        for m in re.findall(r"-(\d+)", clone) if m.isdigit()
    )
    if monos >= 2 or (monos >= 1 and max_n_abn >= 3):
        out["cyto_monosomal"] = 1

    patterns = {
        "cyto_minus7": r"(-7(?![QPp])|-7,|-7$|-7/)",
        "cyto_minus17": r"(-17(?![QPp])|-17,|-17$|-17/)",
        "cyto_minusY": r"-Y",
        "cyto_plus8": r"(\+8(?![,QqPp\d])|\+8,|\+8/|\+8$)",
        "cyto_plus21": r"(\+21(?![,QqPp\d])|\+21,|\+21/|\+21$)",
    }
    for flag, pat in patterns.items():
        if re.search(pat, s):
            out[flag] = 1

    substring_checks = {
        "cyto_del7q": ("DEL(7Q)", "DEL(7)(Q"),
        "cyto_del5q": ("DEL(5Q)", "DEL(5)(Q"),
        "cyto_del20q": ("DEL(20Q)", "DEL(20)(Q"),
        "cyto_del11q": ("DEL(11Q)", "DEL(11)(Q"),
        "cyto_i17q": ("I(17Q)", "I(17)(Q"),
        "cyto_inv3": ("INV(3)",),
    }
    for flag, needles in substring_checks.items():
        if any(needle in s for needle in needles):
            out[flag] = 1
    return out


def cyto_ipssr_group(flags: Mapping[str, int]) -> int:
    """Map cytogenetic flag dict to IPSS-R cytogenetic risk group (1-4)."""
    if flags.get("cyto_very_complex") or flags.get("cyto_minus7") or flags.get("cyto_del7q"):
        return 4
    if flags.get("cyto_complex") or flags.get("cyto_minus17") or flags.get("cyto_i17q"):
        return 3
    if flags.get("cyto_del11q") or flags.get("cyto_plus8") or flags.get("cyto_minusY"):
        return 2
    return 1


def parse_cytogenetics_broad(iscn: str | float) -> dict[str, int]:
    """Broad regex extraction used by the baseline (one-hot every abnormality).

    Unlike ``parse_cytogenetics`` which targets known prognostic lesions,
    this extracts every numeric +/-, translocation, inversion, and any
    karyotype with 3+ abnormalities. Produces sparse high-dimensional flags.
    """
    s = str(iscn).upper().replace(" ", "")
    results: dict[str, int] = defaultdict(int)
    if s in ("", "NAN"):
        results["normal"] = 1
        return dict(results)

    clones = s.split("/")
    max_ab = max(
        (len(re.findall(r"([+-]\d+|DEL|ADD|INV|T\(|DER)", clone)) for clone in clones),
        default=0,
    )
    if max_ab >= 3:
        results["Complex_Karyotype"] = 1

    for type_, c1, c2 in re.findall(r"(T|INV)\((\d+|X|Y)[;]?(\d+|X|Y)?\)", s):
        suffix = f";{c2}" if c2 else ""
        results[f"{type_}({c1}{suffix})"] = 1

    for sign, num in re.findall(r"(?<![0-9])([+-])(\d+|X|Y)(?=[,/]|$)", s):
        results[f"{sign}{num}"] = 1

    if not results:
        results["normal"] = 1
    return dict(results)


def _protein_change_position(p: str | float) -> int:
    """Extract the residue number from a HGVS protein change (e.g. 'p.R882H' -> 882)."""
    s = str(p)
    if not s or s == "nan":
        return 0
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _gene_clean(g: str | float) -> str:
    s = str(g)
    return "UNKNOWN" if not s or s == "nan" else s.upper()


def compute_ipssm_score(
    clinical: pd.DataFrame,
    molecular: pd.DataFrame,
) -> pd.Series:
    """Compute the corrected IPSS-M risk score per patient.

    Implements the Bernard et al. 2022 prognostic formula:
    weighted sum of hemoglobin, platelets, blasts, cytogenetic group,
    TP53 multihit status, FLT3-ITD/TKD, MLL-PTD, NPM1, and curated
    secondary gene flags. Used as a single dense feature inside the
    baseline matrix; also entered directly into the specialist.
    """
    cyto_flags_df = clinical["CYTOGENETICS"].apply(parse_cytogenetics).apply(pd.Series)
    cyto_flags_df.index = clinical["ID"].values
    cyto_group = cyto_flags_df.apply(lambda r: cyto_ipssr_group(r.to_dict()), axis=1)

    mol = molecular.copy()
    mol["GENE_UP"] = mol["GENE"].astype(str).str.upper()
    pc = mol["PROTEIN_CHANGE"].fillna("")

    by_id = lambda series: series.groupby(mol["ID"]).any().astype(int)

    npm1 = by_id(mol["GENE_UP"] == "NPM1")
    mll_ptd = by_id(mol["GENE_UP"].isin(["MLL", "KMT2A"]) & mol["EFFECT"].isin(["PTD", "ITD"]))
    flt3_itd = by_id((mol["GENE_UP"] == "FLT3") & (mol["EFFECT"] == "ITD"))
    flt3_tkd = by_id(
        (mol["GENE_UP"] == "FLT3")
        & (pc.str.contains(r"D835|I836|836_", regex=True, na=False))
    )
    tp53_n = (mol["GENE_UP"] == "TP53").groupby(mol["ID"]).sum()
    tp53_vaf_max = mol[mol["GENE_UP"] == "TP53"].groupby("ID")["VAF"].max()
    nres = mol[mol["GENE_UP"].isin(NRES_GENES)].groupby("ID")["GENE_UP"].nunique().clip(upper=2)

    gene_flags = {
        g: by_id(mol["GENE_UP"] == g)
        for g in ["RUNX1", "NRAS", "ETV6", "IDH2", "CBL", "EZH2", "U2AF1",
                 "SRSF2", "DNMT3A", "ASXL1", "KRAS", "SF3B1", "TP53", "BCOR"]
    }

    idx = clinical["ID"].values
    reindex_int = lambda s, default=0: s.reindex(idx).fillna(default).astype(int)
    reindex_float = lambda s, default=0.0: s.reindex(idx).fillna(default).astype(float)

    pri = pd.DataFrame(index=idx)
    pri["npm1"] = reindex_int(npm1)
    pri["mll_ptd"] = reindex_int(mll_ptd)
    pri["flt3_any"] = (reindex_int(flt3_itd) | reindex_int(flt3_tkd)).astype(int)
    pri["nres"] = reindex_int(nres)
    pri["tp53_n"] = reindex_int(tp53_n)
    pri["tp53_vaf_max"] = reindex_float(tp53_vaf_max)
    for g, s in gene_flags.items():
        pri[f"gene_{g}"] = reindex_int(s)
    pri["cyto_ipssr_group"] = cyto_group.reindex(idx).fillna(1).astype(float).values
    pri["cyto_minus17"] = cyto_flags_df.reindex(idx)["cyto_minus17"].fillna(0).astype(int).values
    pri["cyto_i17q"] = cyto_flags_df.reindex(idx)["cyto_i17q"].fillna(0).astype(int).values
    pri["cyto_del5q"] = cyto_flags_df.reindex(idx)["cyto_del5q"].fillna(0).astype(int).values

    pri["tp53_multihit"] = (
        (pri["tp53_n"] >= 2)
        | ((pri["tp53_n"] == 1) & (pri["tp53_vaf_max"] > 0.5))
        | ((pri["gene_TP53"] == 1) & ((pri["cyto_minus17"] == 1) | (pri["cyto_i17q"] == 1)))
    ).astype(int)

    med_hb = clinical["HB"].median()
    med_plt = clinical["PLT"].median()
    med_blast = clinical["BM_BLAST"].median()
    hb = clinical["HB"].fillna(med_hb).values
    plt_ = clinical["PLT"].fillna(med_plt).values
    blast = clinical["BM_BLAST"].fillna(med_blast).values

    HB1 = np.clip(hb, 4, 20)
    TRANSF_PLT100 = np.minimum(plt_, 250) / 100
    BLAST5 = np.minimum(blast, 20) / 5
    CYTOVEC = pri["cyto_ipssr_group"].values

    score = (
        (HB1 - MEANS["HB1"]) * BETAS["HB1"]
        + (TRANSF_PLT100 - MEANS["TRANSF_PLT100"]) * BETAS["TRANSF_PLT100"]
        + (BLAST5 - MEANS["BLAST5"]) * BETAS["BLAST5"]
        + (CYTOVEC - MEANS["CYTOVEC"]) * BETAS["CYTOVEC"]
    ) / LOG2

    score += (pri["tp53_multihit"].values - MEANS["TP53multi"]) * BETAS["TP53multi"] / LOG2
    score += (pri["flt3_any"].values - MEANS["FLT3"]) * BETAS["FLT3"] / LOG2
    score += (pri["mll_ptd"].values - MEANS["MLL_PTD"]) * BETAS["MLL_PTD"] / LOG2
    score += (pri["npm1"].values - MEANS["NPM1"]) * BETAS["NPM1"] / LOG2

    for g in ["RUNX1", "NRAS", "ETV6", "IDH2", "CBL", "EZH2", "U2AF1",
              "SRSF2", "DNMT3A", "ASXL1", "KRAS"]:
        score += (pri[f"gene_{g}"].values - MEANS[g]) * BETAS[g] / LOG2

    sf3b1_5q = (pri["gene_SF3B1"].values * pri["cyto_del5q"].values).astype(float)
    score += (sf3b1_5q - MEANS["SF3B1_5q"]) * BETAS["SF3B1_5q"] / LOG2

    sf3b1_alpha = (
        (pri["gene_SF3B1"] == 1)
        & (pri["cyto_del5q"] == 0)
        & (pri["gene_RUNX1"] == 0)
        & (pri["gene_EZH2"] == 0)
        & (pri["gene_BCOR"] == 0)
        & (pri["gene_NRAS"] == 0)
        & (blast < 5)
    ).astype(float).values
    score += (sf3b1_alpha - MEANS["SF3B1_alpha"]) * BETAS["SF3B1_alpha"] / LOG2
    score += (pri["nres"].values - MEANS["nRes2"]) * BETAS["nRes2"] / LOG2

    return pd.Series(score, index=idx, name="ipssm_score")


def build_specialist_features(
    clinical: pd.DataFrame,
    molecular: pd.DataFrame,
) -> pd.DataFrame:
    """23-feature reduced matrix for the specialist RSF.

    Returns clinical numerics + curated cytogenetic flags + curated gene
    flags + engineered scores (ipssm_score, tp53_multihit, flt3_itd) + n_mut.
    All features have direct prognostic meaning in MDS/AML — no one-hot
    sparse columns. Same shape works across QRT and Tazi cohorts, which is
    why the specialist transfers across distributions where the baseline's
    high-dim matrix does not.
    """
    feats = clinical[["ID"] + CLINICAL_NUMERICS].copy().set_index("ID")

    cyto = clinical["CYTOGENETICS"].apply(parse_cytogenetics).apply(pd.Series)
    cyto.index = clinical["ID"].values
    for col in ["cyto_normal", "cyto_complex", "cyto_very_complex", "cyto_minus7",
                "cyto_del5q", "cyto_del7q", "cyto_minus17", "cyto_plus8"]:
        feats[col] = cyto[col].reindex(feats.index).fillna(0).astype(int)

    mol = molecular.copy()
    mol["GENE_UP"] = mol["GENE"].astype(str).str.upper()
    for g in CURATED_GENES:
        flag = (mol["GENE_UP"] == g).groupby(mol["ID"]).any().astype(int)
        feats[f"gene_{g}"] = feats.index.map(flag).fillna(0).astype(int)

    tp53_n = (mol["GENE_UP"] == "TP53").groupby(mol["ID"]).sum()
    tp53_vaf_max = mol[mol["GENE_UP"] == "TP53"].groupby("ID")["VAF"].max()
    flt3_itd = ((mol["GENE_UP"] == "FLT3") & (mol["EFFECT"] == "ITD")).groupby(mol["ID"]).any().astype(int)
    feats["tp53_n"] = feats.index.map(tp53_n).fillna(0).astype(int)
    feats["tp53_vaf_max"] = feats.index.map(tp53_vaf_max).fillna(0.0)
    feats["flt3_itd"] = feats.index.map(flt3_itd).fillna(0).astype(int)
    cyto_minus17 = cyto["cyto_minus17"].reindex(feats.index).fillna(0).astype(int)
    feats["tp53_multihit"] = (
        (feats["tp53_n"] >= 2)
        | ((feats["tp53_n"] == 1) & (feats["tp53_vaf_max"] > 0.5))
        | ((feats["gene_TP53"] == 1) & (cyto_minus17 == 1))
    ).astype(int)

    feats["ipssm_score"] = compute_ipssm_score(clinical, molecular).reindex(feats.index).values
    feats["n_mut"] = feats.index.map(molecular.groupby("ID").size()).fillna(0).astype(int)

    keep = [
        "BM_BLAST", "WBC", "HB", "PLT",
        "cyto_normal", "cyto_complex", "cyto_very_complex",
        "cyto_minus7", "cyto_del5q", "cyto_del7q", "cyto_minus17", "cyto_plus8",
        "gene_TP53", "gene_NPM1", "gene_FLT3", "gene_RUNX1", "gene_ASXL1",
        "gene_IDH1", "gene_IDH2",
        "tp53_multihit", "flt3_itd", "ipssm_score", "n_mut",
    ]
    return feats[keep].reset_index()


def build_baseline_features(
    clinical: pd.DataFrame,
    molecular: pd.DataFrame,
    *,
    fit_imputer: bool = True,
    imputer: IterativeImputer | None = None,
) -> tuple[pd.DataFrame, IterativeImputer]:
    """Wide ~1800-column feature matrix for the v12 baseline.

    Combines:
    - Clinical numerics (log1p-transformed, MICE-imputed)
    - Mutation count (Nmut)
    - Broad cytogenetic one-hot from ``parse_cytogenetics_broad``
    - Gene one-hots weighted by max VAF per patient
    - EFFECT and PROTEIN_CHANGE one-hots
    - Targeted interaction flags (NPM1+/FLT3-, TP53+complex)
    - Curated cytogenetic flags from ``parse_cytogenetics``
    - IPSS-M score as a single dense feature

    Returns the feature DataFrame and the fitted IterativeImputer.
    Pass the returned imputer back via ``imputer=`` when transforming
    the test set with ``fit_imputer=False``.
    """
    df = clinical.copy().reset_index(drop=True)

    nmut = molecular.groupby("ID").size().rename("Nmut")
    df = df.merge(nmut, on="ID", how="left").fillna({"Nmut": 0})

    cyto_broad = df["CYTOGENETICS"].apply(parse_cytogenetics_broad).apply(pd.Series).fillna(0)
    df = pd.concat([df, cyto_broad], axis=1)

    mol = molecular.copy()
    mol["GENE_CLEAN"] = mol["GENE"].apply(_gene_clean)
    gene_pivot = (
        mol.pivot_table(index="ID", columns="GENE_CLEAN", values="VAF", aggfunc="max", fill_value=0)
    )
    gene_pivot.columns = [f"GENE_{c}" for c in gene_pivot.columns]
    df = df.merge(gene_pivot, on="ID", how="left").fillna(0)

    for feature in ["EFFECT", "PROTEIN_CHANGE"]:
        if feature == "PROTEIN_CHANGE":
            mol["_feat"] = mol[feature].apply(_protein_change_position)
        else:
            mol["_feat"] = mol[feature]
        dummies = pd.get_dummies(mol["_feat"], prefix=feature)
        dummies["ID"] = mol["ID"].values
        per_patient = dummies.groupby("ID").max()
        df = df.merge(per_patient, on="ID", how="left").fillna(0)

    if "GENE_NPM1" in df.columns and "GENE_FLT3" in df.columns:
        df["INT_NPM1_pos_FLT3_neg"] = ((df["GENE_NPM1"] > 0) & (df["GENE_FLT3"] == 0)).astype(int)
    if "GENE_TP53" in df.columns and "Complex_Karyotype" in df.columns:
        df["INT_TP53_Complex"] = ((df["GENE_TP53"] > 0) & (df["Complex_Karyotype"] > 0)).astype(int)

    cyto_curated = clinical["CYTOGENETICS"].apply(parse_cytogenetics).apply(pd.Series)
    for col in CYTO_FLAGS:
        df[f"v9_{col}"] = cyto_curated[col].fillna(0).astype(int).values
    df["v9_ipssm_score"] = compute_ipssm_score(clinical, molecular).reindex(df["ID"].values).values

    log_cols = ["BM_BLAST", "WBC", "HB", "PLT"]
    if "ANC" in df.columns:
        log_cols.append("ANC")
    if "MONOCYTES" in df.columns:
        log_cols.append("MONOCYTES")
    for c in log_cols:
        df[c] = np.log1p(df[c])

    if fit_imputer:
        imputer = IterativeImputer(
            estimator=RandomForestRegressor(n_estimators=10, random_state=42),
            max_iter=10, random_state=42,
        )
        df[log_cols] = imputer.fit_transform(df[log_cols])
    else:
        assert imputer is not None, "imputer must be provided when fit_imputer=False"
        df[log_cols] = imputer.transform(df[log_cols])

    drop_cols = {"ID", "CENTER", "CYTOGENETICS", "OS_STATUS", "OS_YEARS"}
    feature_cols = [c for c in df.columns if c not in drop_cols]
    out = df[["ID"] + feature_cols].copy()
    out[feature_cols] = out[feature_cols].fillna(0)
    return out, imputer

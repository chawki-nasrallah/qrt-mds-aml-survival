# Results & Post-mortem

## Final leaderboard

| Leaderboard | IPCW-C    | Rank   | Notes |
|-------------|-----------|--------|-------|
| Public      | **0.7685**| **11 / 743** | within 0.0054 of the top public score |
| Private     | **0.7124**| **189**| a 5-point shake-up across the entire field |

For reference, the organisers' baseline benchmark scored **0.6411** on the private LB (rank 678). My submission beat the benchmark by **+0.0713** in IPCW-C.

The submitted file is [outputs/final_submission.csv](../outputs/final_submission.csv).

## The shake-up was systemic

Top of the public leaderboard collapsed from 0.7739 to 0.7231 on private — a **5-point C-index drop across the entire field**. This isn't a "you got unlucky" gap; it's a structural feature of the competition.

| Position           | Public score   | Private score | Drop      |
|--------------------|----------------|---------------|-----------|
| Top-of-LB winner   | 0.7739 (rank 1)| 0.7231 (rank 1, different team) | −0.0508 |
| **My submission**  | **0.7685 (rank 11)**| **0.7124 (rank 189)**| **−0.0561** |

My drop was **0.0053 worse than the top-of-LB winner** — within the same ~0.005 band as the rest of the top of the public leaderboard. For reference, the organisers' benchmark scored 0.6411 on private (rank 678); my submission beat it by **+0.0713**.

## What likely happened, in concrete terms

My iteration history (not committed to this repo, but logged in private notes) shows ~22 model variants over ~4 months. The last 10 chased improvements of ~0.001 each on the public LB:

- v12 (LB 0.7637) → v21 specialist blend at α=0.50 (LB 0.7674, +0.0037)
- v21 → v22 with Option-C propensity selection at α=0.30 (LB 0.7685, +0.0011)

Each of those was a defensible methodological improvement — and on the public LB, each delta was real. But on a 1193-patient public test set, deltas at the 0.001 scale are within sampling noise of the *true* underlying IPCW-C. Once you start chasing them, you're partly fitting noise in the public test composition.

The private LB suggests that's what happened: the specialist-blend (v21/v22) gains over v12 didn't transfer. Looking at the drops:

- v12 baseline alone (estimated private from public-private slope): ~0.7137
- v22 final submission (actual private): 0.7124
- Δ private ≈ −0.0013, the opposite sign of the +0.0048 public Δ.

The specialist-blend was an over-adaptation to the 5%→19% AML enrichment of the *public* test set. Whatever the private test composition was (it's not disclosed), it wasn't that.

## What I'd do differently

1. **Pick a final submission once per-iteration public-LB deltas drop below ~0.003** and don't touch it. The first 0.001 you chase is the start of overfitting; subsequent improvements compound the problem.

2. **Trust cross-validation more strongly than the public LB late in a competition,** especially when CV and LB disagree. On several architecture comparisons my CV scores ranked one variant above another, and the LB inverted the ranking. I sided with the LB. In hindsight the CV-vs-LB disagreement was a signal that the public LB carried test-set-specific noise that CV didn't.

3. **Use the public LB as a binary sanity check, not a continuous optimization target.** "Does this submission rank in the top 30?" is a sanity check; "is this submission +0.0011 above the previous?" is the optimization target trap.

4. **Judge each iteration against domain logic and prior published work, not just the public LB.** A +0.001 LB gain from a change that's biologically arbitrary and unsupported by literature is probably noise. The IPSS-M weights (Bernard 2022) and the RSF + curated-feature approach in Tazi 2022 were anchors I could have leaned on more heavily — when an iteration moved away from a literature-validated structure for a marginal LB gain, that should have been a flag rather than a signal to continue.

5. **Treat the leaderboard score as an estimate with a confidence interval.** A 0.001 improvement on a 1193-patient C-index has a standard error easily larger than 0.001 — the "improvement" is often just within-LB sampling noise.

## What I would not change

The work in [docs/methodology.md](methodology.md) — the data-shift diagnosis, the IPSS-M score implementation, the specialist-blend architecture, the rank-blending derivation, the propensity-selection refinement — is methodology I would defend in any technical conversation. The shake-up isn't a verdict on the methodology; it's a verdict on **how long I kept iterating once the public LB became my proxy for ground truth**.

If you're a reviewer skimming this repo, that last paragraph is the most important thing in it. Then read [docs/methodology.md](methodology.md).

## Reproducibility caveats

- The source pipeline ([src/run_pipeline.py](../src/run_pipeline.py)) is a refactored, cleaned-up version of the original iteration code, not a byte-identical re-runner. Running it against the QRT data should produce a submission within ~0.001 IPCW-C of `outputs/final_submission.csv`, but exact reproduction depends on numpy/scikit-survival version pinning that I haven't aggressively locked.
- The Tazi cohort is not committed (see [data/README.md](../data/README.md)). Without it, the specialist trains on QRT alone, which should still produce a competitive submission at perhaps ~0.001–0.002 lower public IPCW-C than the augmented version.
- Random seeds are fixed to 42 throughout, but parallel RSF builds in `sksurv` are not fully deterministic across hardware. Expect minor (~1e-4) numerical drift on re-runs.

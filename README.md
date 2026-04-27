# 🏥 Patient Flow Ops Framework

**A Data-Driven Operations Framework for Outpatient Clinic Patient Flow Management**

<p align="center">
  <img src="analysis/figures/scenario_dashboard.png" alt="Scenario Results Dashboard" width="100%"/>
</p>

---

## 📌 Overview

This project builds a **fully reproducible, end-to-end operations framework** for outpatient patient flow. Starting from three public U.S. healthcare datasets, we:

1. Train a **no-show prediction model** (GBT, AUC 0.74) on 110K appointments
2. Fit **lognormal service-time distributions** per specialty from CDC NAMCS 2019
3. Synthesize a **realistic 20-provider clinic** with payer-network coverage
4. Run a **multi-stage discrete-event simulation** (SimPy) over 30 replications
5. Compare two **scheduling policies** on throughput, wait time, and utilization

**Key finding:** Both policies (overbooking and buffer-slotting) trade throughput against wait time, but neither resolves the structural nurse-stage bottleneck. Adding one nurse (6 → 7) cuts median wait from **26 → 21 min** — nearly matching the conservative policy without sacrificing throughput. *Software policies have a ceiling that staffing breaks through.*

---

## 📊 Results at a Glance

### Baseline Simulation — 30 Replications

| Metric | Mean | 95% CI |
|--------|------|--------|
| Completed visits / day | 262 | [247, 276] |
| Median wait to physician | 25.8 min | [20, 39] |
| 90th-percentile wait | 42.2 min | — |
| MD utilization | 60.7% | — |
| **Nurse utilization** | **95.2%** ← binding constraint | — |
| No-show rate | 18.0% | — |

### Policy Comparison

| Policy | Throughput | Median Wait | Nurse Util | Nurse OT |
|--------|-----------|-------------|------------|----------|
| **Baseline** | 262 / day | 25.8 min | 95.2% | ~0 hrs |
| **NAR** — overbook no-show slots (π = 0.30) | **319 (+22%)** ↑ | 77 min (+198%) ↑ | **100% (overloaded)** | +1.3 hrs/day |
| **RBS** — buffer high-risk slots (30 min) | 225 (−14%) ↓ | **19.7 min (−24%)** ↓ | 81.1% | 0 hrs |

NAR and RBS are opposite corners of the **throughput–wait Pareto frontier**. See [`scenario_tradeoff.png`](analysis/figures/scenario_tradeoff.png).

### No-Show Model Performance

| Model | AUC | Brier Score |
|-------|-----|-------------|
| Gradient Boosted Trees | **0.742** | 0.144 |
| Logistic Regression (baseline) | 0.734 | — |

Lead-time effect on no-show rate: same-day = 4.7% · 1–2 days = 22.7% · 8–30 days = 31.7% · 31+ days = 33.0%

---

## 🗂️ Repository Structure

```
patient-flow-ops-framework/
│
├── README.md
├── requirements.txt                        # Frozen Python deps
├── SUBMISSION_README.md                    # Project submission notes
│
├── analysis/
│   ├── main.py                             # Orchestrator — runs all 7 phases
│   ├── data_io.py                          # Shared data loaders
│   │
│   ├── phase1_data_prep.py                 # Clean & merge 3 datasets → parquet
│   ├── phase2_noshow_model.py              # GBT no-show classifier (AUC 0.742)
│   ├── phase3_service_times.py             # Lognormal fits per specialty (NAMCS)
│   ├── phase4_synthetic_clinic.py          # 20-provider clinic + payer networks
│   ├── phase5_simulator.py                 # SimPy multi-stage discrete-event sim
│   ├── phase6_scenarios.py                 # NAR vs RBS (30 reps each)
│   ├── phase7_sensitivity.py               # Sensitivity panel
│   ├── build_pptx.py                       # Auto-generates slides from figures
│   │
│   ├── figures/                            # 12 PNG outputs
│   │   ├── scenario_dashboard.png
│   │   ├── scenario_tradeoff.png
│   │   ├── scenario_compare_waits.png
│   │   ├── scenario_compare_throughput.png
│   │   ├── noshow_drivers.png
│   │   ├── noshow_lead_time.png
│   │   ├── sensitivity_panel.png
│   │   └── ...
│   │
│   └── outputs/                            # Parquet / JSON / joblib artifacts
│       ├── noshow_model.joblib
│       ├── noshow_metrics.json
│       ├── scenario_results.parquet
│       ├── scenario_summary.json
│       ├── service_times.json
│       └── ...
│
└── datasets/
    ├── README.md                           # Dataset descriptions + download URLs
    ├── KaggleV2-May-2016.csv               # No-show appointments (110,527 rows)
    ├── namcs/                              # CDC NAMCS 2019 codebook + readme
    └── meps/                               # MEPS HC-243 codebook
```

---

## ⚡ Quickstart

### Prerequisites

- Python ≥ 3.9
- ~1 GB disk space (datasets + artifacts)
- Optional: 711 MB CMS provider CSV for full Phase 1 re-run (URL in `datasets/README.md`). The filtered parquet (`cms_providers.parquet`, 12 MB) is pre-included so all figures reproduce without it.

### Install & Run

```bash
git clone https://github.com/<your-org>/patient-flow-ops-framework.git
cd patient-flow-ops-framework

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run the full pipeline (~3 minutes)
python analysis/main.py

# Rebuild the slides deck
python analysis/build_pptx.py
```

### Run One Phase at a Time

Each phase depends on the previous phase's outputs. Run in order:

```bash
python analysis/phase1_data_prep.py         # ~2 min (skippable — outputs cached)
python analysis/phase2_noshow_model.py      # ~30 s
python analysis/phase3_service_times.py     # ~5 s
python analysis/phase4_synthetic_clinic.py  # ~5 s
python analysis/phase5_simulator.py         # ~15 s
python analysis/phase6_scenarios.py         # ~1 min
python analysis/phase7_sensitivity.py       # ~15 s
```

---

## 🔬 Methodology

### Pipeline Overview

```
Kaggle (110K appts)  ──►  Phase 1: Data Prep  ──►  kaggle_clean.parquet
CDC NAMCS 2019       ──►                      ──►  namcs_slim.parquet
MEPS HC-243          ──►                      ──►  meps_payer.parquet
CMS Provider Data    ──►                      ──►  cms_providers.parquet

kaggle_clean.parquet ──►  Phase 2: No-Show Model   ──►  noshow_model.joblib (AUC 0.742)
namcs_slim.parquet   ──►  Phase 3: Service Times   ──►  service_times.json (lognormal fits)
cms + meps           ──►  Phase 4: Synthetic Clinic ──►  clinic_providers.parquet
                                                        clinic_patients.parquet

Phases 2–4 outputs   ──►  Phase 5: SimPy Simulator ──►  schedule_with_p.parquet
                     ──►  Phase 6: Scenarios (×30) ──►  scenario_results.parquet
                     ──►  Phase 7: Sensitivity      ──►  sensitivity_results.parquet
```

### Key Design Decisions

| Decision | Detail |
|----------|--------|
| **No-show model** | GBT on Kaggle (Brazilian) data; patient-level train/test split (no leakage). Predicted probabilities rescaled to U.S. base rate of 18% before use in the simulator. |
| **Service times** | Lognormal fits per specialty from CDC NAMCS 2019. Check-in (~3 min) and nurse-intake (~10 min) calibrated to OR literature (Cayirli & Veral 2003; Gupta & Denton 2008). |
| **Synthetic clinic** | 20 providers sampled from the CMS pool with realistic specialty mix and asymmetric network coverage: all 20 in private, 14 in public, only 5 in Medicaid. |
| **Payer mix** | Matches MEPS HC-243 — 58% private / 34% public / 7% Medicaid / 1% uninsured. |
| **Simulator validation** | Sanity check with 12 nurses + zero no-shows shows wait collapses → confirms nurse stage is the binding constraint and simulator correctness. |
| **Policy framing** | NAR and RBS are two corner solutions of a constrained stochastic optimization over (throughput, wait, overtime). Richer Pareto-optimal search is listed as future work. |

---

## 📦 Datasets

| Dataset | Source | Rows | Role |
|---------|--------|-----:|------|
| Kaggle No-Show Appointments | [Kaggle](https://www.kaggle.com/datasets/joniarroba/noshowappointments) | 110,527 | No-show labels, demand, demographics |
| CDC NAMCS 2019 PUF | [CDC FTP](https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Datasets/NAMCS/) | 8,250 | Physician consultation time (`TIMEMD`) |
| AHRQ MEPS HC-243 (2022) | [MEPS](https://meps.ahrq.gov/mepsweb/data_stats/download_data_files_detail.jsp?cboPufNumber=HC-243) | 22,431 | U.S. insurance payer mix |
| CMS Doctors & Clinicians | [CMS](https://data.cms.gov/provider-data/dataset/mj5m-pzi6) | 2,857,460 | Provider pool (NPI, specialty, location) |

> **Note:** The 711 MB CMS CSV is not included in this repo. All downstream figures and tables reproduce from the pre-filtered `cms_providers.parquet` (12 MB) in `analysis/outputs/`. See `datasets/README.md` for the direct download URL.

---

## ⚠️ Limitations

- Demand calibrated on Brazilian Kaggle data; absolute no-show level rescaled to U.S. 18% base rate — behavioral patterns may differ
- Operational layer (network labels, multi-stage service times) is synthesized; real EHR + insurance contract data would refine it
- Single-day simulation — no demand carryover, no-show callback dynamics, or seasonal variation
- No financial layer (revenue per visit, overtime cost) — straightforward to add given a clinic's contract rates
- NAR / RBS are corner solutions; a full Pareto sweep over the policy parameter space is future work

---

## 🛠️ Dependencies

```
pandas==3.0.2          numpy==2.4.4           scipy==1.17.1
scikit-learn==1.8.0    simpy==4.1.1           matplotlib==3.10.8
seaborn==0.13.2        pyarrow==24.0.0        joblib==1.5.3
pyreadstat==1.3.4      python-pptx==1.0.2
```

Full frozen list in [`requirements.txt`](requirements.txt).

---

## 📄 License

This project was developed as an academic course deliverable. Datasets remain subject to their original licenses (Kaggle CC0, CDC/AHRQ public use, CMS open data).

---

<p align="center">
  <sub>Team A11 · April 2026</sub>
</p>

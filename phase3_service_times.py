"""Phase 3: Fit service-time distributions for the multi-stage flow.

NAMCS provides empirical TIMEMD (minutes with physician). Check-in and
nurse-intake stages are not in NAMCS — we derive them with documented
assumptions from operations-research literature on outpatient clinics
(Cayirli & Veral 2003; Gupta & Denton 2008):
  - Check-in: lognormal, ~3 min mean (registration/insurance verification)
  - Nurse intake: lognormal, ~50% of MD time mean (vitals, history)

Outputs:
- outputs/service_times.json (fitted distribution parameters)
- figures/service_times_by_specialty.png
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

import data_io as io


SPECCAT_LABEL = {1: "Primary care", 2: "Surgical specialties", 3: "Medical specialties"}


def fit_lognormal(x: np.ndarray) -> dict:
    """Fit lognormal via MLE; return mu, sigma in lognormal-parameter space."""
    x = x[x > 0]
    shape, loc, scale = stats.lognorm.fit(x, floc=0)
    return {"sigma": float(shape), "mu": float(np.log(scale)),
            "mean": float(scale * np.exp(shape**2 / 2)),
            "median": float(scale),
            "n": int(len(x))}


def plot_fits(namcs: pd.DataFrame, fits: dict, out_path):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    for ax, (cat, label) in zip(axes, SPECCAT_LABEL.items()):
        x = namcs.loc[namcs["SPECCAT"] == cat, "TIMEMD"].values
        x = x[x > 0]
        ax.hist(x, bins=range(0, 95, 5), density=True, color="steelblue",
                alpha=0.7, edgecolor="white")
        params = fits[f"md_speccat_{cat}"]
        xs = np.linspace(1, 90, 200)
        pdf = stats.lognorm.pdf(xs, s=params["sigma"], scale=np.exp(params["mu"]))
        ax.plot(xs, pdf, "r-", lw=2, label=f"Lognormal fit\nmedian={params['median']:.1f} min")
        ax.set_title(f"{label}\n(n={params['n']:,})")
        ax.set_xlabel("Minutes with physician (TIMEMD)")
        ax.legend()
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("Density")
    plt.suptitle("NAMCS 2019: physician-consultation time by specialty category", y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


def main():
    io.ensure_dirs()
    namcs = pd.read_parquet(io.OUT / "namcs_slim.parquet")

    fits = {}
    for cat in [1, 2, 3]:
        x = namcs.loc[namcs["SPECCAT"] == cat, "TIMEMD"].values
        fits[f"md_speccat_{cat}"] = fit_lognormal(x)
        fits[f"md_speccat_{cat}"]["label"] = SPECCAT_LABEL[cat]

    # Check-in: lognormal, mean ~3 min, low variance
    # Calibrate so median ~2.5, mean ~3
    fits["checkin"] = {
        "distribution": "lognormal",
        "mu": float(np.log(2.5)),  # median 2.5 min
        "sigma": 0.4,                # CV ~ 0.4
        "mean": float(np.exp(np.log(2.5) + 0.4**2/2)),
        "median": 2.5,
        "source": "Assumed; literature: Cayirli & Veral 2003 cite registration ~2-5 min",
    }
    # Nurse intake: lognormal, mean ~50% of primary-care MD time (~10 min)
    primary_md_mean = fits["md_speccat_1"]["mean"]
    nurse_target_mean = 0.5 * primary_md_mean
    nurse_sigma = 0.45
    nurse_mu = np.log(nurse_target_mean) - nurse_sigma**2 / 2
    fits["nurse_intake"] = {
        "distribution": "lognormal",
        "mu": float(nurse_mu),
        "sigma": nurse_sigma,
        "mean": float(nurse_target_mean),
        "median": float(np.exp(nurse_mu)),
        "source": "Calibrated to ~50% of NAMCS primary-care TIMEMD; "
                   "Gupta & Denton 2008 assume nurse:MD time ratio ~0.4-0.6",
    }

    with open(io.OUT / "service_times.json", "w") as f:
        json.dump(fits, f, indent=2)

    plot_fits(namcs, fits, io.FIG / "service_times_by_specialty.png")

    print("Fitted service-time distributions:")
    for k, v in fits.items():
        if "mean" in v:
            print(f"  {k}: mean={v['mean']:.1f} min, median={v['median']:.1f} min")
    print(f"\nPhase 3 complete. Saved to {io.OUT / 'service_times.json'}")


if __name__ == "__main__":
    main()

"""Phase 7: Sensitivity analysis on the baseline.

Vary one parameter at a time (everything else held at base):
  - U.S. no-show base rate: 10%, 15%, 18% (base), 22%, 25%, 30%
  - Mean MD time multiplier: 0.85x, 1.0x (base), 1.15x, 1.3x
  - Nurse staffing: 4, 5, 6 (base), 7, 8, 10

For each grid point, run baseline policy with 15 replications. Plot tornado-style.

Outputs:
  - outputs/sensitivity_results.parquet
  - figures/sensitivity_panel.png
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import joblib

import data_io as io
from phase5_simulator import (SimConfig, ClinicSim, build_schedule,
                                attach_noshow_probs)
from phase6_scenarios import overtime_hours

N_REPS = 15
BASE_NOSHOW = 0.18
BASE_NURSES = 6


def run_grid_point(providers, patients, service_times, base_schedule,
                    noshow_target, md_time_mult, n_nurses, n_reps=N_REPS, seed=200):
    rep_rows = []
    # Re-scale p_noshow to the target base rate
    sched = base_schedule.copy()
    raw_mean = sched["p_noshow"].mean()
    if raw_mean > 0:
        sched["p_noshow"] = (sched["p_noshow"] * (noshow_target / raw_mean)).clip(0.01, 0.95)
    # Re-scale MD service times
    st = json.loads(json.dumps(service_times))   # deep copy
    for k in ("md_speccat_1", "md_speccat_2", "md_speccat_3"):
        old_mean = st[k]["mean"]
        new_mean = old_mean * md_time_mult
        # adjust mu so the lognormal mean shifts by the multiplier (sigma fixed)
        sigma = st[k]["sigma"]
        st[k]["mu"] = float(np.log(new_mean) - sigma**2 / 2)
        st[k]["mean"] = float(new_mean)
    for rep in range(n_reps):
        cfg = SimConfig(seed=seed + rep, n_nurses=n_nurses)
        sim = ClinicSim(providers, sched, st, cfg, policy="sens")
        visits = sim.run()
        completed = visits[~visits["no_show"]].copy()
        rep_rows.append({
            "noshow_target": noshow_target,
            "md_time_mult": md_time_mult,
            "n_nurses": n_nurses,
            "rep": rep,
            "throughput": len(completed),
            "median_wait_min": completed["wait_to_md"].median(),
            "p90_wait_min": completed["wait_to_md"].quantile(0.90),
            "mean_md_util": float(np.mean([
                min(1.0, sim.md_busy_min[p] / 480) for p in sim.md_busy_min
            ])),
            "nurse_overtime_hrs": overtime_hours(sim.nurse_busy_min, n_nurses),
        })
    return pd.DataFrame(rep_rows)


def plot_sensitivity(results: pd.DataFrame, out_path):
    """3-panel sensitivity: each panel varies one factor, holds others at base."""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    factors = [
        ("noshow_target", "U.S. no-show base rate", BASE_NOSHOW, 1.0, BASE_NURSES),
        ("md_time_mult", "MD time multiplier", BASE_NOSHOW, 1.0, BASE_NURSES),
        ("n_nurses", "Number of nurses on duty", BASE_NOSHOW, 1.0, BASE_NURSES),
    ]
    metrics = ["median_wait_min", "throughput"]
    metric_styles = {"median_wait_min": ("o-", "Median wait (min)", "#E76F51"),
                     "throughput": ("s--", "Throughput (visits/day)", "#2A9D8F")}

    for ax, (factor, factor_label, base_ns, base_mt, base_nu) in zip(axes, factors):
        # Hold other factors at base, vary this one
        subset = results.copy()
        if factor != "noshow_target":
            subset = subset[np.isclose(subset["noshow_target"], base_ns)]
        if factor != "md_time_mult":
            subset = subset[np.isclose(subset["md_time_mult"], base_mt)]
        if factor != "n_nurses":
            subset = subset[subset["n_nurses"] == base_nu]
        agg = subset.groupby(factor)[metrics].mean().sort_index()

        ax2 = ax.twinx()
        style, ylab, color = metric_styles["median_wait_min"]
        ax.plot(agg.index, agg["median_wait_min"], style, color=color,
                  markersize=9, lw=2, label=ylab)
        ax.set_ylabel("Median wait (min)", color=color)
        ax.tick_params(axis="y", labelcolor=color)

        style, ylab2, color2 = metric_styles["throughput"]
        ax2.plot(agg.index, agg["throughput"], style, color=color2,
                   markersize=9, lw=2, label=ylab2)
        ax2.set_ylabel("Throughput (visits/day)", color=color2)
        ax2.tick_params(axis="y", labelcolor=color2)

        ax.set_xlabel(factor_label)
        ax.set_title(factor_label)
        ax.grid(alpha=0.3)
    plt.suptitle("Sensitivity of baseline to key parameters (15 reps per grid point)",
                  y=1.02, fontsize=13)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


def main():
    io.ensure_dirs()
    providers = pd.read_parquet(io.OUT / "clinic_providers.parquet")
    patients = pd.read_parquet(io.OUT / "clinic_patients.parquet")
    with open(io.OUT / "service_times.json") as f:
        service_times = json.load(f)
    model = joblib.load(io.OUT / "noshow_model.joblib")

    # Build a fresh schedule once
    rng = np.random.default_rng(0)
    base_sched = build_schedule(providers, patients, SimConfig(seed=0), rng)
    base_sched = attach_noshow_probs(base_sched, patients, model, rng)

    grid_results = []

    print("Sweeping no-show base rate...")
    for ns in [0.10, 0.15, 0.18, 0.22, 0.25, 0.30]:
        df = run_grid_point(providers, patients, service_times, base_sched,
                              ns, 1.0, BASE_NURSES)
        grid_results.append(df)
    print("Sweeping MD time multiplier...")
    for mt in [0.85, 1.0, 1.15, 1.30]:
        df = run_grid_point(providers, patients, service_times, base_sched,
                              BASE_NOSHOW, mt, BASE_NURSES)
        grid_results.append(df)
    print("Sweeping nurse staffing...")
    for nu in [4, 5, 6, 7, 8, 10]:
        df = run_grid_point(providers, patients, service_times, base_sched,
                              BASE_NOSHOW, 1.0, nu)
        grid_results.append(df)

    results = pd.concat(grid_results, ignore_index=True)
    results.to_parquet(io.OUT / "sensitivity_results.parquet")

    plot_sensitivity(results, io.FIG / "sensitivity_panel.png")
    print("\nPhase 7 complete. Sensitivity figure: figures/sensitivity_panel.png")

    # Print headline elasticities
    base = results[(np.isclose(results["noshow_target"], BASE_NOSHOW)) &
                    (np.isclose(results["md_time_mult"], 1.0)) &
                    (results["n_nurses"] == BASE_NURSES)]
    print(f"\nBaseline at center of grid: median wait = {base['median_wait_min'].mean():.1f} min,"
          f" throughput = {base['throughput'].mean():.0f}")
    extreme_nurses = results[(np.isclose(results["noshow_target"], BASE_NOSHOW)) &
                              (np.isclose(results["md_time_mult"], 1.0)) &
                              (results["n_nurses"] == 10)]
    print(f"With 10 nurses (vs 6):  median wait = {extreme_nurses['median_wait_min'].mean():.1f} min "
          f"(=> adding 4 nurses cuts wait by "
          f"{base['median_wait_min'].mean() - extreme_nurses['median_wait_min'].mean():.0f} min)")


if __name__ == "__main__":
    main()

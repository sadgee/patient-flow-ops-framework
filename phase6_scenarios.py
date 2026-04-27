"""Phase 6: Baseline + 2 policy scenarios + comparison figures.

Policies:
  A) Network-aware reassignment (NAR)
     For each scheduled appointment with predicted no-show prob > p_thresh,
     a standby patient (from a same-network waitlist) is added at the same slot.
     - If original no-shows: standby fills the slot (gain throughput).
     - If original shows:    standby still seen (queues normally).
     This is the operational version of "dynamic reassignment of unused slots."

  B) Risk-buffered scheduling (RBS)
     For each scheduled appointment with predicted no-show prob > p_thresh,
     the *next* slot at that provider is left empty (a 30-min buffer).
     This deliberately reduces schedule density on high-variability appointments
     to absorb cascade delays when those patients do show.

Outputs:
  - outputs/scenario_results.parquet (per-rep metrics for all policies)
  - outputs/scenario_summary.json (mean +/- 95% CI per policy/metric)
  - figures/scenario_compare_waits.png
  - figures/scenario_compare_throughput.png
  - figures/scenario_compare_utilization.png
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import joblib

import data_io as io
from phase5_simulator import (
    SimConfig, ClinicSim, build_schedule, attach_noshow_probs,
    run_replications, SLOT_LEN_MIN
)


P_THRESH = 0.30   # appointments above this predicted no-show prob are "high risk"


def policy_baseline(schedule: pd.DataFrame, providers: pd.DataFrame,
                     patients: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    return schedule.copy()


def policy_nar(schedule: pd.DataFrame, providers: pd.DataFrame,
                patients: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Add standby patients (same network AND specialty match) to high-risk slots."""
    high_risk = schedule[schedule["p_noshow"] > P_THRESH]
    used_pat_ids = set(schedule["patient_id"])
    free_pool = patients[~patients["patient_id"].isin(used_pat_ids)].copy()

    # Index by (network, speccat_needed) so standbys match BOTH constraints.
    pool_by_key = {k: list(v) for k, v in
                    free_pool.groupby(["network", "speccat_needed"]).indices.items()}
    for k in pool_by_key:
        rng.shuffle(pool_by_key[k])
    cursor = {k: 0 for k in pool_by_key}

    extras = []
    prov_nets = providers.set_index("provider_id")["networks"].to_dict()
    for _, row in high_risk.iterrows():
        prov_net_set = set(prov_nets[row["provider_id"]].split("|"))
        prov_speccat = int(row["speccat"])
        chosen = None
        for net in prov_net_set:
            key = (net, prov_speccat)
            if key in cursor and cursor[key] < len(pool_by_key[key]):
                pat_idx = pool_by_key[key][cursor[key]]
                cursor[key] += 1
                chosen = patients.loc[pat_idx]
                break
        if chosen is None:
            continue
        extras.append({
            "patient_id": chosen["patient_id"],
            "provider_id": row["provider_id"],
            "speccat": row["speccat"],
            "patient_payer": chosen["payer"],
            "patient_network": chosen["network"],
            "scheduled_min": row["scheduled_min"],   # same slot as original
            "patient_idx": -1,
            "p_noshow": 0.05,    # standbys are eager — low no-show
            "Age": chosen["age"], "Hypertension": chosen["Hypertension"],
            "Diabetes": chosen["Diabetes"], "SMS_received": chosen["SMS_received"],
            "Alcoholism": 0, "Handicap": 0, "Gender": "F",
            "Scholarship": 0, "LeadDays": 0, "AppointmentDow": row["AppointmentDow"],
            "AnyChronic": int(chosen["Hypertension"] + chosen["Diabetes"] > 0),
            "AgeGroup": "?", "LeadBucket": "same_day",
            "prior_appts": 0, "prior_noshow_rate": 0.0,
            "is_standby": True,
        })
    if not extras:
        return schedule.copy()
    out = pd.concat([schedule.assign(is_standby=False),
                      pd.DataFrame(extras)], ignore_index=True)
    return out.sort_values(["provider_id", "scheduled_min"]).reset_index(drop=True)


def policy_rbs(schedule: pd.DataFrame, providers: pd.DataFrame,
                patients: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """For each high-risk appointment, drop the *next* slot at the same provider
    (insert a 30-min buffer). The dropped slot's patient is removed from this day."""
    s = schedule.sort_values(["provider_id", "scheduled_min"]).reset_index(drop=True)
    s["is_standby"] = False
    drop_idx = set()
    for prov_id, group in s.groupby("provider_id"):
        # group's index labels (positions in `s`)
        idx = group.sort_values("scheduled_min").index.tolist()
        for i in range(len(idx) - 1):
            this_idx, next_idx = idx[i], idx[i + 1]
            if s.loc[this_idx, "p_noshow"] > P_THRESH and this_idx not in drop_idx:
                drop_idx.add(next_idx)
    return s.drop(index=list(drop_idx)).reset_index(drop=True)


POLICIES = {
    "baseline": policy_baseline,
    "NAR (reassign no-show slots)": policy_nar,
    "RBS (risk-buffered schedule)": policy_rbs,
}


CLINIC_OPEN_MIN = 480   # 8 hours in minutes (re-export for clarity)


def overtime_hours(busy_min: float, n_servers: int, open_min: float = 480.0) -> float:
    """If total demand > capacity in regular hours, the excess equals overtime hours."""
    capacity_min = n_servers * open_min
    if busy_min <= capacity_min:
        return 0.0
    return (busy_min - capacity_min) / n_servers / 60.0


def run_one_policy(policy_name, policy_fn, providers, patients, base_schedule,
                    service_times, cfg, n_reps=30):
    rep_summaries = []
    per_visit_records = []         # for wait-by-stage and per-network analysis
    for rep in range(n_reps):
        rng = np.random.default_rng(cfg.seed + rep)
        sched = policy_fn(base_schedule, providers, patients, rng)
        rep_cfg = SimConfig(**{**cfg.__dict__, "seed": cfg.seed + rep})
        sim = ClinicSim(providers, sched, service_times, rep_cfg, policy=policy_name)
        visits = sim.run()
        completed = visits[~visits["no_show"]].copy()

        nurse_overtime = overtime_hours(sim.nurse_busy_min, rep_cfg.n_nurses)
        md_total_busy = float(sum(sim.md_busy_min.values()))
        md_overtime_hrs = overtime_hours(md_total_busy, len(sim.md_busy_min))

        rep_summaries.append({
            "rep": rep,
            "policy": policy_name,
            "n_scheduled": len(visits),
            "n_completed": len(completed),
            "n_no_show": int(visits["no_show"].sum()),
            "no_show_rate": visits["no_show"].mean(),
            "median_wait_min": completed["wait_to_md"].median(),
            "p90_wait_min": completed["wait_to_md"].quantile(0.90),
            "mean_wait_min": completed["wait_to_md"].mean(),
            "mean_wait_checkin": completed["wait_for_checkin"].mean(),
            "mean_wait_nurse": completed["wait_for_nurse"].mean(),
            "mean_wait_md_only": completed["wait_for_md"].mean(),
            "mean_md_util": float(np.mean([
                min(1.0, sim.md_busy_min[p] / 480) for p in sim.md_busy_min
            ])),
            "checkin_util_capped": min(1.0, sim.checkin_busy_min / (rep_cfg.n_checkin * 480)),
            "nurse_util_capped": min(1.0, sim.nurse_busy_min / (rep_cfg.n_nurses * 480)),
            "nurse_overtime_hrs": nurse_overtime,
            "md_overtime_hrs": md_overtime_hrs,
            "throughput": len(completed),
        })
        # Per-visit records for downstream charts
        completed["policy"] = policy_name
        completed["rep"] = rep
        per_visit_records.append(
            completed[["policy", "rep", "patient_payer", "patient_network",
                        "speccat", "wait_for_checkin", "wait_for_nurse",
                        "wait_for_md", "wait_to_md"]]
        )

    visits_df = pd.concat(per_visit_records, ignore_index=True)
    return pd.DataFrame(rep_summaries), visits_df


def summarize(results: pd.DataFrame) -> dict:
    out = {}
    for policy, g in results.groupby("policy"):
        out[policy] = {}
        for metric in ["throughput", "no_show_rate", "median_wait_min",
                        "p90_wait_min", "mean_md_util",
                        "nurse_util_capped", "nurse_overtime_hrs",
                        "md_overtime_hrs", "mean_wait_checkin",
                        "mean_wait_nurse", "mean_wait_md_only"]:
            vals = g[metric].dropna().values
            if len(vals) == 0:
                continue
            out[policy][metric] = {
                "mean": float(vals.mean()),
                "ci_low": float(np.percentile(vals, 2.5)),
                "ci_high": float(np.percentile(vals, 97.5)),
                "std": float(vals.std()),
            }
    return out


def plot_compare(results: pd.DataFrame, metric: str, ylabel: str, title: str,
                  out_path):
    order = list(POLICIES.keys())
    data = [results.loc[results["policy"] == p, metric].dropna().values for p in order]
    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(data, tick_labels=order, patch_artist=True, widths=0.5)
    colors = ["#888", "#2A9D8F", "#E76F51"]
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.7)
    means = [d.mean() for d in data]
    ax.scatter(range(1, len(order) + 1), means, marker="D", s=70,
                color="black", zorder=5, label="mean")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(alpha=0.3, axis="y")
    plt.xticks(rotation=10, ha="right")
    ax.legend(loc="best")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


def plot_summary_grid(results: pd.DataFrame, out_path):
    """Single-figure 2x2 dashboard."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    panels = [
        ("throughput", "Completed visits per day", "Throughput"),
        ("median_wait_min", "Minutes", "Median wait to physician"),
        ("p90_wait_min", "Minutes", "90th-percentile wait to physician"),
        ("mean_md_util", "Utilization (0-1)", "Average MD utilization"),
    ]
    order = list(POLICIES.keys())
    colors = ["#888", "#2A9D8F", "#E76F51"]
    for ax, (metric, ylabel, title) in zip(axes.flat, panels):
        data = [results.loc[results["policy"] == p, metric].dropna().values for p in order]
        bp = ax.boxplot(data, tick_labels=order, patch_artist=True, widths=0.55)
        for patch, c in zip(bp["boxes"], colors):
            patch.set_facecolor(c); patch.set_alpha(0.7)
        means = [d.mean() for d in data]
        ax.scatter(range(1, len(order) + 1), means, marker="D", s=60,
                    color="black", zorder=5)
        ax.set_ylabel(ylabel); ax.set_title(title)
        ax.grid(alpha=0.3, axis="y")
        plt.setp(ax.get_xticklabels(), rotation=10, ha="right")
    plt.suptitle("Policy comparison (30 replications per scenario)", y=1.00, fontsize=14)
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

    cfg = SimConfig(seed=100)

    # Build a single base schedule + p(no-show) once; same demand stream across policies
    rng = np.random.default_rng(cfg.seed)
    base_schedule_raw = build_schedule(providers, patients, cfg, rng)
    base_schedule = attach_noshow_probs(base_schedule_raw, patients, model, rng)
    print(f"Base schedule: {len(base_schedule)} appointments; "
          f"{(base_schedule['p_noshow'] > P_THRESH).sum()} flagged high-risk (p>{P_THRESH}).")

    all_results = []
    all_visits = []
    for name, fn in POLICIES.items():
        print(f"\nRunning policy: {name}")
        df, vdf = run_one_policy(name, fn, providers, patients, base_schedule,
                                   service_times, cfg, n_reps=30)
        print(f"  throughput mean={df['throughput'].mean():.1f}, "
              f"median_wait mean={df['median_wait_min'].mean():.1f} min, "
              f"MD util mean={df['mean_md_util'].mean():.3f}, "
              f"nurse OT hrs={df['nurse_overtime_hrs'].mean():.2f}")
        all_results.append(df)
        all_visits.append(vdf)
    results = pd.concat(all_results, ignore_index=True)
    visits = pd.concat(all_visits, ignore_index=True)
    results.to_parquet(io.OUT / "scenario_results.parquet")
    visits.to_parquet(io.OUT / "scenario_visits.parquet")

    summary = summarize(results)
    with open(io.OUT / "scenario_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Individual figures
    plot_compare(results, "median_wait_min", "Minutes",
                  "Median wait to physician (lower is better)",
                  io.FIG / "scenario_compare_waits.png")
    plot_compare(results, "throughput", "Completed visits per day",
                  "Throughput (higher is better)",
                  io.FIG / "scenario_compare_throughput.png")
    plot_compare(results, "mean_md_util", "Utilization (0-1)",
                  "Average MD utilization",
                  io.FIG / "scenario_compare_utilization.png")
    plot_summary_grid(results, io.FIG / "scenario_dashboard.png")
    plot_tradeoff(results, io.FIG / "scenario_tradeoff.png")
    plot_wait_by_stage(results, io.FIG / "scenario_wait_by_stage.png")
    plot_per_network(visits, io.FIG / "scenario_per_payer.png")
    plot_overtime(results, io.FIG / "scenario_overtime.png")

    print("\nPhase 6 complete. Summary written to outputs/scenario_summary.json")


def plot_wait_by_stage(results: pd.DataFrame, out_path):
    """Stacked bar: mean wait per stage, per policy."""
    order = list(POLICIES.keys())
    g = results.groupby("policy")[["mean_wait_checkin", "mean_wait_nurse",
                                     "mean_wait_md_only"]].mean()
    g = g.reindex(order)
    fig, ax = plt.subplots(figsize=(8, 5))
    bars1 = ax.bar(g.index, g["mean_wait_checkin"], label="Wait for check-in",
                    color="#9CC9E5")
    bars2 = ax.bar(g.index, g["mean_wait_nurse"], bottom=g["mean_wait_checkin"],
                    label="Wait for nurse intake", color="#F4A261")
    bars3 = ax.bar(g.index, g["mean_wait_md_only"],
                    bottom=g["mean_wait_checkin"] + g["mean_wait_nurse"],
                    label="Wait for physician", color="#E76F51")
    totals = g.sum(axis=1)
    for i, t in enumerate(totals):
        ax.text(i, t + 1, f"{t:.0f} min", ha="center", fontweight="bold")
    ax.set_ylabel("Mean wait (minutes)")
    ax.set_title("Where the wait is spent: stage-by-stage breakdown")
    ax.legend(loc="best")
    ax.grid(alpha=0.3, axis="y")
    plt.xticks(rotation=10, ha="right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


def plot_per_network(visits: pd.DataFrame, out_path):
    """Two-panel equity chart:
     (left) median wait by payer, per policy — shows wait is similar across payers
            because each patient is bound to one provider.
     (right) average daily visits per payer, vs the population payer share —
            shows that uninsured patients (7% of population) get only ~7% of visits
            *because* the 5 Medicaid-accepting providers are themselves saturated;
            in a higher-demand scenario this would be where scarcity bites first.
    """
    order = list(POLICIES.keys())
    payers = ["private", "public", "uninsured"]
    colors = {"private": "#4C9BE8", "public": "#F4A261", "uninsured": "#E76F51"}
    population_share = {"private": 0.584, "public": 0.343, "uninsured": 0.073}

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))

    # LEFT — median wait by payer
    ax = axes[0]
    width = 0.25
    x = np.arange(len(order))
    for i, payer in enumerate(payers):
        vals = []
        for policy in order:
            sub = visits[(visits["policy"] == policy) & (visits["patient_payer"] == payer)]
            vals.append(sub["wait_to_md"].median() if len(sub) else 0)
        ax.bar(x + (i - 1) * width, vals, width, label=payer.capitalize(),
                color=colors[payer], edgecolor="black", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(order, rotation=10, ha="right")
    ax.set_ylabel("Median wait to physician (minutes)")
    ax.set_title("Wait time by patient payer")
    ax.legend(title="Payer")
    ax.grid(alpha=0.3, axis="y")

    # RIGHT — share of visits served, per payer, vs population share
    ax = axes[1]
    n_reps = visits["rep"].nunique()
    counts = (visits.groupby(["policy", "patient_payer"]).size() / n_reps).unstack(fill_value=0)
    counts = counts.reindex(order)
    shares = counts.div(counts.sum(axis=1), axis=0)
    width = 0.25
    x = np.arange(len(order))
    for i, payer in enumerate(payers):
        ax.bar(x + (i - 1) * width, shares[payer], width,
                color=colors[payer], edgecolor="black", linewidth=0.5,
                label=f"{payer.capitalize()} (pop. share {population_share[payer]:.0%})")
        for j, p in enumerate(order):
            ax.text(j + (i - 1) * width, shares[payer].iloc[j] + 0.01,
                     f"{shares[payer].iloc[j]:.0%}", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(order, rotation=10, ha="right")
    ax.set_ylabel("Share of completed visits")
    ax.set_title("Share of completed visits by payer\n"
                  "(under-representation of uninsured = network-access problem)")
    ax.legend(title="Payer (population share)", loc="upper left", fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    ax.set_ylim(0, 0.85)

    plt.suptitle("Equity view: Uninsured patients are constrained by network "
                  "(5 of 20 providers accept them)", y=1.02, fontsize=12)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


def plot_overtime(results: pd.DataFrame, out_path):
    """Bar chart: nurse overtime hours per policy, per day."""
    order = list(POLICIES.keys())
    means = [results.loc[results["policy"] == p, "nurse_overtime_hrs"].mean() for p in order]
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    colors = ["#888", "#2A9D8F", "#E76F51"]
    bars = ax.bar(order, means, color=colors, edgecolor="black", linewidth=0.6)
    for b, v in zip(bars, means):
        ax.text(b.get_x() + b.get_width()/2, v + 0.05, f"{v:.2f} h",
                 ha="center", fontweight="bold")
    ax.set_ylabel("Nurse overtime hours per day (mean)")
    ax.set_title("Nurse overtime cost of each policy\n"
                  "(0 = nurses finish within 8-hour shift)")
    ax.grid(alpha=0.3, axis="y")
    ax.axhline(0, color="black", lw=0.5)
    plt.xticks(rotation=10, ha="right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


def plot_tradeoff(results: pd.DataFrame, out_path):
    """Scatter of (median wait, throughput) per replication, colored by policy."""
    fig, ax = plt.subplots(figsize=(8.5, 6))
    colors = {"baseline": "#888",
              "NAR (reassign no-show slots)": "#2A9D8F",
              "RBS (risk-buffered schedule)": "#E76F51"}
    for policy, g in results.groupby("policy"):
        ax.scatter(g["median_wait_min"], g["throughput"],
                    s=60, color=colors[policy], alpha=0.55,
                    edgecolor="black", linewidth=0.5, label=policy)
        # mark the mean
        ax.scatter(g["median_wait_min"].mean(), g["throughput"].mean(),
                    s=300, color=colors[policy], marker="*",
                    edgecolor="black", linewidth=1.5, zorder=5)
    ax.set_xlabel("Median wait to physician (minutes)  → worse")
    ax.set_ylabel("Completed visits per day  → better")
    ax.set_title("Throughput-vs-wait tradeoff across policies\n"
                  "(30 replications each; ★ = mean)")
    ax.legend(loc="best", framealpha=0.95)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


if __name__ == "__main__":
    main()

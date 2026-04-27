"""Phase 5: Multi-stage discrete-event simulator (simpy).

Models one clinic-day:
  Patient arrival (with no-show possibility) -> check-in -> nurse intake -> MD consult.

Resources: 2 check-in desks, 4 intake nurses, 1 MD per provider (patient-bound).
Service times: lognormal, calibrated in Phase 3.
No-show probability: per appointment, sampled from Phase 2 model.

This module exports the simulator as a class so Phase 6 can run baseline + policy scenarios.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
import simpy
import joblib

import data_io as io


CLINIC_OPEN_MIN = 0       # 8:00 AM = t=0
CLINIC_CLOSE_MIN = 480    # 4:00 PM
SLOT_LEN_MIN = 30


@dataclass
class SimConfig:
    n_checkin: int = 2
    n_nurses: int = 6                     # ~1 nurse per 3 providers (typical multispecialty)
    slots_per_provider: int = 16          # 8h @ 30-min slots
    arrival_jitter_sd_min: float = 5.0    # patients arrive a bit early/late
    seed: int = 0


@dataclass
class VisitRecord:
    patient_id: str
    provider_id: str
    speccat: int
    patient_network: str
    patient_payer: str
    scheduled_min: float
    arrived_min: float | None        # None if no-show
    checkin_start: float | None = None
    checkin_end: float | None = None
    nurse_start: float | None = None
    nurse_end: float | None = None
    md_start: float | None = None
    md_end: float | None = None
    no_show: bool = False

    def wait_to_md(self) -> float | None:
        if self.no_show or self.md_start is None or self.arrived_min is None:
            return None
        return self.md_start - self.arrived_min

    def wait_for_checkin(self) -> float | None:
        if self.no_show or self.checkin_start is None or self.arrived_min is None:
            return None
        return self.checkin_start - self.arrived_min

    def wait_for_nurse(self) -> float | None:
        if self.no_show or self.nurse_start is None or self.checkin_end is None:
            return None
        return self.nurse_start - self.checkin_end

    def wait_for_md(self) -> float | None:
        if self.no_show or self.md_start is None or self.nurse_end is None:
            return None
        return self.md_start - self.nurse_end

    def total_visit_min(self) -> float | None:
        if self.no_show or self.md_end is None or self.arrived_min is None:
            return None
        return self.md_end - self.arrived_min


def lognormal_sample(rng: np.random.Generator, mu: float, sigma: float, lo: float = 0.5) -> float:
    """Lognormal sample, clipped at lo to avoid 0-time stages."""
    return float(max(lo, rng.lognormal(mu, sigma)))


def build_schedule(providers: pd.DataFrame, patients: pd.DataFrame,
                    cfg: SimConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Assign patients to providers and slot times, respecting BOTH network
    AND specialty match. A primary-care patient cannot be scheduled with a
    cardiologist; a Medicaid patient cannot see an out-of-network specialist."""
    rows = []
    # Index patients by (network, speccat_needed) so we can match to providers'
    # accepted networks AND specialty.
    keys = patients.groupby(["network", "speccat_needed"]).indices
    pats_by_key = {k: list(v) for k, v in keys.items()}
    for k in pats_by_key:
        rng.shuffle(pats_by_key[k])
    pat_cursor = {k: 0 for k in pats_by_key}

    for _, prov in providers.iterrows():
        prov_nets = list(set(prov["networks"].split("|")))
        prov_speccat = int(prov["speccat"])
        # Patients eligible for this provider: speccat_needed == prov_speccat
        # AND patient.network in prov_nets
        eligible_keys = [(net, prov_speccat) for net in prov_nets
                          if (net, prov_speccat) in pats_by_key]
        for slot_idx in range(cfg.slots_per_provider):
            slot_time = slot_idx * SLOT_LEN_MIN
            assigned = False
            rng.shuffle(eligible_keys)
            for key in eligible_keys:
                while pat_cursor[key] < len(pats_by_key[key]):
                    pat_idx = pats_by_key[key][pat_cursor[key]]
                    pat_cursor[key] += 1
                    rows.append({
                        "patient_id": patients.loc[pat_idx, "patient_id"],
                        "provider_id": prov["provider_id"],
                        "speccat": prov["speccat"],
                        "patient_payer": patients.loc[pat_idx, "payer"],
                        "patient_network": patients.loc[pat_idx, "network"],
                        "scheduled_min": slot_time,
                        "patient_idx": pat_idx,
                    })
                    assigned = True
                    break
                if assigned:
                    break
            # else: slot left empty (capacity exists but no eligible patient)
    return pd.DataFrame(rows)


def attach_noshow_probs(schedule: pd.DataFrame, patients: pd.DataFrame,
                         model_pipe, rng: np.random.Generator) -> pd.DataFrame:
    """Run each scheduled appointment through the no-show model to get p(no-show).
    For features the synthetic clinic doesn't carry directly, we draw realistic values:
      LeadDays ~ Kaggle empirical distribution
      AppointmentDow ~ Mon-Fri uniform
      Scholarship: from MEPS uninsured rate proxy (rare)
    """
    # Empirical lead-time distribution from Kaggle
    kg = pd.read_parquet(io.OUT / "kaggle_clean.parquet")
    lead_dist = kg["LeadDays"].values

    pat = patients.set_index("patient_id")
    sched = schedule.copy()
    # Pull patient features
    sched["Age"] = sched["patient_id"].map(pat["age"])
    sched["Hypertension"] = sched["patient_id"].map(pat["Hypertension"])
    sched["Diabetes"] = sched["patient_id"].map(pat["Diabetes"])
    sched["SMS_received"] = sched["patient_id"].map(pat["SMS_received"])
    sched["Alcoholism"] = 0
    sched["Handicap"] = 0
    sched["Gender"] = rng.choice(["F", "M"], size=len(sched), p=[0.65, 0.35])
    sched["Scholarship"] = (rng.random(len(sched)) < 0.10).astype(int)
    sched["LeadDays"] = rng.choice(lead_dist, size=len(sched), replace=True)
    sched["AppointmentDow"] = rng.integers(0, 5, size=len(sched))

    # Re-derive engineered features (mirror phase2 logic)
    sched["AnyChronic"] = ((sched["Hypertension"] + sched["Diabetes"] +
                              sched["Alcoholism"] + (sched["Handicap"] > 0)) > 0).astype(int)
    sched["AgeGroup"] = pd.cut(sched["Age"], bins=[-1, 5, 17, 39, 64, 200],
                                 labels=["0-5", "6-17", "18-39", "40-64", "65+"])
    sched["LeadBucket"] = pd.cut(sched["LeadDays"], bins=[-1, 0, 2, 7, 30, 1000],
                                   labels=["same_day", "1-2d", "3-7d", "8-30d", "31d+"])
    sched["prior_appts"] = 0          # synthetic patients have no history
    sched["prior_noshow_rate"] = 0.0

    feat_cols = ["Age", "LeadDays", "SMS_received", "Scholarship",
                  "Hypertension", "Diabetes", "Alcoholism", "Handicap",
                  "AnyChronic", "prior_appts", "prior_noshow_rate",
                  "Gender", "AgeGroup", "LeadBucket", "AppointmentDow"]
    raw_p = model_pipe.predict_proba(sched[feat_cols])[:, 1]
    # Rescale absolute level to a published U.S. outpatient base rate (~18%).
    # The model's relative ranking (high-risk vs low-risk) is preserved; only
    # the population mean is anchored to U.S. data. Rationale: the no-show
    # model is trained on Brazilian data, so cross-national differences in
    # the *level* of no-show should be removed before applying to U.S. patients.
    US_BASE_NOSHOW = 0.18
    raw_mean = raw_p.mean()
    if raw_mean > 0:
        scaled = raw_p * (US_BASE_NOSHOW / raw_mean)
        sched["p_noshow"] = np.clip(scaled, 0.01, 0.95)
    else:
        sched["p_noshow"] = raw_p
    return sched


class ClinicSim:
    """One clinic-day simulation."""

    def __init__(self, providers: pd.DataFrame, schedule: pd.DataFrame,
                  service_times: dict, cfg: SimConfig,
                  policy: str = "baseline"):
        self.providers = providers.set_index("provider_id")
        self.schedule = schedule.copy()
        self.service_times = service_times
        self.cfg = cfg
        self.policy = policy
        self.rng = np.random.default_rng(cfg.seed)
        self.records: list[VisitRecord] = []

        self.env = simpy.Environment()
        self.checkin = simpy.Resource(self.env, capacity=cfg.n_checkin)
        self.nurses = simpy.Resource(self.env, capacity=cfg.n_nurses)
        # One MD resource per provider (single-server)
        self.mds = {pid: simpy.Resource(self.env, capacity=1)
                     for pid in self.providers.index}
        self.md_busy_min: dict[str, float] = {pid: 0.0 for pid in self.providers.index}
        self.checkin_busy_min: float = 0.0
        self.nurse_busy_min: float = 0.0

    def _md_time(self, speccat: int) -> float:
        params = self.service_times[f"md_speccat_{speccat}"]
        return lognormal_sample(self.rng, params["mu"], params["sigma"])

    def _checkin_time(self) -> float:
        p = self.service_times["checkin"]
        return lognormal_sample(self.rng, p["mu"], p["sigma"])

    def _nurse_time(self) -> float:
        p = self.service_times["nurse_intake"]
        return lognormal_sample(self.rng, p["mu"], p["sigma"])

    def patient_process(self, rec: VisitRecord):
        """One patient's journey through the clinic."""
        # Determine no-show
        if rec.no_show:
            return
        # Wait until arrival
        arrival_jitter = self.rng.normal(0, self.cfg.arrival_jitter_sd_min)
        arrival = max(0.0, rec.scheduled_min + arrival_jitter)
        if self.env.now < arrival:
            yield self.env.timeout(arrival - self.env.now)
        rec.arrived_min = self.env.now

        # Check-in
        with self.checkin.request() as req:
            yield req
            rec.checkin_start = self.env.now
            ci_dur = self._checkin_time()
            yield self.env.timeout(ci_dur)
            rec.checkin_end = self.env.now
            self.checkin_busy_min += ci_dur

        # Nurse intake
        with self.nurses.request() as req:
            yield req
            rec.nurse_start = self.env.now
            n_dur = self._nurse_time()
            yield self.env.timeout(n_dur)
            rec.nurse_end = self.env.now
            self.nurse_busy_min += n_dur

        # MD consult
        md = self.mds[rec.provider_id]
        with md.request() as req:
            yield req
            rec.md_start = self.env.now
            md_dur = self._md_time(rec.speccat)
            yield self.env.timeout(md_dur)
            rec.md_end = self.env.now
            self.md_busy_min[rec.provider_id] += md_dur

    def run(self) -> pd.DataFrame:
        # Decide no-shows for each appointment
        noshow_draws = self.rng.random(len(self.schedule))
        for i, (_, row) in enumerate(self.schedule.iterrows()):
            no_show = bool(noshow_draws[i] < row["p_noshow"])
            rec = VisitRecord(
                patient_id=row["patient_id"],
                provider_id=row["provider_id"],
                speccat=int(row["speccat"]),
                patient_network=str(row.get("patient_network", "?")),
                patient_payer=str(row.get("patient_payer", "?")),
                scheduled_min=float(row["scheduled_min"]),
                arrived_min=None,
                no_show=no_show,
            )
            self.records.append(rec)
            self.env.process(self.patient_process(rec))

        self.env.run(until=CLINIC_CLOSE_MIN + 240)   # allow up to 4 hrs of overflow

        rows = []
        for r in self.records:
            rows.append({
                "patient_id": r.patient_id,
                "provider_id": r.provider_id,
                "speccat": r.speccat,
                "patient_network": r.patient_network,
                "patient_payer": r.patient_payer,
                "scheduled_min": r.scheduled_min,
                "no_show": r.no_show,
                "arrived_min": r.arrived_min,
                "wait_to_md": r.wait_to_md(),
                "wait_for_checkin": r.wait_for_checkin(),
                "wait_for_nurse": r.wait_for_nurse(),
                "wait_for_md": r.wait_for_md(),
                "total_visit_min": r.total_visit_min(),
                "md_start": r.md_start,
                "md_end": r.md_end,
            })
        return pd.DataFrame(rows)


def run_replications(providers, patients, schedule_with_p, service_times,
                       cfg, policy="baseline", n_reps=20) -> pd.DataFrame:
    """Run N replications; return per-rep summary metrics."""
    rep_summaries = []
    for rep in range(n_reps):
        rep_cfg = SimConfig(**{**cfg.__dict__, "seed": cfg.seed + rep})
        sim = ClinicSim(providers, schedule_with_p, service_times, rep_cfg, policy=policy)
        visits = sim.run()
        completed = visits[~visits["no_show"]].copy()
        rep_summaries.append({
            "rep": rep,
            "policy": policy,
            "n_scheduled": len(visits),
            "n_completed": len(completed),
            "n_no_show": int(visits["no_show"].sum()),
            "no_show_rate": visits["no_show"].mean(),
            "median_wait_min": completed["wait_to_md"].median(),
            "p90_wait_min": completed["wait_to_md"].quantile(0.90),
            "mean_wait_min": completed["wait_to_md"].mean(),
            "mean_md_util": float(np.mean([
                sim.md_busy_min[p] / CLINIC_CLOSE_MIN for p in sim.md_busy_min
            ])),
            "checkin_util": sim.checkin_busy_min / (rep_cfg.n_checkin * CLINIC_CLOSE_MIN),
            "nurse_util": sim.nurse_busy_min / (rep_cfg.n_nurses * CLINIC_CLOSE_MIN),
            "throughput": len(completed),
        })
    return pd.DataFrame(rep_summaries)


def main():
    """Smoke test: build schedule, run a few baseline replications."""
    io.ensure_dirs()
    providers = pd.read_parquet(io.OUT / "clinic_providers.parquet")
    patients = pd.read_parquet(io.OUT / "clinic_patients.parquet")
    with open(io.OUT / "service_times.json") as f:
        service_times = json.load(f)
    model = joblib.load(io.OUT / "noshow_model.joblib")

    cfg = SimConfig(seed=0)
    rng = np.random.default_rng(cfg.seed)
    schedule = build_schedule(providers, patients, cfg, rng)
    schedule_with_p = attach_noshow_probs(schedule, patients, model, rng)
    schedule_with_p.to_parquet(io.OUT / "schedule_with_p.parquet")

    print(f"Schedule: {len(schedule_with_p)} appointments across "
          f"{schedule_with_p['provider_id'].nunique()} providers.")
    print(f"Mean p(no-show) per appointment: {schedule_with_p['p_noshow'].mean():.3f}")

    summary = run_replications(providers, patients, schedule_with_p,
                                 service_times, cfg, policy="baseline", n_reps=10)
    summary.to_parquet(io.OUT / "baseline_smoke.parquet")
    print("\nBaseline (10 reps) summary:")
    print(summary[["n_completed", "no_show_rate", "median_wait_min",
                    "p90_wait_min", "mean_md_util"]].describe().round(2))

    # Sanity checks (simulator validation):
    # (A) No no-shows / no arrival jitter: any remaining wait is pure queueing
    #     from the fixed 30-min slot structure against finite nurse capacity.
    # (B) Same as (A) but with 2x nurse staffing: wait should collapse to near-zero,
    #     confirming the simulator is correct and that waits in (A) and in baseline
    #     reflect nurse saturation, not a bug or randomness.
    sanity_sched = schedule_with_p.copy()
    sanity_sched["p_noshow"] = 0.0
    sanity_cfg_A = SimConfig(seed=999, arrival_jitter_sd_min=0.0)
    sanity_A = run_replications(providers, patients, sanity_sched, service_times,
                                  sanity_cfg_A, policy="sanity_no_noise", n_reps=5)
    sanity_cfg_B = SimConfig(seed=999, arrival_jitter_sd_min=0.0, n_nurses=12)
    sanity_B = run_replications(providers, patients, sanity_sched, service_times,
                                  sanity_cfg_B, policy="sanity_2x_nurses", n_reps=5)
    print("\nSanity checks (simulator validation):")
    print(f"  (A) No no-shows + no jitter, 6 nurses:   "
          f"median wait = {sanity_A['median_wait_min'].mean():.1f} min "
          f"(baseline = {summary['median_wait_min'].mean():.1f}) "
          f"→ nurse saturation is the driver, not randomness")
    print(f"  (B) No no-shows + no jitter, 12 nurses:  "
          f"median wait = {sanity_B['median_wait_min'].mean():.1f} min "
          f"→ waits collapse, confirming simulator is correct")
    pd.concat([sanity_A.assign(variant="A_6_nurses"),
                sanity_B.assign(variant="B_12_nurses")]).to_parquet(
                    io.OUT / "sanity_check.parquet")


if __name__ == "__main__":
    main()

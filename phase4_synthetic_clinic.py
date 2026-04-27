"""Phase 4: Build the synthetic outpatient clinic.

Constructs:
- A clinic of N providers sampled from CMS, with specialty mix matching
  a realistic multispecialty outpatient practice.
- Three insurance networks (A: broad-private, B: broad-public, C: narrow/Medicaid).
  Provider network membership is sampled to mimic real overlap (most providers
  in A+B, a subset in C only).
- A patient population whose payer mix matches MEPS HC-243 (~58/35/7).

Outputs:
- outputs/clinic_providers.parquet
- outputs/clinic_patients.parquet
- outputs/clinic_config.json (specialty mix, network mapping, capacity)
- figures/clinic_overview.png
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import data_io as io


# Map CMS pri_spec -> SPECCAT (NAMCS specialty category) for service-time lookup
SPEC_TO_SPECCAT = {
    "FAMILY PRACTICE": 1, "INTERNAL MEDICINE": 1, "GENERAL PRACTICE": 1,
    "PEDIATRIC MEDICINE": 1, "NURSE PRACTITIONER": 1, "PHYSICIAN ASSISTANT": 1,
    "OBSTETRICS/GYNECOLOGY": 2, "ORTHOPEDIC SURGERY": 2, "OPHTHALMOLOGY": 2,
    "UROLOGY": 2,
    "CARDIOVASCULAR DISEASE (CARDIOLOGY)": 3, "DERMATOLOGY": 3,
    "ENDOCRINOLOGY": 3, "GASTROENTEROLOGY": 3, "NEUROLOGY": 3,
    "PSYCHIATRY": 3, "RHEUMATOLOGY": 3, "PULMONARY DISEASE": 3,
}


# Target specialty mix for the synthetic clinic (counts of providers)
SPECIALTY_TARGET = {
    "FAMILY PRACTICE": 4,
    "INTERNAL MEDICINE": 4,
    "PEDIATRIC MEDICINE": 2,
    "OBSTETRICS/GYNECOLOGY": 2,
    "CARDIOVASCULAR DISEASE (CARDIOLOGY)": 2,
    "DERMATOLOGY": 1,
    "ENDOCRINOLOGY": 1,
    "ORTHOPEDIC SURGERY": 2,
    "PSYCHIATRY": 1,
    "GASTROENTEROLOGY": 1,
}  # 20 providers total


def sample_providers(cms: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for spec, n in SPECIALTY_TARGET.items():
        pool = cms[cms["pri_spec"] == spec]
        if len(pool) < n:
            raise ValueError(f"Not enough CMS providers for {spec}")
        picks = pool.sample(n=n, random_state=int(rng.integers(0, 1e9)))
        rows.append(picks)
    df = pd.concat(rows, ignore_index=True)
    df["provider_id"] = [f"P{i:02d}" for i in range(len(df))]
    df["speccat"] = df["pri_spec"].map(SPEC_TO_SPECCAT)
    return df[["provider_id", "NPI", "pri_spec", "speccat",
                "Facility Name" if "Facility Name" in df.columns else "org_pac_id",
                "City/Town", "State"]] if "Facility Name" in df.columns else \
           df[["provider_id", "NPI", "pri_spec", "speccat",
                "org_pac_id", "City/Town", "State"]]


def assign_networks(providers: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """3 insurance networks. A=broad-private, B=broad-public, C=narrow/Medicaid.
    Most providers in {A,B}; a few specialists in {A only} (out-of-network for public);
    a few PCPs also in {C}.
    """
    networks = []
    for _, p in providers.iterrows():
        nets = set()
        # Most providers accept both private and public for primary care
        if p["speccat"] == 1:
            nets.update(["A", "B"])
            if rng.random() < 0.5:
                nets.add("C")
        elif p["speccat"] == 2:
            # surgical specialists: 70% A+B, 30% A only (private-only)
            if rng.random() < 0.7:
                nets.update(["A", "B"])
            else:
                nets.add("A")
        else:  # medical specialty
            # 60% A+B, 30% A only, 10% A+B+C
            r = rng.random()
            if r < 0.6:
                nets.update(["A", "B"])
            elif r < 0.9:
                nets.add("A")
            else:
                nets.update(["A", "B", "C"])
        networks.append("|".join(sorted(nets)))
    providers = providers.copy()
    providers["networks"] = networks
    return providers


def generate_patients(n_patients: int, rng: np.random.Generator) -> pd.DataFrame:
    """Sample patients whose payer mix matches MEPS HC-243.
    Payer -> insurance network mapping:
      private -> A (and possibly C if dual-eligible)
      public  -> B (Medicare/Medicaid majority)
      uninsured -> C (charity / Medicaid pending)
    """
    meps = pd.read_parquet(io.OUT / "meps_payer.parquet")
    pct_private = (meps["INSCOV22"] == 1).mean()
    pct_public = (meps["INSCOV22"] == 2).mean()
    pct_uninsured = (meps["INSCOV22"] == 3).mean()

    payer_codes = rng.choice(["private", "public", "uninsured"],
                              size=n_patients,
                              p=[pct_private, pct_public, pct_uninsured])
    payer_to_net = {"private": "A", "public": "B", "uninsured": "C"}
    networks = [payer_to_net[p] for p in payer_codes]

    # Visit-type mix calibrated to roughly match the synthetic clinic's specialty
    # capacity (10 PCPs / 4 surgical / 6 medical-specialty providers, 16 slots each):
    #   primary:  10*16=160 / 320 = 50%
    #   surgical:  4*16=64  / 320 = 20%
    #   medical:   6*16=96  / 320 = 30%
    # This matches NAMCS visit-type distribution (~55% primary care visits) within
    # a few percentage points and avoids artificial unmet demand confounding the
    # policy comparison.
    visit_type = rng.choice(["primary", "surgical", "medical"], size=n_patients,
                             p=[0.50, 0.20, 0.30])
    speccat_needed = pd.Series(visit_type).map(
        {"primary": 1, "surgical": 2, "medical": 3}).values

    # Age and chronic-condition prevalences calibrated from Kaggle (broadly U.S.-typical)
    ages = rng.integers(0, 90, size=n_patients)
    has_hypertension = (rng.random(n_patients) < 0.20).astype(int)
    has_diabetes = (rng.random(n_patients) < 0.11).astype(int)
    sms_received = (rng.random(n_patients) < 0.32).astype(int)

    return pd.DataFrame({
        "patient_id": [f"PT{i:06d}" for i in range(n_patients)],
        "age": ages,
        "payer": payer_codes,
        "network": networks,
        "visit_type": visit_type,
        "speccat_needed": speccat_needed,
        "Hypertension": has_hypertension,
        "Diabetes": has_diabetes,
        "SMS_received": sms_received,
    })


def plot_overview(providers, patients, out_path):
    fig, axes = plt.subplots(1, 3, figsize=(17, 4.5))
    # Provider specialty mix
    sp_counts = providers["pri_spec"].value_counts()
    # Shorten only the unwieldy "CARDIOVASCULAR DISEASE (CARDIOLOGY)" label
    short_labels = [s.replace("CARDIOVASCULAR DISEASE (CARDIOLOGY)", "CARDIOLOGY")
                       .replace("OBSTETRICS/GYNECOLOGY", "OB/GYN") for s in sp_counts.index]
    axes[0].barh(range(len(sp_counts)), sp_counts.values, color="steelblue")
    axes[0].set_yticks(range(len(sp_counts)))
    axes[0].set_yticklabels(short_labels, fontsize=9)
    axes[0].set_xlabel("Providers")
    axes[0].set_title(f"Synthetic clinic: {len(providers)} providers")
    axes[0].invert_yaxis()

    # Network membership
    nets = providers["networks"].value_counts()
    axes[1].bar(nets.index, nets.values, color="coral")
    axes[1].set_xlabel("Provider network membership")
    axes[1].set_ylabel("Providers")
    axes[1].set_title("Insurance-network coverage")

    # Patient payer mix
    pay = patients["payer"].value_counts()
    axes[2].pie(pay.values, labels=pay.index, autopct="%1.0f%%",
                colors=["#4C9BE8", "#F4A261", "#E76F51"])
    axes[2].set_title(f"Patient payer mix (n={len(patients):,})\nfrom MEPS HC-243")

    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def main():
    io.ensure_dirs()
    cms = pd.read_parquet(io.OUT / "cms_providers.parquet")
    rng = np.random.default_rng(42)

    providers = sample_providers(cms, rng)
    providers = assign_networks(providers, rng)

    # Patient population: enough to cover ~5 days of clinic at 16 slots * 20 providers = 1600
    patients = generate_patients(n_patients=2000, rng=rng)

    providers.to_parquet(io.OUT / "clinic_providers.parquet")
    patients.to_parquet(io.OUT / "clinic_patients.parquet")

    config = {
        "n_providers": len(providers),
        "n_patients": len(patients),
        "specialty_mix": SPECIALTY_TARGET,
        "slots_per_provider_per_day": 16,
        "clinic_hours": 8,
        "n_checkin_desks": 2,
        "n_intake_nurses": 4,
        "networks_explained": {
            "A": "Broad private (PPO-like)",
            "B": "Broad public (Medicare/Medicaid)",
            "C": "Narrow Medicaid / safety-net",
        },
        "payer_to_network": {"private": "A", "public": "B", "uninsured": "C"},
    }
    with open(io.OUT / "clinic_config.json", "w") as f:
        json.dump(config, f, indent=2)

    plot_overview(providers, patients, io.FIG / "clinic_overview.png")

    print(f"Clinic built: {len(providers)} providers, {len(patients)} patients.")
    print(f"  Network coverage: {providers['networks'].value_counts().to_dict()}")
    print(f"  Payer mix: {patients['payer'].value_counts(normalize=True).round(3).to_dict()}")
    print(f"\nPhase 4 complete.")


if __name__ == "__main__":
    main()

"""Phase 1: Load and clean all four sources, print summary stats, cache to parquet."""
from __future__ import annotations
import pandas as pd
import data_io as io


def summarize_kaggle(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "unique_patients": df["PatientId"].nunique(),
        "date_range": (df["AppointmentDay"].min().date(), df["AppointmentDay"].max().date()),
        "no_show_rate": df["NoShow"].mean(),
        "median_lead_days": df["LeadDays"].median(),
        "p95_lead_days": df["LeadDays"].quantile(0.95),
        "sms_coverage": df["SMS_received"].mean(),
    }


def summarize_namcs(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "median_timemd_min": df["TIMEMD"].median(),
        "mean_timemd_min": df["TIMEMD"].mean(),
        "specialty_cats": df["SPECCAT"].nunique(),
    }


def summarize_meps(df: pd.DataFrame) -> dict:
    n = len(df)
    return {
        "rows": n,
        "pct_private": (df["INSCOV22"] == 1).sum() / n,
        "pct_public_only": (df["INSCOV22"] == 2).sum() / n,
        "pct_uninsured": (df["INSCOV22"] == 3).sum() / n,
        "pct_any_outpatient_visit": (df["OBTOTV22"] > 0).mean(),
        "mean_outpatient_visits": df["OBTOTV22"].mean(),
        "mean_md_visits": df["OBDRV22"].mean(),
    }


def summarize_cms(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "unique_specialties": df["pri_spec"].nunique(),
        "top5_specialties": df["pri_spec"].value_counts().head(5).to_dict(),
        "unique_groups": df["org_pac_id"].nunique(),
        "unique_states": df["State"].nunique(),
    }


def main():
    io.ensure_dirs()

    print("Loading Kaggle...")
    kaggle = io.load_kaggle()
    kaggle.to_parquet(io.OUT / "kaggle_clean.parquet")
    k_stats = summarize_kaggle(kaggle)
    print(f"  {k_stats}")

    print("Loading NAMCS 2019...")
    namcs = io.load_namcs()
    keep = ["TIMEMD", "SPECCAT", "MAJOR", "VMONTH", "AGE", "SEX", "PAYTYPER",
            "USETOBAC", "APPTTIME"]
    keep = [c for c in keep if c in namcs.columns]
    namcs_slim = namcs[keep].copy()
    namcs_slim.to_parquet(io.OUT / "namcs_slim.parquet")
    n_stats = summarize_namcs(namcs)
    print(f"  {n_stats}")

    print("Loading MEPS HC-243...")
    meps = io.load_meps_payer()
    meps.to_parquet(io.OUT / "meps_payer.parquet")
    m_stats = summarize_meps(meps)
    print(f"  {m_stats}")

    print("Loading CMS (streaming 711 MB, filtering to outpatient specialties)...")
    cms = io.load_cms_outpatient(n_sample=None)
    cms.to_parquet(io.OUT / "cms_providers.parquet")
    c_stats = summarize_cms(cms)
    print(f"  rows={c_stats['rows']}, specialties={c_stats['unique_specialties']}, "
          f"groups={c_stats['unique_groups']}, states={c_stats['unique_states']}")
    print(f"  top5 specialties: {c_stats['top5_specialties']}")

    # Write a markdown summary for the slides
    with open(io.OUT / "data_summary.md", "w") as f:
        f.write("# Data Preparation — Summary\n\n")
        f.write(f"## Kaggle (Brazilian no-show data, demand engine)\n")
        f.write(f"- Rows: {k_stats['rows']:,} | Unique patients: {k_stats['unique_patients']:,}\n")
        f.write(f"- Date range: {k_stats['date_range'][0]} to {k_stats['date_range'][1]}\n")
        f.write(f"- **No-show rate: {k_stats['no_show_rate']:.1%}** "
                f"(within the 15-30% range reported for U.S. outpatient clinics)\n")
        f.write(f"- Median lead time: {k_stats['median_lead_days']:.0f} days; "
                f"p95: {k_stats['p95_lead_days']:.0f} days\n")
        f.write(f"- SMS reminder coverage: {k_stats['sms_coverage']:.1%}\n\n")

        f.write(f"## NAMCS 2019 (U.S. service-time calibration)\n")
        f.write(f"- Rows: {n_stats['rows']:,}\n")
        f.write(f"- `TIMEMD` (minutes with physician): median {n_stats['median_timemd_min']:.1f}, "
                f"mean {n_stats['mean_timemd_min']:.1f}\n")
        f.write(f"- Specialty categories available: {n_stats['specialty_cats']}\n\n")

        f.write(f"## MEPS HC-243 (U.S. payer mix, calendar year 2022)\n")
        f.write(f"- Rows: {m_stats['rows']:,}\n")
        f.write(f"- **Payer mix used to assign insurance networks:**\n")
        f.write(f"  - Any private insurance: {m_stats['pct_private']:.1%}\n")
        f.write(f"  - Public only (Medicare/Medicaid/etc.): {m_stats['pct_public_only']:.1%}\n")
        f.write(f"  - Uninsured: {m_stats['pct_uninsured']:.1%}\n")
        f.write(f"- Had any outpatient visit in 2022: {m_stats['pct_any_outpatient_visit']:.1%}\n")
        f.write(f"- Mean outpatient visits / person: {m_stats['mean_outpatient_visits']:.2f}; "
                f"of which physician visits: {m_stats['mean_md_visits']:.2f}\n\n")

        f.write(f"## CMS Doctors & Clinicians (U.S. provider pool)\n")
        f.write(f"- After filtering to outpatient-relevant specialties: {c_stats['rows']:,} unique clinicians\n")
        f.write(f"- Unique groups (practices): {c_stats['unique_groups']:,}\n")
        f.write(f"- Unique states: {c_stats['unique_states']}\n")
        f.write(f"- Top 5 specialties:\n")
        for sp, n in c_stats['top5_specialties'].items():
            f.write(f"  - {sp}: {n:,}\n")

    print(f"\nPhase 1 complete. Artifacts in {io.OUT}")


if __name__ == "__main__":
    main()

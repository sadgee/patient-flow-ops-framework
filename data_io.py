"""Shared paths and loaders for all analysis phases."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets"
OUT = ROOT / "analysis" / "outputs"
FIG = ROOT / "analysis" / "figures"

KAGGLE_CSV = DATA / "KaggleV2-May-2016.csv"
NAMCS_DTA = DATA / "namcs" / "NAMCS2019-stata.dta"
MEPS_DTA = DATA / "meps" / "h243.dta"
CMS_CSV = DATA / "cms" / "DAC_NationalDownloadableFile.csv"


def load_kaggle() -> pd.DataFrame:
    df = pd.read_csv(KAGGLE_CSV, parse_dates=["ScheduledDay", "AppointmentDay"])
    df = df.rename(columns={
        "Hipertension": "Hypertension",
        "Handcap": "Handicap",
        "No-show": "NoShow",
    })
    df["NoShow"] = (df["NoShow"] == "Yes").astype(int)
    df["LeadDays"] = (df["AppointmentDay"].dt.normalize() - df["ScheduledDay"].dt.normalize()).dt.days
    df.loc[df["LeadDays"] < 0, "LeadDays"] = 0
    df = df[df["Age"] >= 0].copy()
    return df


def load_namcs() -> pd.DataFrame:
    return pd.read_stata(NAMCS_DTA, convert_categoricals=False)


def load_meps_payer() -> pd.DataFrame:
    """INSCOV22: 1=any private, 2=public only, 3=uninsured.
    OBTOTV22: total outpatient visits in 2022; OBDRV22: physician outpatient visits."""
    cols = ["DUPERSID", "AGE22X", "SEX", "INSCOV22", "OBTOTV22", "OBDRV22"]
    df = pd.read_stata(MEPS_DTA, columns=cols, convert_categoricals=False)
    return df


CMS_SPECS_OUTPATIENT = {
    "FAMILY PRACTICE", "INTERNAL MEDICINE", "GENERAL PRACTICE",
    "OBSTETRICS/GYNECOLOGY", "PEDIATRIC MEDICINE",
    "CARDIOVASCULAR DISEASE (CARDIOLOGY)", "DERMATOLOGY",
    "ENDOCRINOLOGY", "GASTROENTEROLOGY", "NEUROLOGY",
    "ORTHOPEDIC SURGERY", "OPHTHALMOLOGY", "PSYCHIATRY",
    "RHEUMATOLOGY", "UROLOGY", "PULMONARY DISEASE",
    "NURSE PRACTITIONER", "PHYSICIAN ASSISTANT",
}


def load_cms_outpatient(n_sample: int | None = None, random_state: int = 0) -> pd.DataFrame:
    """Stream the 2.86M-row CMS file and keep only outpatient-relevant specialties."""
    usecols = ["NPI", "gndr", "pri_spec", "org_pac_id", "num_org_mem",
               "City/Town", "State", "ZIP Code"]
    chunks = []
    for chunk in pd.read_csv(CMS_CSV, usecols=usecols, chunksize=200_000,
                             dtype=str, low_memory=False):
        chunk = chunk[chunk["pri_spec"].isin(CMS_SPECS_OUTPATIENT)]
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset=["NPI"]).reset_index(drop=True)
    if n_sample and len(df) > n_sample:
        df = df.sample(n=n_sample, random_state=random_state).reset_index(drop=True)
    return df


def ensure_dirs():
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)

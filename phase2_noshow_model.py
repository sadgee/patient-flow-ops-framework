"""Phase 2: Fit a no-show probability model on Kaggle data.

Outputs:
- outputs/noshow_model.joblib (trained pipeline)
- outputs/noshow_metrics.json (AUC, accuracy, etc.)
- figures/noshow_drivers.png (feature importance / coefficients)
- figures/noshow_calibration.png (predicted vs observed by decile)
"""
from __future__ import annotations
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, OneHotEncoder

import data_io as io


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["PatientId", "ScheduledDay"]).reset_index(drop=True)
    df["AppointmentDow"] = df["AppointmentDay"].dt.dayofweek
    df["ScheduledDow"] = df["ScheduledDay"].dt.dayofweek
    df["AgeGroup"] = pd.cut(df["Age"], bins=[-1, 5, 17, 39, 64, 200],
                             labels=["0-5", "6-17", "18-39", "40-64", "65+"])
    df["AnyChronic"] = ((df["Hypertension"] + df["Diabetes"] +
                          df["Alcoholism"] + (df["Handicap"] > 0)) > 0).astype(int)
    df["LeadBucket"] = pd.cut(df["LeadDays"], bins=[-1, 0, 2, 7, 30, 1000],
                               labels=["same_day", "1-2d", "3-7d", "8-30d", "31d+"])
    # Prior no-show rate per patient (rolling, leak-free: shifted by 1)
    df["prior_appts"] = df.groupby("PatientId").cumcount()
    df["prior_noshows"] = df.groupby("PatientId")["NoShow"].cumsum() - df["NoShow"]
    df["prior_noshow_rate"] = np.where(df["prior_appts"] > 0,
                                        df["prior_noshows"] / df["prior_appts"], 0.0)
    return df


FEATURES_NUM = ["Age", "LeadDays", "SMS_received", "Scholarship",
                "Hypertension", "Diabetes", "Alcoholism", "Handicap",
                "AnyChronic", "prior_appts", "prior_noshow_rate"]
FEATURES_CAT = ["Gender", "AgeGroup", "LeadBucket", "AppointmentDow"]


def build_pipeline(model: str = "gbm") -> Pipeline:
    pre = ColumnTransformer([
        ("num", StandardScaler(), FEATURES_NUM),
        ("cat", OneHotEncoder(drop="first", handle_unknown="ignore"), FEATURES_CAT),
    ])
    if model == "gbm":
        clf = GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                          learning_rate=0.05, random_state=0)
    else:
        clf = LogisticRegression(max_iter=1000, random_state=0)
    return Pipeline([("pre", pre), ("clf", clf)])


def plot_drivers(pipe: Pipeline, out_path):
    """Plot feature importances aggregated by base feature (one-hot bins summed)."""
    pre = pipe.named_steps["pre"]
    clf = pipe.named_steps["clf"]
    feat_names = pre.get_feature_names_out()
    imp = clf.feature_importances_

    # Aggregate one-hot bins back to their base categorical (LeadBucket_*, AgeGroup_*, etc.)
    # and the lead-time numeric into a single "Lead time" group.
    base_imp = {}
    for n, i in zip(feat_names, imp):
        clean = n.replace("num__", "").replace("cat__", "")
        # Strip the one-hot suffix (everything after the last underscore for cat features)
        if "_" in clean and clean.split("_")[0] in {"Gender", "AgeGroup",
                                                       "LeadBucket", "AppointmentDow"}:
            base = clean.split("_")[0]
            # Merge LeadBucket into LeadDays
            if base == "LeadBucket":
                base = "LeadDays"
        else:
            base = clean
        base_imp[base] = base_imp.get(base, 0.0) + i

    items = sorted(base_imp.items(), key=lambda kv: kv[1])
    labels = [k for k, _ in items]
    vals = [v for _, v in items]
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.barh(range(len(labels)), vals, color="steelblue")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_xlabel("Feature importance (one-hot bins aggregated)")
    ax.set_title("No-show drivers (gradient-boosted classifier)")
    for b, v in zip(bars, vals):
        ax.text(v + 0.005, b.get_y() + b.get_height()/2, f"{v:.2f}",
                va="center", fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_calibration(y_true, y_prob, out_path):
    deciles = pd.qcut(y_prob, 10, duplicates="drop", labels=False)
    df = pd.DataFrame({"y": y_true, "p": y_prob, "decile": deciles})
    summary = df.groupby("decile").agg(
        predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size"))
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, summary["predicted"].max()*1.05], [0, summary["predicted"].max()*1.05],
            "k--", alpha=0.5, label="perfect calibration")
    ax.scatter(summary["predicted"], summary["observed"], s=80, color="darkorange",
               edgecolor="black", zorder=5)
    ax.set_xlabel("Predicted no-show probability (decile mean)")
    ax.set_ylabel("Observed no-show rate")
    ax.set_title("Model calibration (deciles of predicted risk)")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def plot_lead_time_effect(df: pd.DataFrame, out_path):
    rates = df.groupby("LeadBucket", observed=True)["NoShow"].agg(["mean", "count"])
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(rates.index.astype(str), rates["mean"], color="steelblue")
    ax.set_ylabel("No-show rate")
    ax.set_xlabel("Days from scheduling to appointment")
    ax.set_title("No-show rate climbs with lead time (Kaggle, n=110,526)")
    for b, n in zip(bars, rates["count"]):
        ax.text(b.get_x() + b.get_width()/2, b.get_height()+0.005,
                f"n={n:,}", ha="center", fontsize=8)
    ax.set_ylim(0, rates["mean"].max() * 1.2)
    ax.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def main():
    io.ensure_dirs()
    df = pd.read_parquet(io.OUT / "kaggle_clean.parquet")
    df = engineer_features(df)

    X = df[FEATURES_NUM + FEATURES_CAT]
    y = df["NoShow"]
    # Patient-level split: same PatientId never appears in both train and test.
    # This avoids leakage from prior_noshow_rate / prior_appts and from any
    # patient-specific behavioral signal the GBM might pick up.
    gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=0)
    train_idx, test_idx = next(gss.split(X, y, groups=df["PatientId"]))
    X_tr, X_te = X.iloc[train_idx], X.iloc[test_idx]
    y_tr, y_te = y.iloc[train_idx], y.iloc[test_idx]

    n_train_patients = df.iloc[train_idx]["PatientId"].nunique()
    n_test_patients = df.iloc[test_idx]["PatientId"].nunique()
    overlap = set(df.iloc[train_idx]["PatientId"]) & set(df.iloc[test_idx]["PatientId"])
    assert len(overlap) == 0, f"Patient leakage: {len(overlap)} overlapping patients!"
    print(f"Training on {len(X_tr):,} appointments ({n_train_patients:,} patients);")
    print(f"Testing on {len(X_te):,} appointments ({n_test_patients:,} patients).")
    print(f"Patient overlap between train/test: {len(overlap)} (expected 0).")
    pipe = build_pipeline("gbm")
    pipe.fit(X_tr, y_tr)
    p_te = pipe.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, p_te)
    brier = brier_score_loss(y_te, p_te)
    base_rate = y.mean()
    print(f"  Test AUC: {auc:.3f} | Brier: {brier:.4f} | Base rate: {base_rate:.3f}")

    # Compare to logistic baseline
    pipe_lr = build_pipeline("logit").fit(X_tr, y_tr)
    p_te_lr = pipe_lr.predict_proba(X_te)[:, 1]
    auc_lr = roc_auc_score(y_te, p_te_lr)
    print(f"  Logit baseline AUC: {auc_lr:.3f}")

    metrics = {
        "n_train": int(len(X_tr)), "n_test": int(len(X_te)),
        "auc_gbm": float(auc), "auc_logit": float(auc_lr),
        "brier_gbm": float(brier), "base_rate": float(base_rate),
        "lead_time_noshow_rates": {
            str(k): float(v) for k, v in df.groupby("LeadBucket", observed=True)["NoShow"].mean().items()
        },
        "sms_effect": {
            "no_sms_noshow": float(df.loc[df["SMS_received"] == 0, "NoShow"].mean()),
            "sms_noshow": float(df.loc[df["SMS_received"] == 1, "NoShow"].mean()),
        },
    }
    with open(io.OUT / "noshow_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    joblib.dump(pipe, io.OUT / "noshow_model.joblib")

    plot_drivers(pipe, io.FIG / "noshow_drivers.png")
    plot_calibration(y_te.values, p_te, io.FIG / "noshow_calibration.png")
    plot_lead_time_effect(df, io.FIG / "noshow_lead_time.png")

    print(f"\nPhase 2 complete. Metrics: {metrics}")


if __name__ == "__main__":
    main()

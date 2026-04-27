"""Top-level orchestrator: runs all phases end-to-end.

Usage (from project root):
    .venv/bin/python analysis/main.py
"""
import importlib
import sys
from pathlib import Path

ANALYSIS = Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))

PHASES = [
    "phase1_data_prep",
    "phase2_noshow_model",
    "phase3_service_times",
    "phase4_synthetic_clinic",
    "phase5_simulator",
    "phase6_scenarios",
    "phase7_sensitivity",
]


def main():
    for name in PHASES:
        print(f"\n{'='*70}\n>>> {name}\n{'='*70}")
        mod = importlib.import_module(name)
        mod.main()
    print(f"\n{'='*70}\nAll phases complete. See analysis/figures/ and analysis/outputs/.\n{'='*70}")


if __name__ == "__main__":
    main()

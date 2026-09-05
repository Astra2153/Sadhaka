"""
Sadhaka — Adversarial Verification Harness
===========================================
    python3 src/run_verification.py                # standard run
    python3 src/run_verification.py --quick        # faster, coarser
    python3 src/run_verification.py --thorough     # slower, tighter bounds

Track 04's premise is that verification capacity, not generation speed, is the
bottleneck. This harness applies that premise to the engine itself.

It answers three questions about Sadhaka that Sadhaka's own report cannot:

  1. What is the smallest fault of each kind it can actually detect?
  2. When it says "85% confident", is it right 85% of the time?
  3. Where does it break, specifically?

The output is deliberately unflattering where the engine deserves it. A
verification report that only ever confirms the tool works is marketing, not
verification.
"""

import os
import sys
import csv
import json
import time
import argparse
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import generate_data
import adversarial as adv
from verification import (sweep_detection_limit, collect_calibration_samples,
                          analyse_calibration, PAISE_LADDER, DAY_LADDER)
from counterfactual import explain_all


def load_dataset(directory):
    def rd(name):
        with open(os.path.join(directory, name), newline="") as f:
            return list(csv.DictReader(f))
    with open(os.path.join(directory, "ground_truth.json")) as f:
        truth = json.load(f)
    return {
        "orders": rd("orders.csv"),
        "recon": rd("settlement_recon.csv"),
        "settlements": rd("settlements.csv"),
        "bank": rd("bank_statement.csv"),
        "invoices": rd("razorpay_gst_invoice.csv"),
        "truth": truth,
    }


def make_dataset(seed=42, num_orders=68):
    """Generate a dataset into a temp dir and load it into memory."""
    d = tempfile.mkdtemp(prefix="sadhaka_v")
    try:
        generate_data.generate(seed=seed, num_orders=num_orders, out_dir=d)
        return load_dataset(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def measure_baseline(base_data):
    """What the engine flags on a CLEAN dataset.

    Every subsequent detection is measured as flags ABOVE this. Without it, a
    fault injected onto an entity the engine already flags would be scored as
    a detection it never made.
    """
    result = adv.run_engine(base_data)
    ids = adv.flagged_ids(result)
    return ids, {
        "exceptions_on_clean_data": len(result["exceptions"]),
        "entities_flagged": len(ids),
        "matches": len(result["batch_matches"]),
        "note": ("These are the engine's normal findings on untampered data — "
                 "on-hold reserves, unsettled orders, refunds in later batches. "
                 "Every detection below is counted only if it flags an entity "
                 "NOT already in this set."),
    }


def run(profile="standard", seed=42, quiet=False):
    t0 = time.time()

    if profile == "quick":
        trials_per_level, cal_trials, ladder = 6, 20, [1, 10, 50, 200, 800, 3200, 20000]
    elif profile == "thorough":
        trials_per_level, cal_trials, ladder = 25, 120, PAISE_LADDER
    else:
        trials_per_level, cal_trials, ladder = 12, 55, PAISE_LADDER

    base_data = make_dataset(seed=seed)
    baseline_ids, baseline = measure_baseline(base_data)

    # ---- 1. detection limits ------------------------------------------
    sweeps = []
    for fault_type in adv.FAULTS:
        sw = sweep_detection_limit(
            base_data, fault_type, baseline_ids,
            trials_per_level=trials_per_level, seed=seed,
            ladder=(ladder if adv.FAULTS[fault_type]["unit"] == "paise" else None))
        sweeps.append(sw)
        if not quiet:
            print(f"  swept {fault_type:<18} "
                  f"{sum(l['trials'] for l in sw['levels']):>4} trials")

    total_trials = sum(sum(l["trials"] for l in s["levels"]) for s in sweeps)

    # ---- 2. calibration ------------------------------------------------
    if not quiet:
        print(f"  collecting calibration samples over {cal_trials} datasets...")
    samples = collect_calibration_samples(make_dataset, trials=cal_trials, seed=seed)
    calibration = analyse_calibration(samples)

    # ---- 3. counterfactuals on the live dataset ------------------------
    live = adv.run_engine(base_data)
    counterfactuals = explain_all(
        live["exceptions"], base_data["bank"], base_data["settlements"],
        base_data["recon"], base_data["orders"])

    # ---- 4. blind spots vs underpowered ---------------------------------
    # These are different findings and must not be conflated. A blind spot is
    # a detector that fails. Underpowered means the measurement was too small
    # to conclude anything — which is a fact about the harness, not the engine.
    blind = [s for s in sweeps if s.get("verdict") == "blind_spot"]
    underpowered = [s for s in sweeps if s.get("verdict") == "underpowered"]

    elapsed = round(time.time() - t0, 1)

    report = {
        "profile": profile,
        "seed": seed,
        "elapsed_seconds": elapsed,
        "total_attack_trials": total_trials,
        "calibration_samples": calibration.get("samples", 0),
        "baseline": baseline,
        "detection_limits": sweeps,
        "calibration": calibration,
        "counterfactuals": counterfactuals,
        "blind_spots": [{"fault_type": s["fault_type"], "label": s["label"],
                         "statement": s["statement"]} for s in blind],
        "underpowered": [{"fault_type": s["fault_type"], "label": s["label"],
                          "statement": s["statement"],
                          "aggregate_rate": s.get("aggregate_rate"),
                          "aggregate_trials": s.get("aggregate_trials"),
                          "aggregate_ci": s.get("aggregate_ci")}
                         for s in underpowered],
        "headline": _headline(sweeps, calibration, total_trials),
    }

    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(cfg.OUTPUT_DIR, "verification_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)

    if not quiet:
        print_report(report)
    return report


def _headline(sweeps, calibration, trials):
    money = [s for s in sweeps if s["unit"] == "paise" and s["lod95"]]
    floor = (cfg.rupees(int(min(money, key=lambda s: s["lod95"])["lod95"]))
             if money else "not established at this trial count")
    blind = sum(1 for s in sweeps if s.get("verdict") == "blind_spot")
    return (f"Attacked with {trials} injected faults across {len(sweeps)} fault "
            f"types. Smallest reliably detected money fault: {floor}. "
            f"Blind spots found: {blind}. Confidence calibration error: "
            f"{calibration.get('ece', 'n/a')}.")


def print_report(r):
    W = 74
    print("\n" + "=" * W)
    print("  SADHAKA — ADVERSARIAL VERIFICATION REPORT".ljust(W))
    print(f"  profile: {r['profile']}   seed: {r['seed']}   "
          f"{r['elapsed_seconds']}s".ljust(W))
    print("=" * W)

    print("\nWHY THIS EXISTS")
    print("  Hand-planted faults prove the engine catches hand-planted faults.")
    print("  This attacks it programmatically instead, and reports where it fails.")

    b = r["baseline"]
    print("\nBASELINE ON CLEAN DATA")
    print(f"  {b['exceptions_on_clean_data']} exceptions across "
          f"{b['entities_flagged']} entities, {b['matches']} matches")
    print(f"  {b['note']}")

    print(f"\nDETECTION LIMITS   ({r['total_attack_trials']} injected faults)")
    print(f"  {'FAULT':<19}{'DETECTED':>10}{'95% CI':>16}{'LOD95':>12}  VERDICT")
    print("  " + "-" * (W - 4))
    labels = {"floor_established": "floor established",
              "blind_spot": "BLIND SPOT",
              "underpowered": "underpowered",
              "reliable": "reliable",
              "unreliable": "UNRELIABLE"}
    for s in r["detection_limits"]:
        l95 = s["lod95_display"] or "—"
        rate = s.get("aggregate_rate", 0)
        ci = s.get("aggregate_ci", [0, 1])
        ci_s = f"{ci[0]*100:.0f}-{ci[1]*100:.0f}%"
        print(f"  {s['fault_type']:<19}{rate*100:>9.0f}%{ci_s:>16}{l95:>12}"
              f"  {labels.get(s.get('verdict'), s.get('verdict',''))}")
    print("\n  Rates are aggregated across all magnitudes above the noise floor,")
    print("  which is far better powered than any single level. The interval is")
    print("  a Wilson score interval; a point estimate from few trials is not evidence.")

    print("\n  What each floor means in practice:")
    for s in r["detection_limits"]:
        print(f"    - {s['statement']}")

    c = r["calibration"]
    print(f"\nCONFIDENCE CALIBRATION   ({c.get('samples',0)} match decisions "
          f"scored against ground truth)")
    if c.get("bins"):
        print(f"  {'CLAIMED':>10}{'OBSERVED':>11}{'GAP':>9}{'N':>7}   DIRECTION")
        print("  " + "-" * (W - 4))
        for bn in c["bins"]:
            print(f"  {bn['claimed_confidence']:>9.1%}{bn['observed_accuracy']:>11.1%}"
                  f"{bn['gap']:>+9.1%}{bn['count']:>7}   {bn['direction']}")
        print(f"\n  Expected calibration error : {c['ece']:.4f}")
        print(f"  Brier score                : {c['brier_score']:.4f}  (lower is better)")
        print(f"  Overall accuracy           : {c['overall_accuracy']:.1%}")
        print(f"  Mean stated confidence     : {c['mean_confidence']:.1%}")
    print(f"\n  {c.get('verdict','')}")

    if r["blind_spots"]:
        print("\nBLIND SPOTS  (reported, not hidden)")
        for bs in r["blind_spots"]:
            print(f"  - {bs['statement']}")
    else:
        print("\nBLIND SPOTS")
        print("  None. No fault type failed to be detected at the magnitudes tested.")

    if r.get("underpowered"):
        print("\nUNDERPOWERED MEASUREMENTS  (a limit of this harness, not of the engine)")
        for u in r["underpowered"]:
            print(f"  - {u['statement']}")

    cfs = [c for c in r["counterfactuals"] if c["counterfactual"].get("actionable")]
    print(f"\nCOUNTERFACTUAL EXPLANATIONS  "
          f"({len(cfs)} of {len(r['counterfactuals'])} exceptions have an "
          f"actionable minimal fix)")
    for c in cfs[:4]:
        print(f"\n  {c['variance_code']} on {c['subject_id']}")
        print(f"    {c['counterfactual']['narrative']}")

    print("\n" + "=" * W)
    print(f"  {r['headline']}")
    print(f"  Full report: {os.path.join(cfg.OUTPUT_DIR, 'verification_report.json')}")
    print("=" * W + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--thorough", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    profile = "quick" if args.quick else ("thorough" if args.thorough else "standard")
    run(profile=profile, seed=args.seed)

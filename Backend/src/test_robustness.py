"""
Sadhaka — Robustness Harness
=============================
A perfect score on ONE synthetic dataset proves almost nothing. The generator
and the engine were written by the same author, so the engine could easily be
overfitted to the exact shape of seed 42.

This runs the whole pipeline across many independent seeds and reports the
distribution of results, plus any seed where the engine regressed. If recall
is 100% on seed 42 and 70% everywhere else, that shows up here instead of
being quietly hidden behind one good demo.

    python src/test_robustness.py --seeds 15
"""

import os
import sys
import json
import shutil
import tempfile
import argparse
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import generate_data
from run_pipeline import run


def run_one(seed, num_orders):
    tmp_data = tempfile.mkdtemp(prefix=f"sadhaka_d{seed}_")
    tmp_out = tempfile.mkdtemp(prefix=f"sadhaka_o{seed}_")
    try:
        generate_data.generate(seed=seed, num_orders=num_orders, out_dir=tmp_data)
        # keep each seed's audit trail isolated
        original_db = cfg.AUDIT_DB
        cfg.AUDIT_DB = os.path.join(tmp_out, "audit.db")
        try:
            res = run(data_dir=tmp_data, output_dir=tmp_out, quiet=True)
        finally:
            cfg.AUDIT_DB = original_db

        sc = res["scorecard"]
        m = res["metrics"]
        return {
            "seed": seed,
            "recall_pct": sc["recall_pct"],
            "code_accuracy_pct": sc["code_accuracy_pct"],
            "trap_pass_pct": sc["trap_pass_pct"],
            "planted_faults": sc["planted_faults"],
            "planted_traps": sc["planted_traps"],
            "batch_match_pct": m["match_rates"]["batch_match_rate_pct"],
            "order_match_pct": m["match_rates"]["order_match_rate_pct"],
            "value_match_pct": m["match_rates"]["value_match_rate_pct"],
            "actionable": m["exceptions"]["actionable"],
            "benign": m["exceptions"]["benign"],
            "misses": [c["id"] for c in sc["cases"] if not c["detected"]],
            "trap_fails": [t["id"] for t in sc["traps"] if not t["passed"]],
            "records": m["throughput"]["total_records_processed"],
        }
    finally:
        shutil.rmtree(tmp_data, ignore_errors=True)
        shutil.rmtree(tmp_out, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=15)
    ap.add_argument("--orders", type=int, default=68)
    ap.add_argument("--start", type=int, default=1)
    args = ap.parse_args()

    seeds = list(range(args.start, args.start + args.seeds))
    results = []

    print(f"Running the pipeline across {len(seeds)} independent datasets...\n")
    for s in seeds:
        try:
            r = run_one(s, args.orders)
            results.append(r)
            flag = "" if not (r["misses"] or r["trap_fails"]) else \
                   f"   <-- misses={r['misses']} traps={r['trap_fails']}"
            print(f"  seed {s:>3}: recall {r['recall_pct']:>5.1f}%  "
                  f"traps {r['trap_pass_pct']:>5.1f}%  "
                  f"batch {r['batch_match_pct']:>6.2f}%  "
                  f"order {r['order_match_pct']:>6.2f}%  "
                  f"({r['records']} records){flag}")
        except Exception as ex:
            print(f"  seed {s:>3}: CRASHED — {type(ex).__name__}: {ex}")
            results.append({"seed": s, "crashed": str(ex)})

    ok = [r for r in results if "crashed" not in r]
    if not ok:
        print("\nAll seeds crashed.")
        return

    def stats(key):
        vals = [r[key] for r in ok]
        return (min(vals), statistics.mean(vals), max(vals))

    print("\n" + "=" * 66)
    print("  ROBUSTNESS SUMMARY")
    print("=" * 66)
    print(f"  datasets run:        {len(ok)} (crashed: {len(results)-len(ok)})")
    print(f"  total records:       {sum(r['records'] for r in ok)}")
    print()
    print(f"  {'METRIC':<26}{'MIN':>10}{'MEAN':>10}{'MAX':>10}")
    for label, key in [
        ("fault recall %", "recall_pct"),
        ("code accuracy %", "code_accuracy_pct"),
        ("trap avoidance %", "trap_pass_pct"),
        ("batch match %", "batch_match_pct"),
        ("order match %", "order_match_pct"),
        ("value match %", "value_match_pct"),
    ]:
        lo, mu, hi = stats(key)
        print(f"  {label:<26}{lo:>10.2f}{mu:>10.2f}{hi:>10.2f}")

    all_misses = [m for r in ok for m in r["misses"]]
    all_trap_fails = [t for r in ok for t in r["trap_fails"]]
    print()
    if all_misses:
        from collections import Counter
        print("  faults missed (by case id):")
        for cid, n in Counter(all_misses).most_common():
            print(f"    {cid}: missed in {n}/{len(ok)} datasets")
    else:
        print("  no planted fault was missed in any dataset")
    if all_trap_fails:
        from collections import Counter
        print("  traps failed (by case id):")
        for cid, n in Counter(all_trap_fails).most_common():
            print(f"    {cid}: failed in {n}/{len(ok)} datasets")
    else:
        print("  no trap was failed in any dataset")

    out = os.path.join(cfg.OUTPUT_DIR, "robustness_results.json")
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  full results: {out}")
    print("=" * 66)


if __name__ == "__main__":
    main()

"""
Sadhaka — Snapshot Exporter
============================
Writes the two JSON files the frontend bundles, so the deployed site can render
real numbers with no backend awake.

    python3 scripts/export_snapshot.py --out ../frontend/src/data

Run this after any pipeline or verification run whose results should appear on
the deployed site. It calls the same API endpoints the live frontend calls, so
the snapshot cannot drift into being a different shape from live data — which
is the usual way fixtures rot.
"""

import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import config as cfg  # noqa: E402


def build(out_dir):
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)

    def get(path):
        r = client.get(path)
        if r.status_code != 200:
            raise SystemExit(
                f"{path} returned {r.status_code}. Run the pipeline first:\n"
                f"  python3 src/run_pipeline.py"
            )
        return r.json()

    journal = get("/journal")
    bundle = {
        "summary": get("/summary"),
        "exceptions": get("/exceptions?limit=400"),
        "gst": get("/gst"),
        "scorecard": get("/scorecard"),
        "config": get("/config"),
        "forecast": get("/forecast"),
        "journal": journal["entries"],
        "journalSummary": journal["summary"],
        "audit": get("/audit?limit=600"),
        "marketplace": get("/marketplace"),
    }

    os.makedirs(out_dir, exist_ok=True)
    snap_path = os.path.join(out_dir, "snapshot.json")
    with open(snap_path, "w") as f:
        json.dump(bundle, f, indent=1, default=str)

    verif_src = os.path.join(cfg.OUTPUT_DIR, "verification_report.json")
    verif_path = os.path.join(out_dir, "verification.json")
    if os.path.exists(verif_src):
        with open(verif_src) as f:
            verification = json.load(f)
        with open(verif_path, "w") as f:
            json.dump(verification, f, indent=1, default=str)
        v_note = (f"{verification['profile']} profile, "
                  f"{verification['total_attack_trials']} injected faults")
    else:
        v_note = ("NOT WRITTEN — no verification report found. Run: "
                  "python3 src/run_verification.py --thorough")

    m = bundle["summary"]["metrics"]
    print("Snapshot exported")
    print("=" * 58)
    print(f"  {snap_path}")
    print(f"    run {bundle['summary']['run_id']}")
    print(f"    {m['throughput']['total_records_processed']} records, "
          f"{m['exceptions']['total']} exceptions "
          f"({m['exceptions']['actionable']} actionable)")
    print(f"    value match {m['match_rates']['value_match_rate_pct']}%")
    print(f"  {verif_path}")
    print(f"    {v_note}")
    print()
    print("  Rebuild the frontend to pick these up: npm run build")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../frontend/src/data",
                    help="directory to write snapshot.json and verification.json")
    args = ap.parse_args()
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    build(os.path.normpath(out))

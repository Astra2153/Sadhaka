#!/usr/bin/env bash
# Sadhaka — one command to reproduce everything.
set -e
cd "$(dirname "$0")"

echo "==> Installing dependencies"
pip install -q -r requirements.txt 2>/dev/null || pip install -q --break-system-packages -r requirements.txt

echo "==> Generating synthetic dataset"
python3 src/generate_data.py

echo "==> Running reconciliation pipeline (5 stages)"
python3 src/run_pipeline.py

echo "==> Running test suite"
python3 src/test_suite.py

echo "==> Running adversarial verification (quick profile)"
python3 src/run_verification.py --quick

echo "==> Generating audit-ready PDF report"
python3 src/generate_pdf_report.py

echo
echo "Done."
echo "  Audit trail         : output/audit_trail.db"
echo "  JSON report         : output/reconciliation_report.json"
echo "  Journal entries     : output/journal_entries.csv"
echo "  Verification report : output/verification_report.json"
echo "  Audit-ready PDF     : output/Sadhaka_Reconciliation_Report.pdf"
echo
echo "Next:"
echo "  Establish detection floors     :  python3 src/run_verification.py --thorough"
echo "  Robustness across 15 datasets  :  python3 src/test_robustness.py --seeds 15"
echo "  Marketplace (194-O / GST TCS)  :  python3 src/marketplace_scenario.py"
echo "  API + Swagger docs             :  uvicorn api.main:app --port 8000   (then /docs)"
echo "  Dashboards                     :  python3 -m http.server 5173 --directory frontend"
echo "                                    /index.html  and  /verify.html"

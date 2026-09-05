"""
Sadhaka — API
=============
FastAPI service over the reconciliation engine.

Everything served here reads FROM the audit trail. No endpoint recomputes a
number for display. If the dashboard and the audit trail could disagree, the
audit trail would be decorative — so they cannot.

Run:
    uvicorn api.main:app --reload --port 8000
Swagger UI:
    http://127.0.0.1:8000/docs
"""

import os
import sys
import json
import sqlite3
import logging

# Without this, Python's logging module defaults to WARNING-and-above with
# no handler attached in some environments, so logger.exception() calls
# (e.g. in qa_agent.py when a Gemini call fails) can silently produce no
# console output at all -- which is exactly the bug that made a real API
# key/quota error look like nothing happened.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import config as cfg                      # noqa: E402
from run_pipeline import run as run_pipeline  # noqa: E402
import security as sec                    # noqa: E402
import access as acc                      # noqa: E402



TAGS_METADATA = [
    {"name": "system", "description": "Liveness and environment checks. Call these first if anything else 404s."},
    {"name": "engine", "description": "Execute the reconciliation pipeline and list past runs. A run must exist before any `reporting` endpoint has data to serve."},
    {"name": "reporting", "description": "Read results from the most recent (or a specified) run's audit trail. Every number here traces to a row in `output/audit_trail.db`."},
    {"name": "reference", "description": "Static configuration and taxonomy — the rates, tolerances and variance codes the engine ran with. Not run-specific."},
    {"name": "scenarios", "description": "Alternate business scenarios not exercised by the default pipeline (marketplace / Route split-payouts, where Section 194-O and TCS apply)."},
    {"name": "verification", "description": "The adversarial harness: detection limits, confidence calibration, blind spots, counterfactual explanations. Populate with `python src/run_verification.py --thorough` before calling these."},
]

app = FastAPI(
    title="Sadhaka — Settlement Reconciliation API",
    description=(
        "Reconciles Razorpay settlement data across five engine stages — bank "
        "credit to settlement batch, settlement batch to individual orders, "
        "settlement GST to the monthly tax invoice, forward cash position, and "
        "double-entry journal generation — plus a marketplace scenario for "
        "Section 194-O TDS / Section 52 GST TCS, and an adversarial harness "
        "that attacks the engine's own matching logic to measure detection "
        "limits and confidence calibration.\n\n"
        "**Every figure returned by this API is read from the audit trail "
        "written by the engine. Nothing is recomputed at display time** — if "
        "an endpoint and the SQLite audit trail could ever disagree, the "
        "audit trail would be decorative, so the two are structurally the "
        "same source.\n\n"
        "**Typical order of calls** for a fresh environment: `POST /run` to "
        "execute the pipeline (or run `python src/run_pipeline.py` directly), "
        "then `GET /summary` for headline metrics, `GET /exceptions` for what "
        "needs attention, `GET /trace/{entity_id}` to ask why a specific "
        "order/payment/settlement did or didn't reconcile, and "
        "`GET /verification` for the adversarial self-test results (run "
        "`python src/run_verification.py --thorough` first to populate it).\n\n"
        "A ready-to-import request collection is at `docs/sadhaka.postman_collection.json` "
        "in the repo, and `docs/requests.http` works directly in VS Code's REST "
        "Client extension for manual, no-UI testing of every endpoint."
    ),
    version="0.4.0",
    contact={"name": "Ashmit Sanjay Katale"},
    openapi_tags=TAGS_METADATA,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # scoped to the deployed frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    engine_version: str
    audit_db_present: bool
    latest_run_id: Optional[str] = None
    server_time: str


class RunSummary(BaseModel):
    run_id: str
    started_at: str
    finished_at: Optional[str]
    engine_version: Optional[str]
    notes: Optional[str]
    decision_count: int


class Decision(BaseModel):
    decision_id: int
    stage: str
    subject_type: str
    subject_id: str
    counterpart_type: Optional[str]
    counterpart_id: Optional[str]
    outcome: str
    variance_code: Optional[str]
    confidence: float
    rule_fired: str
    amount_subject: Optional[int]
    amount_counterpart: Optional[int]
    variance_paise: Optional[int]
    reason: str
    evidence: dict = Field(default_factory=dict)
    amount_subject_display: Optional[str] = None
    variance_display: Optional[str] = None


class TraceResponse(BaseModel):
    entity_id: str
    run_id: str
    found: bool
    decision_count: int
    narrative: str
    decisions: List[Decision]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn():
    if not os.path.exists(cfg.AUDIT_DB):
        raise HTTPException(
            status_code=503,
            detail=("No audit trail found. Run the pipeline first: "
                    "python src/run_pipeline.py  (or POST /run)"),
        )
    c = sqlite3.connect(cfg.AUDIT_DB)
    c.row_factory = sqlite3.Row
    return c


def _latest_run_id(conn=None):
    own = conn is None
    conn = conn or _conn()
    try:
        row = conn.execute(
            "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return row["run_id"] if row else None
    finally:
        if own:
            conn.close()


def _decorate(row: dict) -> dict:
    d = dict(row)
    try:
        d["evidence"] = json.loads(d.get("evidence") or "{}")
    except Exception:
        d["evidence"] = {}
    if d.get("amount_subject") is not None:
        d["amount_subject_display"] = cfg.rupees(d["amount_subject"])
    if d.get("variance_paise"):
        d["variance_display"] = cfg.rupees(d["variance_paise"])
    return d


def _latest_run_with(metric_key, conn):
    """Latest run that actually stored this metric.

    Needed because scenario runs (e.g. the marketplace scenario) also write to
    the audit trail, so 'most recent run' is not always 'most recent run that
    produced a forecast'. Resolving by metric avoids serving a 404 for data
    that exists on the previous run.
    """
    row = conn.execute("""
        SELECT r.run_id FROM runs r
        JOIN run_metrics m ON m.run_id = r.run_id AND m.metric_key = ?
        ORDER BY r.started_at DESC LIMIT 1
    """, (metric_key,)).fetchone()
    return row["run_id"] if row else None


def _metrics(run_id, conn):
    rows = conn.execute(
        "SELECT metric_key, metric_value FROM run_metrics WHERE run_id=?",
        (run_id,)).fetchall()
    return {r["metric_key"]: json.loads(r["metric_value"]) for r in rows}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    """Liveness check. Also reports whether any reconciliation has been run."""
    present = os.path.exists(cfg.AUDIT_DB)
    latest = None
    if present:
        c = sqlite3.connect(cfg.AUDIT_DB)
        c.row_factory = sqlite3.Row
        try:
            r = c.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
            latest = r["run_id"] if r else None
        except sqlite3.OperationalError:
            latest = None
        finally:
            c.close()
    return HealthResponse(
        status="ok", engine_version="0.3.0", audit_db_present=present,
        latest_run_id=latest,
        server_time=datetime.now().isoformat(timespec="seconds"),
    )


@app.post("/run", tags=["engine"],
         dependencies=[Depends(acc.require_role(acc.Role.OPERATOR))])
def trigger_run():
    """Execute the full three-stage reconciliation and return the summary.

    Idempotent in the sense that it creates a NEW run rather than mutating a
    previous one; earlier runs stay queryable for comparison.

    Requires operator role: this consumes compute on every call, so on a
    public deployment it is gated behind X-Sadhaka-Role: operator and a
    matching X-Sadhaka-Key header, verified server-side against
    SADHAKA_OPERATOR_KEY. Unset that environment variable and this endpoint
    is unreachable by anyone, which is the fail-closed default.
    """
    try:
        result = run_pipeline(quiet=True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "run_id": result["run_id"],
        "metrics": result["metrics"],
        "scorecard": result["scorecard"],
    }


@app.get("/runs", response_model=List[RunSummary], tags=["engine"])
def list_runs():
    """All reconciliation runs, newest first."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT r.*, (SELECT COUNT(*) FROM decisions d WHERE d.run_id = r.run_id)
                   AS decision_count
            FROM runs r ORDER BY r.started_at DESC
        """).fetchall()
        return [RunSummary(**dict(r)) for r in rows]
    finally:
        conn.close()


@app.get("/summary", tags=["reporting"])
def summary(run_id: Optional[str] = None):
    """Headline metrics: throughput, match rates with their denominators,
    exception split, money, and the self-score against the answer key."""
    conn = _conn()
    try:
        rid = run_id or _latest_run_with("metrics", conn) or _latest_run_id(conn)
        if not rid:
            raise HTTPException(404, "No runs found. POST /run first.")
        m = _metrics(rid, conn)
        if not m:
            raise HTTPException(404, f"No metrics stored for run {rid}")
        return {"run_id": rid, **m}
    finally:
        conn.close()


@app.get("/exceptions", tags=["reporting"])
def exceptions(
    run_id: Optional[str] = None,
    code: Optional[str] = Query(None, description="Filter by variance code"),
    kind: Optional[str] = Query(None, pattern="^(benign|actionable)$"),
    limit: int = Query(200, le=1000),
):
    """The exception list, split into benign and actionable.

    Benign means the engine understands why the variance exists and no action
    is needed (timing lag, on-hold reserve, rounding). Actionable means a human
    should look. Reporting them together would be alarmism.
    """
    conn = _conn()
    try:
        rid = run_id or _latest_run_with("metrics", conn) or _latest_run_id(conn)
        q = ("SELECT * FROM decisions WHERE run_id=? AND outcome IN "
             "('EXCEPTION','UNMATCHED')")
        params = [rid]
        if code:
            q += " AND variance_code=?"
            params.append(code)
        q += f" ORDER BY ABS(COALESCE(variance_paise, amount_subject, 0)) DESC LIMIT {int(limit)}"
        rows = [_decorate(dict(r)) for r in conn.execute(q, params).fetchall()]

        for r in rows:
            vc = r.get("variance_code") or "UNEXPLAINED"
            r["is_benign"] = vc in cfg.BENIGN_CODES
            r["code_meaning"] = cfg.VARIANCE_CODES.get(vc, "Unclassified.")

        if kind == "benign":
            rows = [r for r in rows if r["is_benign"]]
        elif kind == "actionable":
            rows = [r for r in rows if not r["is_benign"]]

        return {
            "run_id": rid, "count": len(rows),
            "actionable": sum(1 for r in rows if not r["is_benign"]),
            "benign": sum(1 for r in rows if r["is_benign"]),
            "exceptions": rows,
        }
    finally:
        conn.close()


@app.get("/matches", tags=["reporting"])
def matches(run_id: Optional[str] = None, stage: Optional[str] = None,
            limit: int = Query(500, le=2000)):
    """Successful matches with the rule that produced them and the confidence."""
    conn = _conn()
    try:
        rid = run_id or _latest_run_id(conn)
        q = "SELECT * FROM decisions WHERE run_id=? AND outcome='MATCHED'"
        params = [rid]
        if stage:
            q += " AND stage=?"
            params.append(stage)
        q += f" ORDER BY decision_id LIMIT {int(limit)}"
        rows = [_decorate(dict(r)) for r in conn.execute(q, params).fetchall()]
        return {"run_id": rid, "count": len(rows), "matches": rows}
    finally:
        conn.close()


@app.get("/audit", tags=["reporting"],
        dependencies=[Depends(acc.require_role(acc.Role.VIEWER))])
def audit(run_id: Optional[str] = None, outcome: Optional[str] = None,
          stage: Optional[str] = None, variance_code: Optional[str] = None,
          search: Optional[str] = None, limit: int = Query(500, le=5000),
          offset: int = 0):
    """The full decision log — every match and every exception, filterable.

    This is the source of truth the rest of the API reads from. Kept at
    viewer tier deliberately: the whole design principle of this project is
    that reconciliation decisions must be inspectable, so hiding the audit
    trail behind a higher tier than the summaries it backs would undercut
    that. Present as the highest tier in the require_role() chain only for
    symmetry with the other reporting endpoints, at the lowest bar (viewer).
    """
    conn = _conn()
    try:
        rid = run_id or _latest_run_id(conn)
        q = "SELECT * FROM decisions WHERE run_id=?"
        params = [rid]
        for col, val in (("outcome", outcome), ("stage", stage),
                         ("variance_code", variance_code)):
            if val:
                q += f" AND {col}=?"
                params.append(val)
        if search:
            q += (" AND (subject_id LIKE ? OR counterpart_id LIKE ? "
                  "OR reason LIKE ? OR evidence LIKE ?)")
            params += [f"%{search}%"] * 4
        total = conn.execute(
            f"SELECT COUNT(*) c FROM ({q})", params).fetchone()["c"]
        q += f" ORDER BY decision_id LIMIT {int(limit)} OFFSET {int(offset)}"
        rows = [_decorate(dict(r)) for r in conn.execute(q, params).fetchall()]
        return {"run_id": rid, "total": total, "returned": len(rows),
                "offset": offset, "decisions": rows}
    finally:
        conn.close()


@app.get("/trace/{entity_id}", response_model=TraceResponse, tags=["reporting"])
def trace(entity_id: str, run_id: Optional[str] = None):
    """Everything the engine decided about one entity, with a plain-English
    narrative assembled from the recorded reasons.

    This answers 'why didn't order X match?' from the audit trail itself, so
    the explanation is the engine's actual reasoning rather than a plausible
    story generated after the fact.
    """
    conn = _conn()
    try:
        rid = run_id or _latest_run_id(conn)
        rows = conn.execute("""
            SELECT * FROM decisions
            WHERE run_id=? AND (subject_id=? OR counterpart_id=? OR evidence LIKE ?)
            ORDER BY decision_id
        """, (rid, entity_id, entity_id, f'%"{entity_id}"%')).fetchall()
        decs = [_decorate(dict(r)) for r in rows]

        if not decs:
            return TraceResponse(
                entity_id=entity_id, run_id=rid, found=False, decision_count=0,
                narrative=(f"No decision was recorded for '{entity_id}' in run {rid}. "
                           f"Either the identifier does not exist in this dataset, "
                           f"or it was filtered out before matching began."),
                decisions=[])

        parts = []
        for d in decs:
            stage_label = {"stage1_bank_batch": "Bank to settlement batch",
                           "stage2_order": "Settlement to order",
                           "stage3_gst": "GST and input tax credit"}.get(
                               d["stage"], d["stage"])
            verdict = {"MATCHED": "matched", "EXCEPTION": "raised an exception",
                       "UNMATCHED": "found no counterpart"}.get(
                           d["outcome"], d["outcome"].lower())
            parts.append(
                f"[{stage_label}] The engine {verdict} at "
                f"{d['confidence']:.0%} confidence via rule '{d['rule_fired']}'"
                + (f" ({d['variance_code']})" if d.get("variance_code") else "")
                + f". {d['reason']}")

        return TraceResponse(
            entity_id=entity_id, run_id=rid, found=True,
            decision_count=len(decs), narrative="\n\n".join(parts),
            decisions=decs)
    finally:
        conn.close()


@app.get("/gst", tags=["reporting"])
def gst(run_id: Optional[str] = None):
    """GST and input tax credit position: settlement tax vs the monthly
    invoice, ITC eligibility with any blockers named, and effective MDR/GST
    per payment instrument."""
    conn = _conn()
    try:
        rid = run_id or _latest_run_with("gst_report", conn) or _latest_run_id(conn)
        m = _metrics(rid, conn)
        report = m.get("gst_report")
        if not report:
            raise HTTPException(404, f"No GST report stored for run {rid}")

        display = dict(report)
        display["display"] = {
            "settled_fee_total": cfg.rupees(report["settled_fee_total"]),
            "settled_tax_total": cfg.rupees(report["settled_tax_total"]),
            "expected_tax_on_fees": cfg.rupees(report["expected_tax_on_fees"]),
            "gst_understated": cfg.rupees(report["gst_understated"]),
            "total_itc_claimable": cfg.rupees(report["total_itc_claimable"]),
            "total_itc_blocked": cfg.rupees(report["total_itc_blocked"]),
        }
        return {"run_id": rid, **display}
    finally:
        conn.close()


@app.get("/scorecard", tags=["reporting"])
def scorecard(run_id: Optional[str] = None):
    """Self-score against the generator's answer key.

    Faults and traps are scored separately: a fault is caught by raising the
    right exception, a trap is passed by NOT producing a wrong match.
    """
    conn = _conn()
    try:
        rid = run_id or _latest_run_with("scorecard", conn) or _latest_run_id(conn)
        m = _metrics(rid, conn)
        sc = m.get("scorecard")
        if not sc:
            raise HTTPException(404, f"No scorecard stored for run {rid}")
        return {"run_id": rid, **sc}
    finally:
        conn.close()


@app.get("/variance-codes", tags=["reference"])
def variance_codes():
    """The variance taxonomy, and which codes are benign vs actionable."""
    return {
        "codes": [
            {"code": c, "meaning": desc,
             "benign": c in cfg.BENIGN_CODES,
             "actionable": c in cfg.ACTIONABLE_CODES}
            for c, desc in cfg.VARIANCE_CODES.items()
        ],
        "note": ("Benign codes describe variances the engine understands and "
                 "which need no action. Actionable codes need a human. They are "
                 "reported separately so the exception count is honest."),
    }


@app.get("/config", tags=["reference"])
def configuration():
    """The rates and tolerances the engine ran with.

    Exposed because a match rate is not interpretable without knowing the
    tolerances that produced it.
    """
    return {
        "merchant": {
            "legal_name": cfg.DEFAULT_MERCHANT.legal_name,
            "gstin": cfg.DEFAULT_MERCHANT.gstin,
            "state_code": cfg.DEFAULT_MERCHANT.state_code,
            "settlement_model": cfg.DEFAULT_MERCHANT.settlement_model,
            "settlement_cycle": f"T+{cfg.DEFAULT_MERCHANT.settlement_cycle_days}",
        },
        "mdr_rates": cfg.MDR_RATES,
        "gst_on_mdr_pct": 18.0,
        "tolerances": {
            "batch_rounding": cfg.rupees(cfg.TOLERANCES.rounding_paise),
            "per_transaction_rounding": cfg.rupees(cfg.TOLERANCES.per_txn_rounding_paise),
            "date_window_days": cfg.TOLERANCES.date_window_days,
            "monthly_gst_invoice": cfg.rupees(cfg.TOLERANCES.gst_invoice_tolerance_paise),
        },
        "auto_accept_threshold": cfg.AUTO_ACCEPT_THRESHOLD,
        "statutory_notes": {
            "gst_on_mdr": "18% on the fee only, never on transaction value.",
            "upi_mdr": "Zero MDR by statute (Sec 10A PSS Act). Nil fee and nil GST are correct, not missing.",
            "tds_194o": "Not deducted by a payment aggregator; applies to e-commerce operators. Absent by design.",
            "gst_tcs_52": "Not applicable to a payment aggregator; applies to marketplace operators. Absent by design.",
            "settlement_timing": "Post-Sept-2025 RBI Payment Aggregator Directions, the settlement cycle is contractual rather than a fixed T+1 mandate, so it is configurable here.",
        },
    }


@app.get("/forecast", tags=["reporting"])
def forecast(run_id: Optional[str] = None):
    """Forward cash position: what lands, when, and with what confidence.

    The settlement lag is learned from the merchant's observed history rather
    than assumed from the contracted cycle, because the September 2025 RBI
    Payment Aggregator Directions made that cycle contractual rather than
    mandated — so what the contract says and what the gateway does can differ.

    Only money that already exists is projected. Future sales are not
    forecast, because predicting demand from a short history would be a
    fabricated number wearing a confidence interval.
    """
    conn = _conn()
    try:
        rid = run_id or _latest_run_with("forecast", conn) or _latest_run_id(conn)
        m = _metrics(rid, conn)
        fc = m.get("forecast")
        if not fc:
            raise HTTPException(404, f"No forecast stored for run {rid}")
        return {"run_id": rid, **fc}
    finally:
        conn.close()


@app.get("/journal", tags=["reporting"])
def journal(run_id: Optional[str] = None):
    """The double-entry postings this reconciliation implies, plus the trial
    balance. Entries that do not balance to the paise are rejected upstream
    and never appear here."""
    conn = _conn()
    try:
        rid = run_id or _latest_run_with("journal_summary", conn) or _latest_run_id(conn)
        m = _metrics(rid, conn)
        summary = m.get("journal_summary")
        if not summary:
            raise HTTPException(404, f"No journal stored for run {rid}")

        report_path = os.path.join(cfg.OUTPUT_DIR, "reconciliation_report.json")
        entries = []
        if os.path.exists(report_path):
            with open(report_path) as f:
                entries = json.load(f).get("journal", [])
        return {"run_id": rid, "summary": summary, "entries": entries}
    finally:
        conn.close()


@app.get("/marketplace", tags=["scenarios"])
def marketplace(seed: int = 42):
    """Run the marketplace scenario, where the platform pays third-party
    sellers and Section 194-O TDS and Section 52 GST TCS therefore apply.

    This is a separate endpoint, not part of the main pipeline, because
    neither deduction applies to a pure payment aggregator — modelling them
    in the default flow would produce confident, wrong exceptions on every row.
    """
    from marketplace_scenario import run_scenario
    return run_scenario(seed=seed, quiet=True)


# ---------------------------------------------------------------------------
# Adversarial verification
# ---------------------------------------------------------------------------

@app.get("/verification", tags=["verification"])
def verification():
    """The most recent adversarial verification report.

    This is the engine's evidence about ITSELF: the smallest fault of each kind
    it can detect, whether its confidence scores are honest, and where it
    fails. Generated by `python3 src/run_verification.py`.
    """
    path = os.path.join(cfg.OUTPUT_DIR, "verification_report.json")
    if not os.path.exists(path):
        raise HTTPException(
            503, "No verification report yet. Run: python3 src/run_verification.py")
    with open(path) as f:
        return json.load(f)


@app.get("/verification/detection-limits", tags=["verification"])
def detection_limits():
    """Detection rate as a function of fault magnitude, per fault type, with
    Wilson confidence intervals.

    A point estimate from a handful of trials is not evidence, so every rate
    is reported with its interval and the harness refuses to claim a floor it
    cannot support.
    """
    path = os.path.join(cfg.OUTPUT_DIR, "verification_report.json")
    if not os.path.exists(path):
        raise HTTPException(503, "No verification report yet.")
    with open(path) as f:
        r = json.load(f)
    return {"profile": r["profile"], "total_trials": r["total_attack_trials"],
            "detection_limits": r["detection_limits"],
            "blind_spots": r["blind_spots"],
            "underpowered": r.get("underpowered", [])}


@app.get("/verification/calibration", tags=["verification"])
def calibration():
    """Is the confidence score honest? Reliability curve, expected calibration
    error and Brier score, measured against the generator's ground truth."""
    path = os.path.join(cfg.OUTPUT_DIR, "verification_report.json")
    if not os.path.exists(path):
        raise HTTPException(503, "No verification report yet.")
    with open(path) as f:
        return json.load(f)["calibration"]


@app.get("/verification/counterfactuals", tags=["verification"])
def counterfactuals():
    """For each exception, the minimal change that would make it reconcile.

    Turns 'this did not match' into 'this would match if the amount were
    Rs 12.40 higher — which is exactly the GST, so the GST leg is likely
    duplicated'.
    """
    path = os.path.join(cfg.OUTPUT_DIR, "verification_report.json")
    if not os.path.exists(path):
        raise HTTPException(503, "No verification report yet.")
    with open(path) as f:
        cfs = json.load(f)["counterfactuals"]
    return {"count": len(cfs),
            "actionable": sum(1 for c in cfs
                              if c["counterfactual"].get("actionable")),
            "counterfactuals": cfs}


@app.post("/verification/run", tags=["verification"],
         dependencies=[Depends(acc.require_role(acc.Role.OPERATOR))])
def run_verification_endpoint(profile: str = Query("quick",
                                                   pattern="^(quick|standard|thorough)$")):
    """Run the harness on demand. 'quick' takes about a minute; 'thorough'
    takes considerably longer but is the only profile that can establish
    detection floors.

    Requires operator role (see /run for why)."""
    from run_verification import run as run_verify
    r = run_verify(profile=profile, quiet=True)
    return {"profile": r["profile"], "headline": r["headline"],
            "total_attack_trials": r["total_attack_trials"],
            "blind_spots": r["blind_spots"],
            "calibration": r["calibration"]}


# ---------------------------------------------------------------------------
# Natural-language Q&A (grounded — see src/qa_agent.py for the design rule)
# ---------------------------------------------------------------------------

class QARequest(BaseModel):
    question: str
    run_id: Optional[str] = None


class QAResponse(BaseModel):
    answer: str
    llm_used: bool
    run_id: Optional[str]
    retrieval_strategy: List[str]
    grounded_rows: List[dict]
    security_flagged: bool = False


@app.post("/qa", response_model=QAResponse, tags=["qa"],
          dependencies=[Depends(acc.require_role(acc.Role.OPERATOR))])
def ask_question(req: QARequest, request: Request):
    """Ask a natural-language question about a reconciliation run.

    This is retrieval-grounded, not free-form chat: the question is first used
    to deterministically pull real rows from the audit trail (by entity id,
    by variance-code keyword, or the run's headline metrics), and only that
    retrieved data is shown to the language model. The model is instructed to
    answer from the provided context alone and never compute a new rupee
    figure — the same principle that keeps the matching engine itself
    rule-based rather than LLM-driven.

    Requires operator role. Natural-language querying is gated because it is
    the one endpoint that sends merchant financial data to a third-party LLM
    provider; on a public deployment that should be a deliberate,
    credentialed action rather than something any visitor can trigger. Set
    SADHAKA_OPERATOR_KEY and send X-Sadhaka-Role: operator with a matching
    X-Sadhaka-Key. With the variable unset, this endpoint is unreachable —
    fail closed.

    Security, in order of operation:
      0. Role check (operator or above), enforced server-side.
      1. Rate limited per client IP (20 requests/60s) — see src/security.py.
      2. The question is screened for prompt-injection patterns before
         anything else happens. A flagged question is not blocked outright
         (that would make the feature unusable on false positives) but is
         reported via `security_flagged` in the response.
      3. Screened or not, every question reaches the model only inside a
         structurally isolated prompt: wrapped in explicit boundary markers
         the model is instructed to treat as inert data, never instructions.
      4. The model's own output is checked for boundary-token leakage, which
         would indicate the isolation itself failed.

    Works with no LLM configured: set GEMINI_API_KEY to get a phrased natural-
    language answer, or leave it unset to get the retrieved rows formatted
    directly. Either way, `grounded_rows` in the response lets you verify the
    answer against the actual source data rather than trust it blindly.
    """
    allowed, msg = sec.check_rate_limit(acc.client_identity(request))
    if not allowed:
        raise HTTPException(status_code=429, detail=msg)

    from qa_agent import answer_question
    result = answer_question(req.question, cfg.AUDIT_DB, req.run_id)
    return QAResponse(**result)


@app.get("/qa/status", tags=["qa"])
def qa_status():
    """Whether the Q&A endpoint has an LLM configured, and which model."""
    from qa_agent import is_llm_available, MODEL_NAME
    available = is_llm_available()
    return {
        "llm_configured": available,
        "model": MODEL_NAME if available else None,
        "note": ("Set the GEMINI_API_KEY environment variable to enable "
                 "natural-language phrasing. Without it, /qa still works and "
                 "returns the retrieved audit-trail rows directly.") if not available
                else "Gemini is configured and answering from retrieved context.",
    }


# ---------------------------------------------------------------------------
# Audit-ready PDF export
# ---------------------------------------------------------------------------

@app.get("/report/pdf", tags=["reporting"])
def download_pdf_report():
    """Generate and return the audit-ready PDF reconciliation report.

    Unlike a dashboard screenshot, this is a real accounting document a
    merchant could file with an accountant or a GST officer: an executive
    summary, a full exception schedule with the reason recorded for each
    variance, a GST/ITC statement, journal entries with a trial balance, the
    forward cash position, and a verification appendix. Every figure is read
    from the same reconciliation_report.json, journal_entries.csv and
    verification_report.json the rest of this API reads from — nothing is
    recomputed for the PDF.

    Regenerated fresh on each call from current output/ files, so it always
    reflects the most recent pipeline run.
    """
    from fastapi.responses import FileResponse
    from generate_pdf_report import build as build_pdf

    out_path = os.path.join(cfg.OUTPUT_DIR, "Sadhaka_Reconciliation_Report.pdf")
    try:
        build_pdf(out_path)
    except SystemExit as e:
        raise HTTPException(400, str(e))

    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename="Sadhaka_Reconciliation_Report.pdf",
    )


# ---------------------------------------------------------------------------
# Ledger: historical runs, adjusting entries, admin change log
# ---------------------------------------------------------------------------

class AdjustmentLine(BaseModel):
    account_code: str
    account_name: str
    debit_paise: int = 0
    credit_paise: int = 0
    memo: str = ""


class AdjustmentRequest(BaseModel):
    run_id: str
    kind: str                      # journal_correction | exception_resolution | annotation
    reason: str
    targets: dict = {}
    lines: List[AdjustmentLine] = []
    detail: dict = {}


class ReversalRequest(BaseModel):
    adjustment_id: str
    reason: str


@app.get("/ledger/runs", tags=["ledger"])
def ledger_runs(limit: int = Query(50, le=200)):
    """Every reconciliation run recorded, newest first, with headline metrics.

    This is the ledger's time dimension: the engine never overwrites a run,
    so the full history of what was reconciled and when stays queryable.
    Runs that stored no metrics (scenario runs, for instance) are returned
    with has_metrics=false rather than dropped — an incomplete row is
    information; a missing one is not.
    """
    from ledger import list_runs_with_metrics
    runs = list_runs_with_metrics(limit=limit)
    return {"count": len(runs), "runs": runs}


@app.get("/ledger/adjustments", tags=["ledger"])
def ledger_adjustments(run_id: Optional[str] = None, kind: Optional[str] = None,
                       include_reversed: bool = True,
                       limit: int = Query(200, le=1000)):
    """Adjusting entries posted against reconciliation runs.

    Corrections are append-only: an adjustment never modifies or deletes what
    the engine wrote. A withdrawn correction is reversed by posting a further
    adjustment, and both stay visible.
    """
    from ledger import list_adjustments, adjustment_summary
    return {
        "summary": adjustment_summary(run_id),
        "adjustments": list_adjustments(run_id=run_id, kind=kind,
                                        include_reversed=include_reversed,
                                        limit=limit),
    }


@app.post("/ledger/adjustments", tags=["ledger"],
          dependencies=[Depends(acc.require_role(acc.Role.ADMIN))])
def post_ledger_adjustment(req: AdjustmentRequest,
                           role: acc.Role = Depends(acc.get_role)):
    """Post an adjusting entry. Requires admin role.

    Deliberately NOT an edit endpoint. The engine's decisions are immutable;
    this posts a correction alongside them, carrying a required reason and an
    author. A journal_correction must balance to the paise, exactly as an
    engine-generated entry must — a correction that does not balance is not a
    correction, it is a new error.
    """
    from ledger import post_adjustment, log_admin_action
    payload = ({"lines": [l.model_dump() for l in req.lines]}
               if req.kind == "journal_correction" else req.detail)
    try:
        return post_adjustment(
            run_id=req.run_id, kind=req.kind, targets=req.targets,
            reason=req.reason, author=role.name.lower(), payload=payload)
    except ValueError as e:
        log_admin_action("post_adjustment", role.name.lower(), "rejected",
                         detail=str(e)[:200])
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/ledger/adjustments/reverse", tags=["ledger"],
          dependencies=[Depends(acc.require_role(acc.Role.ADMIN))])
def reverse_ledger_adjustment(req: ReversalRequest,
                              role: acc.Role = Depends(acc.get_role)):
    """Reverse a previously posted adjustment by posting a reversal.

    The original stays in the ledger marked 'reversed'. Nothing is deleted,
    because the history of what was concluded and then withdrawn is often the
    most informative part of a ledger.
    """
    from ledger import reverse_adjustment, log_admin_action
    try:
        return reverse_adjustment(req.adjustment_id, req.reason,
                                  role.name.lower())
    except ValueError as e:
        log_admin_action("reverse_adjustment", role.name.lower(), "rejected",
                         target=req.adjustment_id, detail=str(e)[:200])
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/ledger/admin-log", tags=["ledger"],
         dependencies=[Depends(acc.require_role(acc.Role.OPERATOR))])
def ledger_admin_log(limit: int = Query(200, le=1000)):
    """Every action taken through the admin surface, accepted or rejected.

    Rejections are logged too: a change log that records only successes is
    not an audit log, because the attempts that were refused are exactly what
    a reviewer needs to see.
    """
    from ledger import list_admin_actions
    actions = list_admin_actions(limit=limit)
    return {
        "count": len(actions),
        "accepted": sum(1 for a in actions if a["outcome"] == "accepted"),
        "rejected": sum(1 for a in actions if a["outcome"] == "rejected"),
        "actions": actions,
    }


@app.get("/ledger/trend", tags=["ledger"])
def ledger_trend(limit: int = Query(30, le=100)):
    """Match rates and exception counts across historical runs, oldest first.

    Shaped for charting: a single run's match rate says nothing about whether
    reconciliation quality is improving or degrading. The trend does.
    """
    from ledger import list_runs_with_metrics
    runs = [r for r in list_runs_with_metrics(limit=limit) if r.get("has_metrics")]
    runs.reverse()
    return {
        "count": len(runs),
        "points": [{
            "run_id": r["run_id"],
            "started_at": r["started_at"],
            "value_match_rate_pct": r.get("value_match_rate_pct"),
            "batch_match_rate_pct": r.get("batch_match_rate_pct"),
            "order_match_rate_pct": r.get("order_match_rate_pct"),
            "exceptions_total": r.get("exceptions_total"),
            "exceptions_actionable": r.get("exceptions_actionable"),
            "exceptions_benign": r.get("exceptions_benign"),
            "records": r.get("records"),
        } for r in runs],
    }

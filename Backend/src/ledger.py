"""
Sadhaka — Adjusting Entries Ledger
====================================
Lets an authorised operator correct the reconciliation record WITHOUT
destroying it.

WHY EDITING IS NOT ON OFFER
----------------------------
The central claim of this project is that the audit trail is an objective
record: every figure traces to a rule that fired on specific data, and
nothing is recomputed for display. An admin who can edit a posted decision
destroys that claim outright — any reviewer of the ledger afterwards has to
ask "is this what the engine decided, or what someone changed it to?", and
there is no way to answer from the data.

So this module offers the accounting-correct alternative, which is also the
more useful one: **adjusting entries**. A correction is a NEW entry posted
alongside the original, carrying a reason, an author, and a pointer to what
it adjusts. The original is never modified or deleted. This is how real
double-entry bookkeeping handles error correction — a posted entry is
reversed, never erased — and it means the ledger can show both "what the
engine found" and "what a human concluded after review", which is strictly
more information than an edited record could ever carry.

WHAT AN ADJUSTMENT CAN AND CANNOT DO
-------------------------------------
CAN:  post a balanced correcting journal entry; annotate an exception with a
      resolution; record that a variance was investigated and accepted.
CANNOT: change a match decision, alter a confidence score, delete an
      exception, or modify anything the engine wrote. Those are engine
      outputs. An adjustment sits on top of them.

Every adjustment is itself auditable: who, when, why, and against what.
"""

import os
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

import config as cfg


SCHEMA = """
CREATE TABLE IF NOT EXISTS adjustments (
    adjustment_id   TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    kind            TEXT NOT NULL,      -- journal_correction | exception_resolution | annotation
    targets         TEXT NOT NULL,      -- JSON: what this adjusts (entry_id, subject_id, ...)
    reason          TEXT NOT NULL,      -- required. an adjustment with no stated reason is
                                        -- indistinguishable from tampering.
    author          TEXT NOT NULL,      -- role that posted it
    payload         TEXT NOT NULL,      -- JSON: the correcting lines / resolution detail
    amount_paise    INTEGER,
    status          TEXT NOT NULL DEFAULT 'posted',   -- posted | reversed
    reversed_by     TEXT,               -- adjustment_id of the reversal, if any
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_adj_run     ON adjustments(run_id);
CREATE INDEX IF NOT EXISTS idx_adj_kind    ON adjustments(kind);
CREATE INDEX IF NOT EXISTS idx_adj_created ON adjustments(created_at);

-- Every action taken through the admin surface is logged here, including
-- ones that were rejected. A change log that only records successes is not
-- an audit log.
CREATE TABLE IF NOT EXISTS admin_actions (
    action_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    actor_role      TEXT NOT NULL,
    target          TEXT,
    detail          TEXT,
    outcome         TEXT NOT NULL,      -- accepted | rejected
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_created ON admin_actions(created_at);
"""

VALID_KINDS = {"journal_correction", "exception_resolution", "annotation"}


def _conn(db_path=None):
    path = db_path or cfg.AUDIT_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def log_admin_action(action: str, actor_role: str, outcome: str,
                     target: Optional[str] = None, detail: Optional[str] = None,
                     db_path=None):
    """Record an admin-surface action. Called for rejections too."""
    c = _conn(db_path)
    try:
        c.execute("""
            INSERT INTO admin_actions (action, actor_role, target, detail,
                                       outcome, created_at)
            VALUES (?,?,?,?,?,?)
        """, (action, actor_role, target, detail, outcome,
              datetime.now().isoformat(timespec="seconds")))
        c.commit()
    finally:
        c.close()


def post_adjustment(run_id: str, kind: str, targets: dict, reason: str,
                    author: str, payload: dict, db_path=None) -> dict:
    """Post an adjusting entry. Returns the created record.

    Validation is strict and deliberate:
      - kind must be one of the three permitted types
      - reason must be substantive (an empty or trivial reason makes the
        adjustment unauditable, which defeats the point)
      - a journal_correction must balance to the paise, exactly as engine-
        generated entries must. A correction that does not balance is not a
        correction, it is a new error.
    """
    if kind not in VALID_KINDS:
        raise ValueError(
            f"Unknown adjustment kind '{kind}'. Must be one of: "
            f"{', '.join(sorted(VALID_KINDS))}")

    if not reason or len(reason.strip()) < 10:
        raise ValueError(
            "An adjustment requires a substantive reason (at least 10 "
            "characters). An adjustment with no stated reason is "
            "indistinguishable from tampering when read back later.")

    amount = None

    if kind == "journal_correction":
        lines = payload.get("lines") or []
        if not lines:
            raise ValueError("A journal correction needs at least one line.")
        total_dr = sum(int(l.get("debit_paise", 0) or 0) for l in lines)
        total_cr = sum(int(l.get("credit_paise", 0) or 0) for l in lines)
        if total_dr != total_cr:
            raise ValueError(
                f"Correcting entry does not balance: debits "
                f"{cfg.rupees(total_dr)} against credits {cfg.rupees(total_cr)}. "
                f"A correction must balance to the paise, exactly as an "
                f"engine-generated entry must.")
        if total_dr == 0:
            raise ValueError("A correcting entry of zero has no effect.")
        amount = total_dr

    adjustment_id = f"adj_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

    c = _conn(db_path)
    try:
        c.execute("""
            INSERT INTO adjustments (adjustment_id, run_id, kind, targets,
                                     reason, author, payload, amount_paise,
                                     status, created_at)
            VALUES (?,?,?,?,?,?,?,?,'posted',?)
        """, (adjustment_id, run_id, kind, json.dumps(targets), reason.strip(),
              author, json.dumps(payload), amount,
              datetime.now().isoformat(timespec="seconds")))
        c.commit()
    finally:
        c.close()

    log_admin_action("post_adjustment", author, "accepted",
                     target=adjustment_id,
                     detail=f"{kind}: {reason[:120]}", db_path=db_path)

    return get_adjustment(adjustment_id, db_path)


def reverse_adjustment(adjustment_id: str, reason: str, author: str,
                       db_path=None) -> dict:
    """Reverse a previously posted adjustment by posting a REVERSAL, not by
    deleting. The original stays in the ledger marked 'reversed', and the
    reversal is itself a first-class adjustment with its own reason.

    Two entries where a naive design would have zero. That is the point: the
    history of what was concluded and then un-concluded is often the most
    informative part of a ledger."""
    original = get_adjustment(adjustment_id, db_path)
    if not original:
        raise ValueError(f"No adjustment '{adjustment_id}' to reverse.")
    if original["status"] == "reversed":
        raise ValueError(
            f"Adjustment '{adjustment_id}' is already reversed by "
            f"{original.get('reversed_by')}.")

    payload = original["payload"]
    if original["kind"] == "journal_correction":
        # flip every line: debits become credits and vice versa
        flipped = []
        for l in payload.get("lines", []):
            flipped.append({
                **l,
                "debit_paise": int(l.get("credit_paise", 0) or 0),
                "credit_paise": int(l.get("debit_paise", 0) or 0),
                "memo": f"Reversal of {adjustment_id}: {l.get('memo', '')}".strip(),
            })
        payload = {"lines": flipped, "reverses": adjustment_id}
    else:
        payload = {"reverses": adjustment_id, "original_payload": payload}

    reversal = post_adjustment(
        run_id=original["run_id"],
        kind=original["kind"],
        targets={"reverses": adjustment_id, **(original["targets"] or {})},
        reason=f"Reversal of {adjustment_id}. {reason}",
        author=author,
        payload=payload,
        db_path=db_path,
    )

    c = _conn(db_path)
    try:
        c.execute("UPDATE adjustments SET status='reversed', reversed_by=? "
                  "WHERE adjustment_id=?",
                  (reversal["adjustment_id"], adjustment_id))
        c.commit()
    finally:
        c.close()

    log_admin_action("reverse_adjustment", author, "accepted",
                     target=adjustment_id,
                     detail=f"reversed by {reversal['adjustment_id']}",
                     db_path=db_path)

    return reversal


def _row_to_dict(r) -> dict:
    d = dict(r)
    for field in ("targets", "payload"):
        try:
            d[field] = json.loads(d[field]) if d.get(field) else {}
        except (json.JSONDecodeError, TypeError):
            d[field] = {}
    if d.get("amount_paise") is not None:
        d["amount"] = cfg.rupees(d["amount_paise"])
    return d


def get_adjustment(adjustment_id: str, db_path=None) -> Optional[dict]:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT * FROM adjustments WHERE adjustment_id=?",
                      (adjustment_id,)).fetchone()
        return _row_to_dict(r) if r else None
    finally:
        c.close()


def list_adjustments(run_id: Optional[str] = None, kind: Optional[str] = None,
                     include_reversed: bool = True, limit: int = 200,
                     db_path=None) -> list:
    c = _conn(db_path)
    try:
        q = "SELECT * FROM adjustments WHERE 1=1"
        params = []
        if run_id:
            q += " AND run_id=?"; params.append(run_id)
        if kind:
            q += " AND kind=?"; params.append(kind)
        if not include_reversed:
            q += " AND status != 'reversed'"
        q += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        return [_row_to_dict(r) for r in c.execute(q, params).fetchall()]
    finally:
        c.close()


def list_admin_actions(limit: int = 200, db_path=None) -> list:
    """The change log: every admin action, accepted or rejected."""
    c = _conn(db_path)
    try:
        rows = c.execute(
            f"SELECT * FROM admin_actions ORDER BY action_id DESC LIMIT {int(limit)}"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def adjustment_summary(run_id: Optional[str] = None, db_path=None) -> dict:
    """Aggregate view for the ledger page."""
    adj = list_adjustments(run_id=run_id, db_path=db_path)
    active = [a for a in adj if a["status"] == "posted"]
    reversed_ = [a for a in adj if a["status"] == "reversed"]

    by_kind = {}
    for a in active:
        b = by_kind.setdefault(a["kind"], {"count": 0, "value_paise": 0})
        b["count"] += 1
        b["value_paise"] += a.get("amount_paise") or 0
    for k, b in by_kind.items():
        b["value"] = cfg.rupees(b["value_paise"])

    net = sum(a.get("amount_paise") or 0 for a in active)
    return {
        "total_adjustments": len(adj),
        "active": len(active),
        "reversed": len(reversed_),
        "by_kind": by_kind,
        "net_correction_paise": net,
        "net_correction": cfg.rupees(net),
        "note": ("Reversed adjustments remain in the ledger and are counted "
                 "in the total but excluded from the net, so the history of "
                 "what was concluded and later withdrawn stays visible."),
    }


# ---------------------------------------------------------------------------
# Historical runs — the ledger's time dimension
# ---------------------------------------------------------------------------

def list_runs_with_metrics(limit: int = 50, db_path=None) -> list:
    """Every reconciliation run with its headline metrics, newest first.

    Powers the multi-run ledger and the trend charts. Runs that stored no
    metrics (e.g. a scenario run) are included but flagged, rather than
    silently dropped — an incomplete row is information, a missing one is not.
    """
    path = db_path or cfg.AUDIT_DB
    if not os.path.exists(path):
        return []
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        runs = c.execute(
            f"SELECT * FROM runs ORDER BY started_at DESC LIMIT {int(limit)}"
        ).fetchall()
        out = []
        for r in runs:
            row = dict(r)
            m = c.execute(
                "SELECT metric_value FROM run_metrics "
                "WHERE run_id=? AND metric_key='metrics'", (row["run_id"],)
            ).fetchone()
            decisions = c.execute(
                "SELECT COUNT(*) n FROM decisions WHERE run_id=?",
                (row["run_id"],)).fetchone()["n"]
            row["decision_count"] = decisions

            if m:
                metrics = json.loads(m["metric_value"])
                mr = metrics.get("match_rates", {})
                ex = metrics.get("exceptions", {})
                tp = metrics.get("throughput", {})
                row["has_metrics"] = True
                row["value_match_rate_pct"] = mr.get("value_match_rate_pct")
                row["batch_match_rate_pct"] = mr.get("batch_match_rate_pct")
                row["order_match_rate_pct"] = mr.get("order_match_rate_pct")
                row["exceptions_total"] = ex.get("total")
                row["exceptions_actionable"] = ex.get("actionable")
                row["exceptions_benign"] = ex.get("benign")
                row["records"] = tp.get("total_records_processed")
                row["money"] = metrics.get("money", {})
            else:
                row["has_metrics"] = False
            out.append(row)
        return out
    finally:
        c.close()

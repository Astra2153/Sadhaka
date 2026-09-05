"""
Sadhaka — Audit Trail
=====================
Every decision the engine makes is written here before it is reported anywhere
else. The dashboard, the metrics and the Q&A layer all read FROM this trail —
none of them recompute. That is deliberate: if the number on the screen came
from somewhere other than the audit trail, the audit trail is decorative.

Design properties:
  * Append-only within a run. Decisions are never mutated after they are written.
  * Idempotent across runs. Each run gets a run_id; re-running does not
    double-count, and prior runs stay queryable for comparison.
  * Self-describing. Every row carries the rule that fired, the confidence, the
    inputs compared, and a human-readable reason.
"""

import sqlite3
import json
import os
import uuid
from datetime import datetime
from contextlib import contextmanager

import config as cfg


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    dataset_hash    TEXT,
    engine_version  TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    stage           TEXT NOT NULL,      -- stage1_bank_batch | stage2_order | stage3_gst
    subject_type    TEXT NOT NULL,      -- bank_txn | settlement | order | payment | invoice
    subject_id      TEXT NOT NULL,
    counterpart_type TEXT,
    counterpart_id  TEXT,
    outcome         TEXT NOT NULL,      -- MATCHED | UNMATCHED | EXCEPTION
    variance_code   TEXT,
    confidence      REAL NOT NULL,
    rule_fired      TEXT NOT NULL,
    amount_subject  INTEGER,
    amount_counterpart INTEGER,
    variance_paise  INTEGER,
    reason          TEXT NOT NULL,
    evidence        TEXT,               -- JSON: what was actually compared
    created_at      TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_run     ON decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_decisions_subject ON decisions(subject_id);
CREATE INDEX IF NOT EXISTS idx_decisions_outcome ON decisions(outcome);
CREATE INDEX IF NOT EXISTS idx_decisions_code    ON decisions(variance_code);
CREATE INDEX IF NOT EXISTS idx_decisions_stage   ON decisions(stage);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id          TEXT NOT NULL,
    metric_key      TEXT NOT NULL,
    metric_value    TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_key)
);
"""

ENGINE_VERSION = "0.3.0"


class AuditTrail:
    def __init__(self, db_path=None, run_notes=""):
        self.db_path = db_path or cfg.AUDIT_DB
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT INTO runs (run_id, started_at, engine_version, notes) VALUES (?,?,?,?)",
            (self.run_id, datetime.now().isoformat(timespec="seconds"),
             ENGINE_VERSION, run_notes),
        )
        self._conn.commit()
        self._pending = []

    # -- writing -----------------------------------------------------------

    def record(self, stage, subject_type, subject_id, outcome, confidence,
               rule_fired, reason, counterpart_type=None, counterpart_id=None,
               variance_code=None, amount_subject=None, amount_counterpart=None,
               variance_paise=None, evidence=None):
        """Queue a decision. Flushed in batches for speed; call .flush() to force."""
        self._pending.append((
            self.run_id, stage, subject_type, str(subject_id),
            counterpart_type, (str(counterpart_id) if counterpart_id else None),
            outcome, variance_code, float(confidence), rule_fired,
            amount_subject, amount_counterpart, variance_paise, reason,
            json.dumps(evidence or {}, default=str),
            datetime.now().isoformat(timespec="seconds"),
        ))
        if len(self._pending) >= 200:
            self.flush()

    def flush(self):
        if not self._pending:
            return
        self._conn.executemany("""
            INSERT INTO decisions
              (run_id, stage, subject_type, subject_id, counterpart_type,
               counterpart_id, outcome, variance_code, confidence, rule_fired,
               amount_subject, amount_counterpart, variance_paise, reason,
               evidence, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, self._pending)
        self._conn.commit()
        self._pending = []

    def set_metric(self, key, value):
        self._conn.execute(
            "INSERT OR REPLACE INTO run_metrics (run_id, metric_key, metric_value) VALUES (?,?,?)",
            (self.run_id, key, json.dumps(value, default=str)),
        )
        self._conn.commit()

    def finish(self):
        self.flush()
        self._conn.execute("UPDATE runs SET finished_at=? WHERE run_id=?",
                           (datetime.now().isoformat(timespec="seconds"), self.run_id))
        self._conn.commit()

    # -- reading -----------------------------------------------------------

    def decisions(self, run_id=None, outcome=None, stage=None,
                  variance_code=None, subject_id=None, limit=None):
        q = "SELECT * FROM decisions WHERE run_id = ?"
        params = [run_id or self.run_id]
        if outcome:
            q += " AND outcome = ?"; params.append(outcome)
        if stage:
            q += " AND stage = ?"; params.append(stage)
        if variance_code:
            q += " AND variance_code = ?"; params.append(variance_code)
        if subject_id:
            q += " AND (subject_id = ? OR counterpart_id = ?)"
            params += [subject_id, subject_id]
        q += " ORDER BY decision_id"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def trace(self, entity_id, run_id=None):
        """Everything the engine ever decided about one entity. Powers 'why
        didn't order X match?' without the answer being regenerated."""
        rows = self._conn.execute("""
            SELECT * FROM decisions
            WHERE run_id = ?
              AND (subject_id = ? OR counterpart_id = ?
                   OR evidence LIKE ?)
            ORDER BY decision_id
        """, (run_id or self.run_id, entity_id, entity_id, f'%{entity_id}%')).fetchall()
        return [dict(r) for r in rows]

    def metrics(self, run_id=None):
        rows = self._conn.execute(
            "SELECT metric_key, metric_value FROM run_metrics WHERE run_id=?",
            (run_id or self.run_id,)).fetchall()
        return {r["metric_key"]: json.loads(r["metric_value"]) for r in rows}

    def list_runs(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC").fetchall()]

    def close(self):
        self.flush()
        self._conn.close()


@contextmanager
def audit_run(db_path=None, notes=""):
    a = AuditTrail(db_path, notes)
    try:
        yield a
        a.finish()
    finally:
        a.close()

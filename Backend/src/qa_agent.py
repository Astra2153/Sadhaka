"""
Sadhaka — Natural-Language Q&A Agent
=====================================
Answers plain-English questions about a reconciliation run.

THE CENTRAL DESIGN RULE
------------------------
The LLM never decides what happened to the money. It only phrases and reasons
over data that was already retrieved deterministically from the audit trail.

This is not a style preference — it is the same principle that keeps the
matching engine itself rule-based rather than LLM-driven (see stage1/stage2):
a reconciliation answer has to be traceable to a specific row, a specific
rule, a specific number. An LLM asked "did this reconcile?" with no retrieval
step would happily generate a plausible-sounding wrong answer, and nothing
downstream would catch it, because on a chat page a hallucination reads
exactly like a real answer.

So every question goes through two strictly separated stages:

  1. RETRIEVAL (deterministic, no LLM) — pull the actual rows: entity
     lookups by ID, a keyword/code search across the decision log, and the
     run's stored metrics. This is the same SQLite audit trail every other
     endpoint reads from.

  2. GENERATION (LLM, grounded) — Gemini receives ONLY the retrieved rows
     as context and is explicitly instructed to answer from them alone, to
     say plainly when the context doesn't cover the question, and never to
     compute a new number that isn't already present in the context.

If GEMINI_API_KEY is not set, the endpoint still works: it returns the
retrieved context directly with a templated (non-LLM) narrative, so the
feature degrades to "real data, plainly formatted" rather than failing.
"""

import os
import re
import json
import sqlite3
from typing import Optional

import config as cfg
import security as sec

# gemini-2.0-flash was retired; Google's own 404 response for that model
# names the replacement directly, so using it here rather than guessing.
MODEL_NAME = "gemini-3.6-flash"

SYSTEM_INSTRUCTION = """You are a query interface over a payment settlement reconciliation audit trail. You answer questions about ONE specific run using ONLY the data provided to you in the context block below.

Rules you must follow exactly:
1. Never invent, estimate, or compute a rupee figure that is not already present in the context. If the context does not contain the number needed to answer, say so plainly — do not approximate.
2. Every claim you make must be traceable to a specific line in the context. Where useful, mention the entity id (order_, pay_, setl_, bnk_) so the person can look it up themselves.
3. If the question asks about something outside the provided context (e.g. a different run, an entity not retrieved, general reconciliation advice), say the context doesn't cover it rather than guessing.
4. You are explaining decisions a deterministic rule-based engine already made. You are not making reconciliation decisions yourself, and you never claim more certainty than the underlying confidence score shown in the context.
5. Keep answers concise — a few sentences unless the question genuinely needs a list or table.
6. Use rupee formatting exactly as given in the context (e.g. "Rs 1,234.56"); do not reformat or recompute it.
"""

_client = None


def _get_client():
    """Lazy singleton so import doesn't fail when no key is configured."""
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    from google import genai
    _client = genai.Client(api_key=key)
    return _client


def is_llm_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


# ---------------------------------------------------------------------------
# Retrieval — deterministic, reads the same audit trail every other endpoint uses
# ---------------------------------------------------------------------------

ENTITY_PATTERN = re.compile(
    r"\b(order_\w+|pay_\w+|setl_\w+|bnk_\w+|rfnd_\w+|disp_\w+|adj_\w+)\b"
)

KEYWORD_TO_CODE = {
    "chargeback": "CHARGEBACK", "dispute": "CHARGEBACK",
    "refund": "PARTIAL_PAYMENT", "partial": "PARTIAL_PAYMENT",
    "hold": "ON_HOLD", "reserve": "ON_HOLD",
    "rounding": "ROUNDING",
    "delay": "TIMING_LAG", "late": "TIMING_LAG", "timing": "TIMING_LAG",
    "unsettled": "NOT_YET_SETTLED", "not yet settled": "NOT_YET_SETTLED",
    "fee": "FEE_DEDUCTION", "mdr": "FEE_DEDUCTION", "overcharge": "FEE_DEDUCTION",
    "gst": "TAX_DEDUCTION", "tax": "TAX_DEDUCTION",
    "duplicate": "DUPLICATE_CANDIDATE",
    "unexplained": "UNEXPLAINED", "unknown": "UNEXPLAINED",
}


def retrieve_context(question: str, db_path: str, run_id: Optional[str] = None,
                     max_rows: int = 25) -> dict:
    """Pull the rows relevant to a question, deterministically.

    Combines three retrieval strategies, matching the three ways a person
    actually asks about reconciliation data:
      - explicit entity ids mentioned in the question -> exact trace
      - variance-code keywords -> filtered decision rows
      - otherwise -> the run's stored summary metrics
    """
    if not os.path.exists(db_path):
        return {"error": "No audit trail found. Run the pipeline first.",
                "rows": [], "metrics": {}}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rid = run_id
        if not rid:
            row = conn.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            rid = row["run_id"] if row else None
        if not rid:
            return {"error": "No runs recorded yet.", "rows": [], "metrics": {}}

        context = {"run_id": rid, "rows": [], "metrics": {}, "retrieval": []}

        # 1. explicit entity ids
        entity_ids = ENTITY_PATTERN.findall(question)
        for eid in entity_ids[:5]:
            rows = conn.execute("""
                SELECT * FROM decisions
                WHERE run_id=? AND (subject_id=? OR counterpart_id=? OR evidence LIKE ?)
                ORDER BY decision_id
            """, (rid, eid, eid, f'%"{eid}"%')).fetchall()
            if rows:
                context["retrieval"].append(f"exact match on entity '{eid}'")
                context["rows"].extend(dict(r) for r in rows[:max_rows])

        # 2. keyword -> variance code
        ql = question.lower()
        matched_codes = {code for kw, code in KEYWORD_TO_CODE.items() if kw in ql}
        for code in matched_codes:
            rows = conn.execute("""
                SELECT * FROM decisions WHERE run_id=? AND variance_code=?
                ORDER BY ABS(COALESCE(variance_paise, amount_subject, 0)) DESC
                LIMIT ?
            """, (rid, code, max_rows)).fetchall()
            if rows:
                context["retrieval"].append(f"variance_code = {code}")
                context["rows"].extend(dict(r) for r in rows)

        # 3. always attach the run's headline metrics — cheap, and answers
        #    a large fraction of questions on their own ("what's the match rate")
        mrow = conn.execute(
            "SELECT metric_value FROM run_metrics WHERE run_id=? AND metric_key='metrics'",
            (rid,)
        ).fetchone()
        if mrow:
            context["metrics"] = json.loads(mrow["metric_value"])

        # de-dupe rows (an id can be pulled by both entity and keyword search)
        seen = set()
        deduped = []
        for r in context["rows"]:
            if r["decision_id"] not in seen:
                seen.add(r["decision_id"])
                deduped.append(r)
        context["rows"] = deduped[:max_rows]

        if not context["retrieval"]:
            context["retrieval"].append("no entity id or known keyword found; "
                                        "answering from run-level metrics only")

        return context
    finally:
        conn.close()


def _format_context_for_prompt(context: dict) -> str:
    """Render retrieved rows as compact, LLM-readable text. Kept deterministic
    and simple — this is data going INTO the model, not output."""
    lines = [f"Run: {context.get('run_id', 'unknown')}",
             f"Retrieval strategy used: {'; '.join(context.get('retrieval', []))}",
             ""]

    m = context.get("metrics") or {}
    if m:
        mr = m.get("match_rates", {})
        ex = m.get("exceptions", {})
        money = m.get("money", {})
        lines.append("RUN METRICS:")
        lines.append(f"  batch match rate: {mr.get('batch_match_rate_pct')}% "
                     f"({mr.get('batch_match_denominator')})")
        lines.append(f"  order match rate: {mr.get('order_match_rate_pct')}% "
                     f"({mr.get('order_match_denominator')})")
        lines.append(f"  value match rate: {mr.get('value_match_rate_pct')}% "
                     f"({mr.get('value_match_denominator')})")
        lines.append(f"  exceptions: {ex.get('total')} total "
                     f"({ex.get('actionable')} actionable, {ex.get('benign')} benign)")
        lines.append(f"  actionable value: {ex.get('actionable_value')}")
        for k, v in money.items():
            lines.append(f"  {k.replace('_',' ')}: {v}")
        lines.append("")

    rows = context.get("rows") or []
    if rows:
        lines.append(f"RETRIEVED DECISIONS ({len(rows)} rows):")
        for r in rows:
            amt = cfg.rupees(r["amount_subject"]) if r.get("amount_subject") is not None else "n/a"
            var = cfg.rupees(r["variance_paise"]) if r.get("variance_paise") else "n/a"
            lines.append(
                f"  - [{r.get('outcome')}] {r.get('subject_type')} {r.get('subject_id')}"
                + (f" -> {r.get('counterpart_type')} {r.get('counterpart_id')}"
                   if r.get("counterpart_id") else "")
                + f" | code={r.get('variance_code')} | confidence={r.get('confidence'):.0%}"
                if r.get("confidence") is not None else ""
            )
            lines.append(f"    amount={amt} variance={var} rule={r.get('rule_fired')}")
            lines.append(f"    reason: {r.get('reason')}")
    else:
        lines.append("RETRIEVED DECISIONS: none matched this question.")

    return "\n".join(lines)


_RATE_KEYWORDS = {
    "batch": ("batch_match_rate_pct", "batch_match_denominator"),
    "order": ("order_match_rate_pct", "order_match_denominator"),
    "transaction": ("order_match_rate_pct", "order_match_denominator"),
    "value": ("value_match_rate_pct", "value_match_denominator"),
    "bank": ("bank_value_match_rate_pct", "bank_value_denominator"),
}


def _templated_answer(context: dict, question: str = "") -> str:
    """Non-LLM fallback: the retrieved data, plainly formatted. Used when no
    GEMINI_API_KEY is set, so the feature degrades gracefully instead of
    failing outright.

    Without an LLM to disambiguate intent, "match rate" is genuinely
    ambiguous across four different numbers — so this picks the specific rate
    a keyword in the question points to, and only falls back to the value
    rate (the one this project treats as most decision-relevant) when nothing
    in the question narrows it down.
    """
    rows = context.get("rows") or []
    if not rows:
        m = context.get("metrics", {})
        if not m:
            return context.get("error", "No data found for this question.")
        mr = m.get("match_rates", {})
        ql = question.lower()
        pct_key, den_key = mr and next(
            (v for k, v in _RATE_KEYWORDS.items() if k in ql),
            ("value_match_rate_pct", "value_match_denominator")
        )
        return (f"{pct_key.replace('_pct','').replace('_',' ')}: "
                f"{mr.get(pct_key)}% ({mr.get(den_key)}). "
                f"{m.get('exceptions', {}).get('total')} total exceptions, "
                f"{m.get('exceptions', {}).get('actionable')} needing action.")
    parts = []
    for r in rows[:5]:
        parts.append(f"[{r.get('outcome')}] {r.get('subject_id')}: {r.get('reason')}")
    return "\n\n".join(parts)


def answer_question(question: str, db_path: str, run_id: Optional[str] = None) -> dict:
    """End-to-end: screen, retrieve, then generate (or fall back to templated).

    Always returns the retrieved rows alongside the answer, so the person can
    verify the answer against the actual source data rather than trust it
    blindly — the same transparency principle as the rest of the audit trail.

    Every question is screened for injection patterns before anything else
    happens. A flagged question is NOT blocked outright (false positives
    would make the feature unusable) but proceeds only through the
    structurally-isolated prompt path, and the flag is reported back in the
    response so the caller knows screening occurred.
    """
    screen = sec.screen_question(question)
    if not screen.safe_to_process:
        return {
            "answer": screen.reason or "This question could not be processed.",
            "llm_used": False, "run_id": None, "retrieval_strategy": [],
            "grounded_rows": [], "security_flagged": False,
        }
    question = screen.sanitized_question

    context = retrieve_context(question, db_path, run_id)

    if context.get("error") and not context.get("metrics"):
        return {"answer": context["error"], "grounded_rows": [],
                "llm_used": False, "run_id": None,
                "retrieval_strategy": [], "security_flagged": screen.flagged}

    client = _get_client()

    if client is None:
        answer = _templated_answer(context, question)
        llm_used = False
    else:
        context_block = _format_context_for_prompt(context)
        hardened_system, user_content = sec.build_isolated_prompt(
            SYSTEM_INSTRUCTION, context_block, question)
        try:
            from google.genai import types as genai_types
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=user_content,
                config=genai_types.GenerateContentConfig(
                    system_instruction=hardened_system,
                    temperature=0.1,      # low: this is retrieval-grounded
                                          # explanation, not creative writing
                    max_output_tokens=400,
                ),
            )
            answer = resp.text or "The model returned an empty response."
            leak_warning = sec.check_output_leak(answer)
            if leak_warning:
                answer = leak_warning
            llm_used = True
        except Exception as e:
            # network/quota/key failure -> degrade to templated, never crash.
            #
            # The full exception is logged with a traceback so it is visible
            # in the server console. An earlier version of this code embedded
            # only type(e).__name__ in the answer and never logged anything,
            # which meant a real Gemini error (bad key, wrong model, quota,
            # region restriction) was completely invisible anywhere -- the
            # console showed nothing because nothing was ever printed. Silent
            # exception handling that leaves no diagnostic trail is a bug.
            import logging
            logging.getLogger("sadhaka.qa").exception(
                "Gemini call failed: %s: %s", type(e).__name__, e)
            answer = (f"[LLM unavailable: {type(e).__name__}: {e}] " +
                     _templated_answer(context, question))
            llm_used = False

    return {
        "answer": answer,
        "llm_used": llm_used,
        "run_id": context.get("run_id"),
        "retrieval_strategy": context.get("retrieval", []),
        "security_flagged": screen.flagged,
        "grounded_rows": [
            {"subject_id": r.get("subject_id"), "outcome": r.get("outcome"),
             "variance_code": r.get("variance_code"), "reason": r.get("reason"),
             "confidence": r.get("confidence")}
            for r in (context.get("rows") or [])[:10]
        ],
    }
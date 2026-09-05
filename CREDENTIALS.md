# Reviewer Credentials

Most of Sadhaka needs no sign-in. Overview, Exceptions, GST & ITC, Cash
forecast, Journal, Audit trail, Marketplace, Verification, Ledger, and API
docs are all open reads — anyone with the URL can see full reconciliation
results.

Two pages send data further or write to the ledger, and are gated on
purpose:

| Page | What it does | Role required | Demo key |
|---|---|---|---|
| `/ask` | Sends retrieved audit-trail data to Gemini for a phrased answer | operator | `ashmit` |
| `/admin` | Posts adjusting entries to the ledger | admin | `ashmit123` |

## How to use them

**On `/ask`:** a sign-in box appears before the conversation UI. Enter
`ashmit` and click Sign in.

**On `/admin`:** select role **admin**, enter `ashmit123`, and click Sign in.

## Why these exist and how they actually work

The key is verified **server-side**, on every single request — not by a
check in the browser. Concretely: the frontend attaches whatever was typed
as an `X-Sadhaka-Key` header, and the backend independently compares it
against an environment variable (`SADHAKA_OPERATOR_KEY` /
`SADHAKA_ADMIN_KEY`) using a constant-time comparison. A forged header with
the wrong value is rejected with `403`, regardless of what the browser UI
shows. This is a genuine access control, not a client-side password screen.

If the backend is started **without** these environment variables set, both
roles become completely unreachable — the default is fail-closed, not
fail-open.

## Why they're written down at all

These are demo values on synthetic data, set for this reviewer-facing
build so a judge doesn't need to inspect the backend `.env` file to test
every page. In a genuine production deployment, `SADHAKA_OPERATOR_KEY` and
`SADHAKA_ADMIN_KEY` would be private secrets, generated randomly and never
committed to a repository or written in a document like this one.

## What each role can and cannot do

**Operator** can trigger `/run`, `/verification/run`, and `/qa`. These
consume compute or send data externally, so they're gated against being
spammed on a public deployment.

**Admin** can additionally post and reverse ledger adjustments. It
**cannot** edit or delete anything the reconciliation engine wrote — no
match decision, no confidence score, no exception. The engine's output
staying immutable is the entire basis for trusting the audit trail, so
corrections are posted as new entries alongside the original, never as
edits. See `/admin` itself, or `Backend/src/ledger.py`, for the full
reasoning.

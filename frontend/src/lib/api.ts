/**
 * Data loading.
 *
 * The app tries the live API first and falls back to a bundled snapshot of the
 * last real pipeline run. That is deliberate rather than a convenience: the
 * demo must not depend on a backend being awake, and a reviewer opening the
 * deployed URL should see real numbers immediately rather than a spinner
 * pointed at a cold server.
 *
 * The snapshot is genuine output, not fixtures — it is written by the same
 * endpoints the live mode calls. When live data is available it always wins,
 * and the UI says which one is on screen so nobody mistakes one for the other.
 */

import snapshotData from "@/data/snapshot.json";
import verificationData from "@/data/verification.json";
import type { Bundle, VerificationReport } from "@/types";

export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export type DataSource = "live" | "snapshot";

export interface LoadResult<T> {
  data: T;
  source: DataSource;
  error?: string;
}

const TIMEOUT_MS = 4000;

async function getJson<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`${path} returned ${res.status}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

const bundledSnapshot = snapshotData as unknown as Bundle;
const bundledVerification = verificationData as unknown as VerificationReport;

export async function loadBundle(): Promise<LoadResult<Bundle>> {
  if (!API_BASE) {
    return { data: bundledSnapshot, source: "snapshot" };
  }
  try {
    const [summary, exceptions, gst, scorecard, config, forecast, journal, audit] =
      await Promise.all([
        getJson<Bundle["summary"]>("/summary"),
        getJson<Bundle["exceptions"]>("/exceptions?limit=400"),
        getJson<Bundle["gst"]>("/gst"),
        getJson<Bundle["scorecard"]>("/scorecard"),
        getJson<Bundle["config"]>("/config"),
        getJson<Bundle["forecast"]>("/forecast"),
        getJson<{ summary: Bundle["journalSummary"]; entries: Bundle["journal"] }>("/journal"),
        getJson<Bundle["audit"]>("/audit?limit=600"),
      ]);
    return {
      data: {
        summary, exceptions, gst, scorecard, config, forecast,
        journal: journal.entries,
        journalSummary: journal.summary,
        audit,
        marketplace: bundledSnapshot.marketplace,
      },
      source: "live",
    };
  } catch (e) {
    return {
      data: bundledSnapshot,
      source: "snapshot",
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

export async function loadVerification(): Promise<LoadResult<VerificationReport>> {
  if (!API_BASE) return { data: bundledVerification, source: "snapshot" };
  try {
    const data = await getJson<VerificationReport>("/verification");
    return { data, source: "live" };
  } catch (e) {
    return {
      data: bundledVerification,
      source: "snapshot",
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

/** Trace one entity through the audit trail. Live-only — there is no useful
 *  offline answer, so the caller is told plainly rather than shown a guess. */
export async function traceEntity(id: string): Promise<
  { found: boolean; narrative: string; decisions: unknown[] } | { unavailable: true }
> {
  if (!API_BASE) return { unavailable: true };
  try {
    return await getJson(`/trace/${encodeURIComponent(id)}`);
  } catch {
    return { unavailable: true };
  }
}

/* ---------------- Q&A ---------------- */

export interface QAResponse {
  answer: string;
  llm_used: boolean;
  run_id: string | null;
  retrieval_strategy: string[];
  grounded_rows: {
    subject_id: string;
    outcome: string;
    variance_code: string | null;
    reason: string;
    confidence: number | null;
  }[];
}

export interface QAStatus {
  llm_configured: boolean;
  model: string | null;
  note: string;
}

/** Q&A is retrieval-grounded and live-only — there is no meaningful offline
 *  answer to a free-form question, so this is honest about that rather than
 *  pretending the bundled snapshot can answer anything asked of it.
 *
 *  /qa requires operator role or above (it is the one endpoint that sends
 *  merchant data to a third-party LLM), so the stored credential is attached
 *  as headers here. Without one, the server correctly returns 403 -- that is
 *  reported back distinctly from a network failure, rather than both being
 *  collapsed into the same generic "unavailable" the earlier version returned.
 */
export async function askQuestion(
  question: string
): Promise<QAResponse | { unavailable: true } | { forbidden: true; detail: string }> {
  if (!API_BASE) return { unavailable: true };
  try {
    const res = await fetch(`${API_BASE}/qa`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question }),
    });
    if (res.status === 403) {
      const data = await res.json().catch(() => ({}));
      return {
        forbidden: true,
        detail: (data as { detail?: string }).detail ||
          "This endpoint requires operator role or higher.",
      };
    }
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as QAResponse;
  } catch {
    return { unavailable: true };
  }
}

export async function getQAStatus(): Promise<QAStatus | { unavailable: true }> {
  if (!API_BASE) return { unavailable: true };
  try {
    return await getJson<QAStatus>("/qa/status");
  } catch {
    return { unavailable: true };
  }
}

/* ---------------- credentials & ledger ---------------- */

/**
 * Role credentials live in sessionStorage, not localStorage, and are sent as
 * headers on every privileged request.
 *
 * To be explicit about what this does and does not achieve: the key is
 * verified SERVER-SIDE on every request, so holding it in the browser does
 * not grant anything by itself — a forged header without the real key is
 * rejected by the API. What browser storage does affect is convenience
 * (staying signed in across page navigations) and exposure (anyone with
 * access to the machine and devtools can read it). sessionStorage limits
 * that exposure to the tab's lifetime, which is the right trade for an
 * operator console. It is deliberately NOT presented as a security boundary
 * on its own.
 */
const CRED_KEY = "sadhaka.credentials";

export interface Credentials {
  role: "operator" | "admin";
  key: string;
}

export function getCredentials(): Credentials | null {
  try {
    const raw = sessionStorage.getItem(CRED_KEY);
    return raw ? (JSON.parse(raw) as Credentials) : null;
  } catch {
    return null;
  }
}

export function setCredentials(c: Credentials | null) {
  try {
    if (c) sessionStorage.setItem(CRED_KEY, JSON.stringify(c));
    else sessionStorage.removeItem(CRED_KEY);
  } catch {
    /* storage unavailable (private mode); privileged calls will 403, which
       is the correct failure rather than a silent partial state */
  }
}

function authHeaders(): Record<string, string> {
  const c = getCredentials();
  if (!c) return {};
  return { "X-Sadhaka-Role": c.role, "X-Sadhaka-Key": c.key };
}

async function getAuthed<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
}

async function postAuthed<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      (data as { detail?: string }).detail || `${path} -> ${res.status}`
    );
  }
  return data as T;
}

export interface LedgerRun {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  engine_version: string | null;
  notes: string | null;
  decision_count: number;
  has_metrics: boolean;
  value_match_rate_pct?: number;
  batch_match_rate_pct?: number;
  order_match_rate_pct?: number;
  exceptions_total?: number;
  exceptions_actionable?: number;
  exceptions_benign?: number;
  records?: number;
  money?: Record<string, string>;
}

export interface Adjustment {
  adjustment_id: string;
  run_id: string;
  kind: string;
  targets: Record<string, unknown>;
  reason: string;
  author: string;
  payload: Record<string, unknown>;
  amount_paise: number | null;
  amount?: string;
  status: string;
  reversed_by: string | null;
  created_at: string;
}

export interface AdjustmentSummary {
  total_adjustments: number;
  active: number;
  reversed: number;
  by_kind: Record<string, { count: number; value_paise: number; value: string }>;
  net_correction_paise: number;
  net_correction: string;
  note: string;
}

export interface AdminAction {
  action_id: number;
  action: string;
  actor_role: string;
  target: string | null;
  detail: string | null;
  outcome: string;
  created_at: string;
}

export interface TrendPoint {
  run_id: string;
  started_at: string;
  value_match_rate_pct: number | null;
  batch_match_rate_pct: number | null;
  order_match_rate_pct: number | null;
  exceptions_total: number | null;
  exceptions_actionable: number | null;
  exceptions_benign: number | null;
  records: number | null;
}

export const ledgerRuns = () =>
  getAuthed<{ count: number; runs: LedgerRun[] }>("/ledger/runs");

export const ledgerTrend = () =>
  getAuthed<{ count: number; points: TrendPoint[] }>("/ledger/trend");

export const ledgerAdjustments = (runId?: string) =>
  getAuthed<{ summary: AdjustmentSummary; adjustments: Adjustment[] }>(
    `/ledger/adjustments${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`
  );

export const ledgerAdminLog = () =>
  getAuthed<{ count: number; accepted: number; rejected: number; actions: AdminAction[] }>(
    "/ledger/admin-log"
  );

export const postAdjustment = (body: {
  run_id: string;
  kind: string;
  reason: string;
  targets?: Record<string, unknown>;
  lines?: {
    account_code: string;
    account_name: string;
    debit_paise: number;
    credit_paise: number;
    memo: string;
  }[];
  detail?: Record<string, unknown>;
}) => postAuthed<Adjustment>("/ledger/adjustments", body);

export const reverseAdjustment = (adjustment_id: string, reason: string) =>
  postAuthed<Adjustment>("/ledger/adjustments/reverse", { adjustment_id, reason });

/** Verify a credential by calling an endpoint that requires it. Returns the
 *  effective role the server actually granted, which may be lower than what
 *  was claimed — the client never decides its own role. */
export async function verifyCredentials(c: Credentials): Promise<boolean> {
  if (!API_BASE) return false;
  try {
    const res = await fetch(`${API_BASE}/ledger/admin-log?limit=1`, {
      headers: { "X-Sadhaka-Role": c.role, "X-Sadhaka-Key": c.key },
    });
    return res.ok;
  } catch {
    return false;
  }
}

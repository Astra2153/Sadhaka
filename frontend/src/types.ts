/**
 * Shapes returned by the Sadhaka API.
 *
 * These mirror the FastAPI response models. Amounts ending in `_paise` are
 * integers in the smallest currency unit, matching Razorpay's own convention —
 * never floats, because binary floating point cannot represent 0.01 exactly and
 * a reconciliation engine that drifts by a paise per row is worthless.
 * Fields without the suffix are pre-formatted display strings from the server.
 */

export interface MatchRates {
  batch_match_rate_pct: number;
  batch_match_denominator: string;
  order_match_rate_pct: number;
  order_match_denominator: string;
  value_match_rate_pct: number;
  value_match_denominator: string;
  bank_value_match_rate_pct: number;
  bank_value_denominator: string;
}

export interface Throughput {
  bank_credits: number;
  settlement_batches: number;
  recon_rows: number;
  settled_transactions: number;
  orders: number;
  total_records_processed: number;
}

export interface Metrics {
  throughput: Throughput;
  match_rates: MatchRates;
  exceptions: {
    total: number;
    actionable: number;
    benign: number;
    actionable_value: string;
    benign_value: string;
    unresolved_settlements: number;
  };
  confidence_distribution: Record<string, number>;
  money: Record<string, string>;
}

export interface Summary {
  run_id: string;
  metrics: Metrics;
  exception_summary?: unknown;
}

export interface Decision {
  decision_id: number;
  stage: string;
  subject_type: string;
  subject_id: string;
  counterpart_type: string | null;
  counterpart_id: string | null;
  outcome: string;
  variance_code: string | null;
  confidence: number;
  rule_fired: string;
  amount_subject: number | null;
  amount_counterpart: number | null;
  variance_paise: number | null;
  reason: string;
  evidence: Record<string, unknown>;
  amount_subject_display?: string | null;
  variance_display?: string | null;
  is_benign?: boolean;
  code_meaning?: string;
  created_at?: string;
}

export interface ExceptionsResponse {
  run_id: string;
  count: number;
  actionable: number;
  benign: number;
  exceptions: Decision[];
}

export interface AuditResponse {
  run_id: string;
  total: number;
  returned: number;
  offset: number;
  decisions: Decision[];
}

export interface GstInvoice {
  invoice_no: string;
  period: string;
  supplier: string;
  supplier_gstin: string;
  taxable_value: number;
  invoice_tax: number;
  settlement_tax: number;
  tax_difference: number;
  within_tolerance: boolean;
  reflected_in_gstr2b: boolean;
  itc_claimable: number;
  itc_blockers: string[];
}

export interface InstrumentBreakdown {
  count: number;
  gross: number;
  fee: number;
  tax: number;
  effective_mdr_pct: number;
  effective_gst_pct: number;
  contracted_mdr_pct: number;
  statutory_note: string;
}

export interface GstReport {
  run_id: string;
  settled_fee_total: number;
  settled_tax_total: number;
  expected_tax_on_fees: number;
  gst_understated: number;
  total_itc_claimable: number;
  total_itc_blocked: number;
  invoices: GstInvoice[];
  by_instrument: Record<string, InstrumentBreakdown>;
  display: Record<string, string>;
}

export interface ForecastDay {
  date: string;
  weekday: string;
  expected_paise: number;
  expected: string;
  late_case_paise: number;
  cumulative_paise: number;
  cumulative: string;
  item_count: number;
  sources: string[];
}

export interface LagSummary {
  observations: number;
  median_days: number;
  p90_days: number;
  stdev_days: number;
  min_days?: number;
  max_days?: number;
  source: string;
}

export interface Forecast {
  run_id?: string;
  as_of: string;
  horizon_days: number;
  behaviour: {
    settlement_lag: LagSummary;
    credit_lag: LagSummary;
    contracted_cycle_days: number;
    drift_note: string | null;
  };
  confidence_band: "tight" | "moderate" | "wide";
  confidence_reason: string;
  expected_total_paise: number;
  expected_total: string;
  late_case_total: string;
  inflight_count: number;
  inflight_net: string;
  awaiting_credit_count: number;
  awaiting_credit: string;
  timeline: ForecastDay[];
  at_risk: { category: string; amount: string; amount_paise: number; count: number; note: string }[];
  inflight_detail: Record<string, unknown>[];
  awaiting_detail: Record<string, unknown>[];
}

export interface JournalLine {
  account_code: string;
  account_name: string;
  account_type: string;
  debit_paise: number;
  credit_paise: number;
  debit: string;
  credit: string;
  memo: string;
}

export interface JournalEntry {
  entry_id: string;
  date: string;
  narration: string;
  source_ref: string;
  category: string;
  balanced: boolean;
  total_debit_paise: number;
  total_credit_paise: number;
  total_debit: string;
  total_credit: string;
  lines: JournalLine[];
}

export interface TrialBalanceRow {
  account_code: string;
  account_name: string;
  account_type: string;
  debit_paise: number;
  credit_paise: number;
  debit: string;
  credit: string;
  net_paise: number;
  net: string;
}

export interface JournalSummary {
  entries_generated: number;
  entries_balanced: number;
  entries_unbalanced: number;
  trial_balance: TrialBalanceRow[];
  trial_debit_total: string;
  trial_credit_total: string;
  trial_balances: boolean;
  gst_recoverable: string;
  gateway_cost: string;
}

export interface Scorecard {
  run_id?: string;
  planted_faults: number;
  detected: number;
  recall_pct: number;
  code_accuracy_pct: number;
  planted_traps: number;
  traps_passed: number;
  trap_pass_pct: number;
  cases: {
    id: string;
    type: string;
    expected_code: string;
    detected: boolean;
    correct_code: boolean;
    detail: string;
    description: string;
  }[];
  traps: {
    id: string;
    type: string;
    kind: string;
    criterion: string;
    passed: boolean;
    detail: string;
    description: string;
  }[];
}

export interface AppConfig {
  merchant: Record<string, string>;
  mdr_rates: Record<string, number>;
  gst_on_mdr_pct: number;
  tolerances: Record<string, string | number>;
  auto_accept_threshold: number;
  statutory_notes: Record<string, string>;
}

/* ---------------- verification ---------------- */

export interface DetectionLevel {
  magnitude: number;
  magnitude_display: string;
  trials: number;
  detected: number;
  detection_rate: number;
  ci_low: number;
  ci_high: number;
  correct_code_rate: number;
}

export interface DetectionSweep {
  fault_type: string;
  label: string;
  unit: string;
  levels: DetectionLevel[];
  lod50: number | null;
  lod95: number | null;
  lod50_display: string | null;
  lod95_display: string | null;
  verdict: "floor_established" | "blind_spot" | "underpowered" | "reliable" | "unreliable";
  underpowered: boolean;
  trials_per_level: number;
  aggregate_rate: number;
  aggregate_trials: number;
  aggregate_ci: [number, number];
  statement: string;
}

export interface CalibrationBin {
  range: string;
  lo: number;
  hi: number;
  count: number;
  claimed_confidence: number;
  observed_accuracy: number;
  gap: number;
  direction: "overconfident" | "underconfident" | "calibrated";
}

export interface Calibration {
  samples: number;
  bins: CalibrationBin[];
  ece: number;
  brier_score: number;
  overall_accuracy: number;
  mean_confidence: number;
  verdict: string;
}

export interface Counterfactual {
  subject_id: string;
  variance_code: string | null;
  original_reason: string;
  counterfactual: {
    available: boolean;
    narrative: string;
    actionable: boolean;
    changes?: { field: string; current: string; required: string; delta?: string; text: string }[];
    likely_cause?: string;
    cause_explanation?: string;
    nearest_counterpart?: string;
    gap?: string;
  };
}

export interface VerificationReport {
  profile: string;
  seed: number;
  elapsed_seconds: number;
  total_attack_trials: number;
  calibration_samples: number;
  baseline: {
    exceptions_on_clean_data: number;
    entities_flagged: number;
    matches: number;
    note: string;
  };
  detection_limits: DetectionSweep[];
  calibration: Calibration;
  counterfactuals: Counterfactual[];
  blind_spots: { fault_type: string; label: string; statement: string }[];
  underpowered?: { fault_type: string; label: string; statement: string }[];
  headline: string;
}

export interface Marketplace {
  as_of: string;
  payout_count: number;
  seller_count: number;
  gross: string;
  commission: string;
  tds_194o_deducted: string;
  tds_194o_in_26as: string;
  tds_not_yet_visible: string;
  tcs_52_collected: string;
  tcs_52_in_gstr8: string;
  net_paid: string;
  tds_matched: number;
  tds_pending_within_tolerance: number;
  tds_overdue: number;
  rates_applied: Record<string, number | string>;
  payouts: Record<string, string | number>[];
}

export interface Bundle {
  summary: Summary;
  exceptions: ExceptionsResponse;
  gst: GstReport;
  scorecard: Scorecard;
  config: AppConfig;
  forecast: Forecast;
  journal: JournalEntry[];
  journalSummary: JournalSummary;
  audit: AuditResponse;
  marketplace: Marketplace;
}

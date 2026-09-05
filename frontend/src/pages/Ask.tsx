import { useEffect, useRef, useState } from "react";
import { PageHead, Section, Badge, Finding } from "@/components/ui";
import {
  askQuestion, getQAStatus, API_BASE, getCredentials, setCredentials, verifyCredentials,
  type QAResponse, type QAStatus, type Credentials,
} from "@/lib/api";

interface Turn {
  question: string;
  response?: QAResponse;
  unavailable?: boolean;
  forbidden?: string;
  loading?: boolean;
}

const SUGGESTIONS = [
  "What is the value match rate?",
  "Are there any chargebacks?",
  "Tell me about on-hold amounts",
  "What's the GST understated by?",
  "Why didn't order_2007 fully reconcile?",
];

export default function Ask() {
  const [status, setStatus] = useState<QAStatus | { unavailable: true } | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // /qa requires operator role or above -- sign-in lives on this page too so
  // asking a question does not require a detour through /admin first.
  const [creds, setCreds] = useState<Credentials | null>(getCredentials());
  const [keyInput, setKeyInput] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [signInError, setSignInError] = useState<string | null>(null);

  async function signIn() {
    setSigningIn(true);
    setSignInError(null);
    const candidate: Credentials = { role: "operator", key: keyInput.trim() };
    const ok = await verifyCredentials(candidate);
    setSigningIn(false);
    if (!ok) {
      setSignInError(
        "The server rejected that key. Check that SADHAKA_OPERATOR_KEY is " +
        "set on the backend and matches exactly."
      );
      return;
    }
    setCredentials(candidate);
    setCreds(candidate);
    setKeyInput("");
  }

  function signOut() {
    setCredentials(null);
    setCreds(null);
  }

  useEffect(() => {
    getQAStatus().then(setStatus);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function send(q: string) {
    const question = q.trim();
    if (!question) return;
    setInput("");
    setTurns((t) => [...t, { question, loading: true }]);

    const result = await askQuestion(question);

    setTurns((t) => {
      const copy = [...t];
      const idx = copy.length - 1;
      if ("unavailable" in result) {
        copy[idx] = { question, unavailable: true };
      } else if ("forbidden" in result) {
        copy[idx] = { question, forbidden: result.detail };
      } else {
        copy[idx] = { question, response: result };
      }
      return copy;
    });
  }

  const offline = !API_BASE;
  const llmOff = status && !("unavailable" in status) && !status.llm_configured;

  return (
    <>
      <PageHead
        title="Ask"
        lede="A question interface over the audit trail — grounded, not free-form."
        aside={
          status && !("unavailable" in status) ? (
            <Badge tone={status.llm_configured ? "credit" : "amber"}>
              {status.llm_configured ? `Gemini: ${status.model}` : "no LLM configured"}
            </Badge>
          ) : null
        }
      />

      <Section
        title="How this is different from a chatbot"
        note="The model never decides what happened to the money. Every question is first used to pull real rows from the audit trail — by entity id, by variance-code keyword, or the run's stored metrics — and only that retrieved data is shown to the model. It is instructed to answer from the context alone and never compute a new rupee figure. This is the same principle that keeps the matching engine itself rule-based rather than LLM-driven: a reconciliation answer has to trace to a specific row, not a plausible-sounding guess."
      >
        {offline && (
          <Finding title="Live backend required" tone="warn">
            This page needs a running API — it has no meaningful offline answer,
            unlike the rest of the site. Set <code className="bg-paper-sunk px-1.5 py-px">VITE_API_BASE_URL</code>{" "}
            and start <code className="bg-paper-sunk px-1.5 py-px">uvicorn api.main:app --port 8000</code>.
          </Finding>
        )}
        {!offline && llmOff && (
          <Finding title="Answering from retrieved data directly" tone="note">
            No <code className="bg-paper-sunk px-1.5 py-px">GEMINI_API_KEY</code> is set on
            the backend, so questions still work but return the retrieved audit-trail
            rows in a plain template rather than an LLM-phrased answer. Set the key
            in <code className="bg-paper-sunk px-1.5 py-px">Backend/.env</code> to enable
            natural-language phrasing.
          </Finding>
        )}
      </Section>

      {!creds ? (
        <Section
          title="Sign in to ask questions"
          note="This endpoint sends retrieved data to a third-party LLM, so on a public deployment it is a credentialed action rather than something any visitor can trigger. The key is verified by the server on every request."
        >
          <div className="max-w-[420px] space-y-3">
            <input
              className="field w-full"
              type="password"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") signIn(); }}
              placeholder="Operator key (SADHAKA_OPERATOR_KEY)"
              disabled={offline}
            />
            <button className="chip" onClick={signIn} disabled={offline || signingIn || !keyInput.trim()}>
              {signingIn ? "Verifying with server…" : "Sign in"}
            </button>
            {signInError && (
              <p className="max-w-[52ch] text-[12.5px] leading-[1.5] text-debit">{signInError}</p>
            )}
          </div>
        </Section>
      ) : (
      <Section
        title="Conversation"
        note={
          <span>
            Signed in as operator. <button className="text-indigo underline" onClick={signOut}>sign out</button>
          </span>
        }
      >
        <div className="mb-5 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" onClick={() => send(s)} disabled={offline}>
              {s}
            </button>
          ))}
        </div>

        <div className="mb-5 max-h-[560px] space-y-5 overflow-y-auto border-y border-rule py-5">
          {turns.length === 0 ? (
            <p className="text-[13.5px] italic text-ink-soft">
              Ask a question, or pick a suggestion above.
            </p>
          ) : (
            turns.map((t, i) => (
              <div key={i}>
                <div className="mb-2 font-serif text-[15px] font-medium">{t.question}</div>
                {t.loading && <div className="text-[13px] text-ink-soft">Retrieving…</div>}
                {t.unavailable && (
                  <div className="text-[13px] text-amber">
                    Could not reach the backend for this question.
                  </div>
                )}
                {t.forbidden && (
                  <div className="text-[13px] text-debit">{t.forbidden}</div>
                )}
                {t.response && (
                  <div className="border-l-2 border-indigo pl-4">
                    <p className="whitespace-pre-wrap text-[14px] leading-[1.6]">
                      {t.response.answer}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <Badge tone={t.response.llm_used ? "indigo" : "neutral"}>
                        {t.response.llm_used ? "Gemini, grounded" : "direct from audit trail"}
                      </Badge>
                      <span className="text-[11.5px] text-ink-soft">
                        {t.response.retrieval_strategy.join("; ")}
                      </span>
                    </div>
                    {t.response.grounded_rows.length > 0 && (
                      <details className="mt-2.5">
                        <summary className="cursor-pointer text-[12.5px] text-ink-soft">
                          {t.response.grounded_rows.length} source row(s) this answer was grounded in
                        </summary>
                        <ul className="mt-2 space-y-1.5">
                          {t.response.grounded_rows.map((r, j) => (
                            <li key={j} className="text-[12px] text-ink-soft">
                              <span className="font-medium text-ink">{r.subject_id}</span>
                              {r.variance_code && <> · {r.variance_code}</>} — {r.reason}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <div className="flex gap-2">
          <input
            className="field flex-1"
            placeholder={offline ? "Backend not connected" : "Ask about this reconciliation run…"}
            value={input}
            disabled={offline}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") send(input); }}
          />
          <button className="chip" onClick={() => send(input)} disabled={offline}>
            Ask
          </button>
        </div>
      </Section>
      )}
    </>
  );
}

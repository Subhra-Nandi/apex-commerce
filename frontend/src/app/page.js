"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  Bot,
  Boxes,
  CheckCircle2,
  CreditCard,
  Gauge,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wallet,
  XCircle,
  Zap,
} from "lucide-react";
import { API_BASE, api, inr, rupees } from "@/lib/api";

/* ------------------------------------------------------------------ atoms */

const TONE = {
  emerald: {
    text: "text-emerald-400",
    bg: "bg-emerald-500/10",
    ring: "ring-emerald-500/25",
    dot: "bg-emerald-400",
    bar: "bg-emerald-500",
  },
  cyan: {
    text: "text-cyan-400",
    bg: "bg-cyan-500/10",
    ring: "ring-cyan-500/25",
    dot: "bg-cyan-400",
    bar: "bg-cyan-500",
  },
  amber: {
    text: "text-amber-400",
    bg: "bg-amber-500/10",
    ring: "ring-amber-500/25",
    dot: "bg-amber-400",
    bar: "bg-amber-500",
  },
  rose: {
    text: "text-rose-400",
    bg: "bg-rose-500/10",
    ring: "ring-rose-500/25",
    dot: "bg-rose-400",
    bar: "bg-rose-500",
  },
  zinc: {
    text: "text-zinc-400",
    bg: "bg-zinc-800/60",
    ring: "ring-zinc-700",
    dot: "bg-zinc-500",
    bar: "bg-zinc-600",
  },
};

const STATUS_TONE = {
  paid: "emerald",
  awaiting_approval: "amber",
  pending_payment: "cyan",
  recovered: "amber",
  failed: "rose",
  pending: "zinc",
};

function Pill({ children, tone = "zinc", icon: Icon, pulse = false }) {
  const t = TONE[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-medium ring-1 ring-inset ${t.bg} ${t.text} ${t.ring}`}
    >
      {Icon ? (
        <Icon size={12} strokeWidth={2.25} />
      ) : (
        <span
          className={`h-1.5 w-1.5 rounded-full ${t.dot} ${pulse ? "pulse-dot" : ""}`}
        />
      )}
      {typeof children === "string" ? children.replace(/_/g, " ") : children}
    </span>
  );
}

function Card({ title, icon: Icon, right, children, className = "" }) {
  return (
    <section
      className={`rounded-xl border border-zinc-800 bg-zinc-900/80 shadow-card backdrop-blur ${className}`}
    >
      {title ? (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-5 py-3.5">
          <h2 className="flex items-center gap-2 text-[13.5px] font-semibold tracking-tight text-zinc-100">
            {Icon ? <Icon size={15} className="text-zinc-500" /> : null}
            {title}
          </h2>
          {right}
        </header>
      ) : null}
      <div className="p-5">{children}</div>
    </section>
  );
}

function Button({ children, onClick, disabled, variant = "ghost", icon: Icon }) {
  const variants = {
    primary:
      "bg-emerald-500 text-emerald-950 shadow-glow hover:bg-emerald-400 font-semibold",
    accent:
      "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/35 hover:bg-amber-500/25",
    ghost:
      "bg-zinc-800/70 text-zinc-200 ring-1 ring-inset ring-zinc-700 hover:bg-zinc-800 hover:text-white",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-[13px] transition-all disabled:cursor-not-allowed disabled:opacity-40 ${variants[variant]}`}
    >
      {Icon ? <Icon size={14} strokeWidth={2.25} /> : null}
      {children}
    </button>
  );
}

function StatCard({ icon: Icon, label, value, sub, children }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/80 p-4 shadow-card">
      <div className="flex items-center gap-2 text-[11.5px] font-medium uppercase tracking-wider text-zinc-500">
        <Icon size={13} />
        {label}
      </div>
      <p className="num mt-2 text-[22px] font-semibold tracking-tight text-zinc-50">
        {value}
        {sub ? (
          <span className="ml-1.5 text-[13px] font-normal text-zinc-500">
            {sub}
          </span>
        ) : null}
      </p>
      {children}
    </div>
  );
}

function Banner({ tone, icon: Icon, title, children, action }) {
  const t = TONE[tone];
  return (
    <div
      className={`flex flex-wrap items-center gap-3 rounded-xl px-4 py-3 ring-1 ring-inset ${t.bg} ${t.ring}`}
    >
      <Icon size={16} className={`${t.text} shrink-0`} />
      <div className="min-w-0 flex-1">
        {title ? (
          <p className={`text-[13px] font-semibold ${t.text}`}>{title}</p>
        ) : null}
        <p className="text-[12.5px] leading-relaxed text-zinc-400">{children}</p>
      </div>
      {action}
    </div>
  );
}

function Json({ label, value }) {
  if (value === null || value === undefined) return null;
  return (
    <details className="mt-2.5 overflow-hidden rounded-lg border border-zinc-800">
      <summary className="px-3 py-2 font-mono text-[11px] uppercase tracking-wider text-zinc-500 hover:bg-zinc-800/50 hover:text-zinc-300">
        {label}
      </summary>
      <pre className="max-h-72 overflow-auto border-t border-zinc-800 bg-zinc-950/70 p-3 font-mono text-[11px] leading-relaxed text-zinc-400">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

/* ------------------------------------------------------- the floor gauge */

/**
 * One order line drawn as a price scale.
 *   zinc bar      = what the merchant paid
 *   emerald band  = the margin policy protects
 *   emerald tick  = the floor; no price may land left of it
 *   rose hairline = where the agent wanted to land, if it was overruled
 *   pill          = the price that will actually be charged
 * When the enclave overrides a price the pill slides into the tick and stops.
 */
function FloorGauge({ cost, floor, requested, final, adjusted }) {
  const ceiling = Math.max(cost, floor, requested, final) * 1.2 || 1;
  const pct = (v) => Math.max(0, Math.min(100, ((v || 0) / ceiling) * 100));
  const [markerAt, setMarkerAt] = useState(
    adjusted ? pct(requested) : pct(final)
  );

  useEffect(() => {
    const timer = setTimeout(() => setMarkerAt(pct(final)), 90);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cost, floor, requested, final, adjusted]);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3.5 py-3">
      <div className="relative h-6">
        <div className="absolute inset-x-0 top-2.5 h-1.5 rounded-full bg-zinc-800" />
        <div
          className="absolute top-2.5 h-1.5 rounded-l-full bg-zinc-700"
          style={{ left: 0, width: `${pct(cost)}%` }}
        />
        <div
          className="absolute top-2.5 h-1.5 bg-emerald-500/35"
          style={{
            left: `${pct(cost)}%`,
            width: `${Math.max(0, pct(floor) - pct(cost))}%`,
          }}
        />
        <div
          className="absolute top-0.5 h-5 w-[3px] -translate-x-1/2 rounded-full bg-emerald-400"
          style={{ left: `${pct(floor)}%` }}
          title={`Margin floor ${inr(floor)}`}
        />
        {adjusted ? (
          <div
            className="absolute top-1 h-4 w-px -translate-x-1/2 bg-rose-500/70"
            style={{ left: `${pct(requested)}%` }}
            title={`Agent proposed ${inr(requested)}`}
          />
        ) : null}
        <div
          className={`gauge-marker absolute top-0 h-6 w-2 -translate-x-1/2 rounded-full ring-2 ring-zinc-950 ${
            adjusted ? "bg-amber-400" : "bg-zinc-100"
          }`}
          style={{ left: `${markerAt}%` }}
          title={`Charged ${inr(final)}`}
        />
      </div>
      <div className="num mt-1 flex justify-between text-[11px]">
        <span className="text-zinc-500">cost {inr(cost)}</span>
        <span className="font-medium text-emerald-400">floor {inr(floor)}</span>
        <span
          className={
            adjusted ? "font-semibold text-amber-400" : "font-medium text-zinc-200"
          }
        >
          charged {inr(final)}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------- audit feed step */

function Step({ index, icon: Icon, title, tone, subtitle, children, last }) {
  const t = TONE[tone];
  return (
    <li className="relative flex gap-4">
      {!last ? (
        <span className="absolute left-[15px] top-9 bottom-0 w-px bg-zinc-800" />
      ) : null}
      <span
        className={`relative z-10 grid h-8 w-8 shrink-0 place-items-center rounded-lg ring-1 ring-inset ${t.bg} ${t.ring} ${t.text}`}
      >
        <Icon size={15} strokeWidth={2.25} />
      </span>
      <div className="min-w-0 flex-1 pb-6">
        <div className="flex flex-wrap items-center gap-2">
          <span className="num text-[11px] font-medium text-zinc-600">
            {String(index).padStart(2, "0")}
          </span>
          <h3 className="text-[13.5px] font-semibold text-zinc-100">{title}</h3>
          {subtitle}
        </div>
        <div className="mt-2">{children}</div>
      </div>
    </li>
  );
}

/* --------------------------------------------------------------- the page */

const PRESETS = [
  {
    label: "keyboard + hub",
    request:
      "Set me up with a mechanical keyboard and a USB-C hub for my ApexBook laptop.",
    budget: 7000,
  },
  {
    label: "hub + mouse",
    request: "I need a USB-C hub and a quiet wireless mouse for my desk.",
    budget: 4000,
  },
  { label: "laptop", request: "I want to buy the ApexBook 14 Pro laptop.", budget: 60000 },
];

export default function Home() {
  const [health, setHealth] = useState(null);
  const [summary, setSummary] = useState(null);
  const [orders, setOrders] = useState([]);
  const [products, setProducts] = useState([]);
  const [llm, setLlm] = useState(null);

  const [agentId, setAgentId] = useState("agent-buyer-01");
  const [request, setRequest] = useState(PRESETS[0].request);
  const [budget, setBudget] = useState(7000);

  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [s, o, p] = await Promise.all([
        api.summary(),
        api.orders(),
        api.products(),
      ]);
      setSummary(s);
      setOrders(o.orders || []);
      setProducts(p.products || []);
      setHealth(true);
    } catch {
      setHealth(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    api
      .llmStatus()
      .then(setLlm)
      .catch(() => setLlm(null));
  }, [refresh]);

  const loadOrder = useCallback(async (id) => {
    try {
      const data = await api.order(id);
      setSelected(data?.error ? null : data);
    } catch {
      setSelected(null);
    }
  }, []);

  const run = async (mode) => {
    setBusy(mode);
    setError("");
    setFlash(null);
    const body = { agent_id: agentId, request, budget_inr: Number(budget) || 0 };
    try {
      let next;
      if (mode === "negotiate") {
        const d = await api.negotiate(body);
        next = { mode, ai: d.ai_proposal, intent: d.proposed_intent, primary: null, recovery: null, note: d.note };
      } else if (mode === "buy") {
        const d = await api.purchase(body);
        next = { mode, ai: d.ai_proposal, intent: d.proposed_intent, primary: d.enclave_result, recovery: null };
      } else {
        const d = await api.purchaseResilient(body);
        next = { mode, ai: d.ai_proposal, intent: d.proposed_intent, primary: d.attempt, recovery: d.recovery };
      }
      setResult(next);
      setHealth(true);
      await refresh();
      if (next.primary?.order_id) await loadOrder(next.primary.order_id);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(null);
    }
  };

  const slip = async (key, work, describe) => {
    setBusy(key);
    setFlash(null);
    try {
      const data = await work();
      setFlash({ ok: !data?.error, text: data?.error || describe(data) });
      await refresh();
    } catch (caught) {
      setFlash({ ok: false, text: caught.message });
    } finally {
      setBusy(null);
    }
  };

  const mandate = summary?.mandate;
  const spent = mandate?.spent_today_inr ?? 0;
  const dailyCap = mandate?.daily_cap_inr ?? 0;
  const spentPct = dailyCap > 0 ? Math.min(100, (spent / dailyCap) * 100) : 0;

  const decision = result?.primary?.decision || null;
  const stepUpLink =
    result?.primary?.requires_step_up && decision?.approved
      ? result.primary.pay_here
      : null;

  const input =
    "w-full rounded-lg border border-zinc-800 bg-zinc-950/70 px-3.5 py-2.5 text-[13.5px] text-zinc-100 transition-colors placeholder:text-zinc-600 focus:border-emerald-500/60 focus:outline-none";

  return (
    <div className="aurora min-h-screen">
      <div className="grid-field min-h-screen">
        {/* ---------------------------------------------------------- header */}
        <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-xl">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-4">
            <div className="flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-lg bg-emerald-500/12 ring-1 ring-inset ring-emerald-500/30">
                <ShieldCheck size={17} className="text-emerald-400" />
              </span>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-[15px] font-bold tracking-[0.12em] text-zinc-50">
                    APEX-COMMERCE
                  </h1>
                  <span className="rounded-full bg-emerald-500/12 px-2 py-0.5 text-[10.5px] font-medium tracking-wide text-emerald-400 shadow-[0_0_16px_-2px_rgba(16,185,129,0.55)] ring-1 ring-inset ring-emerald-500/30">
                    Agentic Middleware
                  </span>
                </div>
                <p className="mt-0.5 text-[12px] text-zinc-500">
                  An AI proposes the price. A deterministic enclave decides
                  whether it may be charged.
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Pill tone="cyan" icon={Sparkles}>
                {llm?.primary?.model || "Gemini 2.5 Flash"}
              </Pill>
              <Pill tone={llm?.fallback?.configured ? "amber" : "zinc"} icon={RefreshCw}>
                Fallback {llm?.fallback?.free_models_discovered?.length ?? 0}
              </Pill>
              <Pill
                tone={health === false ? "rose" : health ? "emerald" : "zinc"}
                pulse={health !== false}
              >
                {health === false
                  ? "Backend offline"
                  : health
                    ? "Backend live"
                    : "Connecting…"}
              </Pill>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-7xl space-y-5 px-5 py-6">
          {health === false ? (
            <Banner
              tone="rose"
              icon={AlertCircle}
              title="Cannot reach the policy enclave"
              action={
                <Button icon={RefreshCw} onClick={refresh}>
                  Retry
                </Button>
              }
            >
              Nothing on this page is live. Start the API at{" "}
              <span className="font-mono text-zinc-300">{API_BASE}</span> with{" "}
              <span className="font-mono text-zinc-300">
                uvicorn app.main:app --reload
              </span>{" "}
              from the backend folder.
            </Banner>
          ) : null}

          {stepUpLink ? (
            <Banner
              tone="amber"
              icon={Lock}
              title="Step-up approval required"
              action={
                <a
                  href={stepUpLink}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-2.5 text-[13px] font-semibold text-amber-950 transition-colors hover:bg-amber-400"
                >
                  Approve &amp; pay <ArrowUpRight size={14} />
                </a>
              }
            >
              This order clears{" "}
              {rupees(summary?.step_up_threshold_inr ?? 2000)}, so the agent was
              stopped and a pre-validated Razorpay payment link was issued
              instead. A human decides from here. Test UPI:{" "}
              <span className="font-mono text-zinc-300">success@razorpay</span>
            </Banner>
          ) : null}

          {/* --------------------------------------------------- metrics */}
          <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              icon={Wallet}
              label="Per Tx limit"
              value={rupees(mandate?.per_transaction_cap_inr ?? 0)}
            />
            <StatCard
              icon={Activity}
              label="Spent today"
              value={rupees(spent)}
              sub={`/ ${rupees(dailyCap)}`}
            >
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-cyan-500 transition-[width] duration-700"
                  style={{ width: `${spentPct}%` }}
                />
              </div>
            </StatCard>
            <StatCard
              icon={Lock}
              label="Step-up threshold"
              value={rupees(summary?.step_up_threshold_inr ?? 0)}
            >
              <div className="mt-3">
                <Pill tone="amber">Requires human approval</Pill>
              </div>
            </StatCard>
            <StatCard
              icon={Boxes}
              label="Orders executed"
              value={summary?.order_count ?? 0}
            />
          </div>

          {/* ----------------------------------------------- two columns */}
          <div className="grid gap-5 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
            {/* LEFT — command center */}
            <div className="space-y-5 lg:sticky lg:top-24 lg:self-start">
              <Card title="Buyer Agent Command Center" icon={Bot}>
                <div className="space-y-4">
                  <label className="block">
                    <span className="mb-1.5 block text-[11.5px] font-medium uppercase tracking-wider text-zinc-500">
                      Agent persona ID
                    </span>
                    <input
                      value={agentId}
                      onChange={(e) => setAgentId(e.target.value)}
                      className={`${input} font-mono text-[13px]`}
                    />
                  </label>

                  <label className="block">
                    <span className="mb-1.5 block text-[11.5px] font-medium uppercase tracking-wider text-zinc-500">
                      Natural language request
                    </span>
                    <textarea
                      rows={4}
                      value={request}
                      onChange={(e) => setRequest(e.target.value)}
                      className={`${input} resize-none leading-relaxed`}
                    />
                  </label>

                  <label className="block">
                    <span className="mb-1.5 block text-[11.5px] font-medium uppercase tracking-wider text-zinc-500">
                      Budget (₹)
                    </span>
                    <input
                      type="number"
                      min={0}
                      value={budget}
                      onChange={(e) => setBudget(e.target.value)}
                      className={`${input} num`}
                    />
                  </label>

                  <div className="flex flex-wrap gap-2">
                    {PRESETS.map((p) => (
                      <button
                        key={p.label}
                        type="button"
                        onClick={() => {
                          setRequest(p.request);
                          setBudget(p.budget);
                        }}
                        className="rounded-full border border-zinc-800 bg-zinc-950/60 px-3 py-1.5 text-[12px] text-zinc-400 transition-colors hover:border-emerald-500/40 hover:bg-emerald-500/10 hover:text-emerald-300"
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>

                  <div className="grid gap-2.5 border-t border-zinc-800 pt-4">
                    <Button
                      onClick={() => run("negotiate")}
                      disabled={Boolean(busy)}
                      icon={Sparkles}
                    >
                      {busy === "negotiate" ? "Negotiating…" : "Negotiate only"}
                    </Button>
                    <Button
                      variant="primary"
                      onClick={() => run("buy")}
                      disabled={Boolean(busy)}
                      icon={Zap}
                    >
                      {busy === "buy" ? "Executing…" : "Buy now"}
                    </Button>
                    <Button
                      variant="accent"
                      onClick={() => run("recover")}
                      disabled={Boolean(busy)}
                      icon={RefreshCw}
                    >
                      {busy === "recover"
                        ? "Recovering…"
                        : "Buy with auto-recovery"}
                    </Button>
                  </div>

                  {error ? (
                    <Banner tone="rose" icon={XCircle}>
                      {error}
                    </Banner>
                  ) : null}
                </div>
              </Card>

              <Card title="Inject Slippage" icon={AlertCircle}>
                <p className="text-[12.5px] leading-relaxed text-zinc-500">
                  Move the world after the agent has quoted, then buy with
                  auto-recovery.
                </p>
                <div className="mt-3.5 flex flex-wrap gap-2">
                  <Button
                    disabled={Boolean(busy)}
                    onClick={() =>
                      slip("stock", () => api.setStock("KBD-MECH-01", 0), () => "Keyboard stock is now zero.")
                    }
                  >
                    Sell out keyboard
                  </Button>
                  <Button
                    disabled={Boolean(busy)}
                    onClick={() =>
                      slip("cost", () => api.setCost("MOU-WL-01", 1500), () => "Mouse cost raised to ₹1,500 — floor is now ₹1,680.")
                    }
                  >
                    Raise mouse cost
                  </Button>
                  <Button
                    disabled={Boolean(busy)}
                    onClick={() =>
                      slip("offers", () => api.offers(), (d) => `Razorpay offers: ${d.count}. Configured: ${d.configured_offer_id || "none"}.`)
                    }
                  >
                    Check offers
                  </Button>
                  <Button
                    variant="primary"
                    disabled={Boolean(busy)}
                    onClick={() =>
                      slip("reset", () => api.resetSlippage(), (d) => `Restored ${d.count} products.`)
                    }
                    icon={RefreshCw}
                  >
                    Restore catalog
                  </Button>
                </div>
                {flash ? (
                  <div className="mt-3.5">
                    <Banner
                      tone={flash.ok ? "emerald" : "rose"}
                      icon={flash.ok ? CheckCircle2 : XCircle}
                    >
                      {flash.text}
                    </Banner>
                  </div>
                ) : null}
              </Card>
            </div>

            {/* RIGHT — audit stream */}
            <Card
              title="Policy Enclave · Real-Time Audit Stream"
              icon={Gauge}
              right={
                result?.primary ? (
                  <Pill tone={STATUS_TONE[result.primary.status] || "zinc"}>
                    {result.primary.status}
                  </Pill>
                ) : (
                  <Pill tone="zinc">Idle</Pill>
                )
              }
            >
              {!result ? (
                <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-800 px-6 py-20 text-center">
                  <span className="grid h-11 w-11 place-items-center rounded-xl bg-zinc-800/60 ring-1 ring-inset ring-zinc-700">
                    <Bot size={19} className="text-zinc-500" />
                  </span>
                  <p className="mt-4 text-[13.5px] font-medium text-zinc-300">
                    No transaction yet
                  </p>
                  <p className="mt-1 max-w-sm text-[12.5px] leading-relaxed text-zinc-500">
                    Send the agent shopping. Every stage it passes through — the
                    model&apos;s proposal, the enclave&apos;s verdict, the
                    Razorpay call, any recovery — streams in here.
                  </p>
                </div>
              ) : (
                <ol>
                  {/* 1 — agent proposal */}
                  <Step
                    index={1}
                    icon={Bot}
                    tone="cyan"
                    title="Agent intent & proposal"
                    subtitle={
                      <Pill tone="cyan">
                        {result.ai?.served_by?.[0] || "llm"}
                      </Pill>
                    }
                  >
                    {result.ai?.front_agent?.reasoning ? (
                      <p className="rounded-lg border-l-2 border-cyan-500/50 bg-cyan-500/[0.06] px-3.5 py-2.5 text-[13px] leading-relaxed text-zinc-300">
                        {result.ai.front_agent.reasoning}
                      </p>
                    ) : (
                      <p className="text-[13px] text-zinc-500">
                        No reasoning returned.
                      </p>
                    )}
                    {result.ai?.negotiator?.offer_note ? (
                      <p className="mt-2 text-[12.5px] leading-relaxed text-zinc-500">
                        {result.ai.negotiator.offer_note}
                      </p>
                    ) : null}
                    <Json label="proposed intent" value={result.intent} />
                  </Step>

                  {/* 2 — enclave */}
                  <Step
                    index={2}
                    icon={ShieldCheck}
                    tone={decision ? (decision.approved ? "emerald" : "rose") : "zinc"}
                    title="Deterministic policy enclave"
                    subtitle={
                      <>
                        <Pill tone={decision ? (decision.approved ? "emerald" : "rose") : "zinc"}>
                          {decision ? (decision.approved ? "Approved" : "Rejected") : "Not evaluated"}
                        </Pill>
                        <Pill tone="emerald" icon={Lock}>
                          Model never saw cost prices
                        </Pill>
                      </>
                    }
                  >
                    {decision ? (
                      <>
                        <p className="num mb-3 text-[24px] font-semibold tracking-tight text-zinc-50">
                          {rupees(result.primary.subtotal_inr)}
                        </p>
                        <div className="space-y-3">
                          {decision.lines.map((line) => (
                            <div key={line.sku}>
                              <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                                <p className="text-[12.5px]">
                                  <span className="font-mono font-medium text-zinc-200">
                                    {line.sku}
                                  </span>
                                  <span className="text-zinc-600"> × {line.quantity}</span>
                                </p>
                                <div className="flex items-center gap-2">
                                  <Pill tone={line.price_adjusted ? "amber" : "zinc"}>
                                    {line.price_adjusted ? "Raised to floor" : "Price accepted"}
                                  </Pill>
                                  <span className="num text-[13px] font-semibold text-zinc-100">
                                    {inr(line.line_total_paise)}
                                  </span>
                                </div>
                              </div>
                              <FloorGauge
                                cost={line.unit_cost_paise}
                                floor={line.floor_unit_price_paise}
                                requested={line.requested_unit_price_paise}
                                final={line.final_unit_price_paise}
                                adjusted={line.price_adjusted}
                              />
                            </div>
                          ))}
                        </div>
                        {!decision.approved ? (
                          <div className="mt-3 space-y-2">
                            {decision.rejection_reasons.map((reason, i) => (
                              <Banner key={i} tone="rose" icon={XCircle}>
                                {reason}
                              </Banner>
                            ))}
                          </div>
                        ) : null}
                      </>
                    ) : (
                      <p className="text-[13px] text-zinc-500">
                        {result.note ||
                          "Negotiation only — the enclave was not asked to authorise anything."}
                      </p>
                    )}
                  </Step>

                  {/* 3 — razorpay */}
                  <Step
                    index={3}
                    icon={CreditCard}
                    tone={
                      result.primary?.razorpay_order_id
                        ? "emerald"
                        : result.primary?.razorpay_payment_link_id
                          ? "amber"
                          : "zinc"
                    }
                    title="Razorpay execution"
                    subtitle={
                      <Pill
                        tone={
                          result.primary?.razorpay_order_id
                            ? "emerald"
                            : result.primary?.razorpay_payment_link_id
                              ? "amber"
                              : "zinc"
                        }
                      >
                        {result.primary?.razorpay_order_id
                          ? "Order created"
                          : result.primary?.razorpay_payment_link_id
                            ? "Payment link issued"
                            : "Never called"}
                      </Pill>
                    }
                  >
                    {result.primary?.razorpay_order_id ||
                    result.primary?.razorpay_payment_link_id ? (
                      <p className="font-mono text-[12px] text-zinc-400">
                        {result.primary.razorpay_order_id ||
                          result.primary.razorpay_payment_link_id}
                      </p>
                    ) : (
                      <p className="text-[13px] text-zinc-500">
                        The enclave blocked this before any money API was
                        touched. That is the design working, not a bug.
                      </p>
                    )}
                    {result.primary?.pay_here ? (
                      <a
                        href={result.primary.pay_here}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2.5 text-[13px] font-semibold text-emerald-950 shadow-glow transition-colors hover:bg-emerald-400"
                      >
                        Open checkout <ArrowUpRight size={14} />
                      </a>
                    ) : null}
                  </Step>

                  {/* 4 — recovery */}
                  <Step
                    index={4}
                    icon={RefreshCw}
                    last
                    tone={
                      !result.recovery?.attempted
                        ? "zinc"
                        : result.recovery.succeeded
                          ? "emerald"
                          : "rose"
                    }
                    title="Auto-recovery pipeline"
                    subtitle={
                      <Pill
                        tone={
                          !result.recovery?.attempted
                            ? "zinc"
                            : result.recovery.succeeded
                              ? "emerald"
                              : "rose"
                        }
                      >
                        {!result.recovery
                          ? "Not requested"
                          : !result.recovery.attempted
                            ? "Not needed"
                            : result.recovery.succeeded
                              ? "Counter-offer issued"
                              : "No offer possible"}
                      </Pill>
                    }
                  >
                    {!result.recovery ? (
                      <p className="text-[13px] text-zinc-500">
                        Run “Buy with auto-recovery” to arm this stage.
                      </p>
                    ) : (
                      <>
                        <p className="text-[13px] leading-relaxed text-zinc-300">
                          {result.recovery.reason}
                        </p>
                        {result.recovery.actions?.length ? (
                          <ol className="mt-3 space-y-2">
                            {result.recovery.actions.map((a, i) => (
                              <li
                                key={i}
                                className="flex gap-3 rounded-lg border border-zinc-800 bg-zinc-950/60 px-3.5 py-2.5"
                              >
                                <span className="num grid h-5 w-5 shrink-0 place-items-center rounded-full bg-zinc-800 text-[10.5px] font-semibold text-zinc-400">
                                  {i + 1}
                                </span>
                                <div>
                                  <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-600">
                                    {a.step.replace(/_/g, " ")}
                                  </p>
                                  <p className="mt-0.5 text-[13px] leading-relaxed text-zinc-300">
                                    {a.detail}
                                  </p>
                                </div>
                              </li>
                            ))}
                          </ol>
                        ) : null}
                        {result.recovery.counter_offer ? (
                          <div className="mt-3 rounded-xl bg-emerald-500/[0.07] p-4 ring-1 ring-inset ring-emerald-500/25">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p className="text-[12px] font-semibold text-emerald-400">
                                Counter-offer · order{" "}
                                {result.recovery.counter_offer.order_id}
                              </p>
                              <Pill
                                tone={
                                  STATUS_TONE[result.recovery.counter_offer.status] ||
                                  "zinc"
                                }
                              >
                                {result.recovery.counter_offer.status}
                              </Pill>
                            </div>
                            <p className="num mt-1.5 text-[22px] font-semibold tracking-tight text-zinc-50">
                              {rupees(result.recovery.counter_offer.subtotal_inr)}
                            </p>
                            {result.recovery.accessory_discount_applied ? (
                              <p className="mt-1.5 text-[12.5px] leading-relaxed text-emerald-300/80">
                                Accessories repriced to their protected floors —
                                the deepest discount policy allows, and not one
                                paisa lower.
                              </p>
                            ) : null}
                            {result.recovery.counter_offer.pay_here ? (
                              <a
                                href={result.recovery.counter_offer.pay_here}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2.5 text-[13px] font-semibold text-emerald-950 transition-colors hover:bg-emerald-400"
                              >
                                Pay counter-offer <ArrowUpRight size={14} />
                              </a>
                            ) : null}
                          </div>
                        ) : null}
                      </>
                    )}
                    <Json label="full api response" value={result} />
                  </Step>
                </ol>
              )}
            </Card>
          </div>

          {/* ------------------------------------------- ledger + catalog */}
          <div className="grid gap-5 lg:grid-cols-2">
            <Card
              title="Six-stage audit ledger"
              icon={Activity}
              right={
                selected ? (
                  <Pill tone={STATUS_TONE[selected.status] || "zinc"}>
                    order {selected.id}
                  </Pill>
                ) : null
              }
            >
              {!selected ? (
                <p className="rounded-lg border border-dashed border-zinc-800 px-4 py-10 text-center text-[13px] text-zinc-500">
                  Pick an order below to replay its immutable trail.
                </p>
              ) : (
                <ol className="space-y-3">
                  {[1, 2, 3, 4, 5, 6].map((stage) => {
                    const events = (selected.trail || []).filter(
                      (e) => e.stage_index === stage
                    );
                    const hit = events.length > 0;
                    return (
                      <li key={stage} className="flex gap-3">
                        <span
                          className={`num grid h-6 w-6 shrink-0 place-items-center rounded-md text-[11px] font-semibold ring-1 ring-inset ${
                            hit
                              ? "bg-emerald-500/12 text-emerald-400 ring-emerald-500/30"
                              : "bg-zinc-900 text-zinc-700 ring-zinc-800"
                          }`}
                        >
                          {stage}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p
                            className={`text-[11px] font-medium uppercase tracking-wider ${
                              hit ? "text-zinc-500" : "text-zinc-700"
                            }`}
                          >
                            {selected.stage_names?.[stage] || `Stage ${stage}`}
                          </p>
                          {hit ? (
                            events.map((e, i) => (
                              <div key={e.id ?? i} className={i ? "mt-2" : ""}>
                                <p className="text-[13px] leading-relaxed text-zinc-300">
                                  {e.message}
                                </p>
                                <Json label="payload" value={e.payload} />
                              </div>
                            ))
                          ) : (
                            <p className="text-[13px] text-zinc-700">
                              Not reached.
                            </p>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )}
              <p className="mt-4 border-t border-zinc-800 pt-3.5 text-[12px] text-zinc-600">
                The log only appends. A stage listed twice is a rejection and the
                rescue that followed it, not a duplicate.
              </p>
            </Card>

            <div className="space-y-5">
              <Card title="Orders" icon={Boxes} right={<Pill tone="zinc">{orders.length}</Pill>}>
                {!orders.length ? (
                  <p className="rounded-lg border border-dashed border-zinc-800 px-4 py-8 text-center text-[13px] text-zinc-500">
                    No orders yet.
                  </p>
                ) : (
                  <div className="-mx-5 max-h-72 overflow-auto">
                    <table className="w-full text-left">
                      <thead className="sticky top-0 bg-zinc-900/95 backdrop-blur">
                        <tr className="text-[11px] uppercase tracking-wider text-zinc-600">
                          <th className="px-5 py-2 font-medium">ID</th>
                          <th className="px-5 py-2 font-medium">Status</th>
                          <th className="px-5 py-2 text-right font-medium">Total</th>
                          <th className="px-5 py-2 font-medium">Items</th>
                        </tr>
                      </thead>
                      <tbody>
                        {orders.map((o) => (
                          <tr
                            key={o.id}
                            onClick={() => loadOrder(o.id)}
                            className={`cursor-pointer border-t border-zinc-800/70 text-[12.5px] transition-colors hover:bg-zinc-800/40 ${
                              selected?.id === o.id ? "bg-zinc-800/50" : ""
                            }`}
                          >
                            <td className="num px-5 py-2.5 font-mono text-zinc-300">
                              {o.id}
                            </td>
                            <td className="px-5 py-2.5">
                              <Pill tone={STATUS_TONE[o.status] || "zinc"}>
                                {o.status}
                              </Pill>
                            </td>
                            <td className="num px-5 py-2.5 text-right text-zinc-200">
                              {rupees(o.subtotal_inr)}
                            </td>
                            <td className="px-5 py-2.5 font-mono text-[11px] text-zinc-500">
                              {(o.items || []).map((i) => i.sku).join(", ") || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card
                title="Merchant catalog"
                icon={Lock}
                right={<Pill tone="emerald">Cost &amp; floor hidden from the LLM</Pill>}
              >
                {!products.length ? (
                  <p className="rounded-lg border border-dashed border-zinc-800 px-4 py-8 text-center text-[13px] text-zinc-500">
                    Nothing seeded. Run{" "}
                    <span className="font-mono text-zinc-400">
                      python -m app.catalog.seed_data
                    </span>
                    .
                  </p>
                ) : (
                  <div className="-mx-5 overflow-x-auto">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="text-[11px] uppercase tracking-wider text-zinc-600">
                          <th className="px-5 py-2 font-medium">SKU</th>
                          <th className="px-5 py-2 text-right font-medium">Cost</th>
                          <th className="px-5 py-2 text-right font-medium text-emerald-500">
                            Floor
                          </th>
                          <th className="px-5 py-2 text-right font-medium">List</th>
                          <th className="px-5 py-2 text-right font-medium">Stock</th>
                        </tr>
                      </thead>
                      <tbody>
                        {products.map((p) => (
                          <tr
                            key={p.sku}
                            className="border-t border-zinc-800/70 text-[12.5px]"
                          >
                            <td className="px-5 py-2.5 font-mono text-zinc-300">
                              {p.sku}
                            </td>
                            <td className="num px-5 py-2.5 text-right text-zinc-500">
                              {rupees(p.cost_inr)}
                            </td>
                            <td className="num px-5 py-2.5 text-right font-medium text-emerald-400">
                              {rupees(p.floor_inr)}
                            </td>
                            <td className="num px-5 py-2.5 text-right text-zinc-200">
                              {rupees(p.list_inr)}
                            </td>
                            <td
                              className={`num px-5 py-2.5 text-right ${
                                p.stock_quantity === 0
                                  ? "text-rose-400"
                                  : "text-zinc-400"
                              }`}
                            >
                              {p.stock_quantity}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
          </div>

          <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-zinc-800 pb-8 pt-5 text-[11.5px] text-zinc-600">
            <span>{summary?.merchant?.name || "Merchant"}</span>
            <span className="text-zinc-800">·</span>
            <span>money held as integer paise</span>
            <span className="text-zinc-800">·</span>
            <span>audit log appends only</span>
            <span className="text-zinc-800">·</span>
            <span className="font-mono">{API_BASE}</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
# APEX-Commerce

**Agentic Policy & Negotiated Execution Exchange**

An agent-readable middleware and policy-gated negotiation gateway that lets
external buyer agents (like ChatGPT or Claude) discover product catalogs,
negotiate margin-aware bundles, and execute purchases through Razorpay's
test APIs — all under strict, deterministic, non-LLM spending limits and a
full audit trail.

> Built for the **Razorpay Hackathon — AI Growth & Agentic Commerce** track.

---

## Why this project exists

AI agents are starting to shop on behalf of humans. That's convenient — and
dangerous, because a language model that can move money is a liability. APEX-Commerce
solves this with a simple principle:

> **The AI proposes. Deterministic code disposes.**

The LLM only ever produces a structured *intent* (a JSON proposal). A separate,
predictable, non-AI Python module — the **Policy Enclave** — validates every
proposal against hard spending caps, price-floor rules, and idempotency checks
*before* a single rupee moves.

---

## Core safety guarantees

- **Deterministic Policy Enclave** — the LLM never calls Razorpay directly.
- **Price Floor Guard** — `Negotiated_Price >= Cost_Price x (1 + Min_Margin_%)`.
  Any lower offer is automatically overridden to the floor.
- **Step-Up Human Approval** — orders above Rs.2,000 require human confirmation
  via a Razorpay Payment Link.
- **6-Stage Audit Trail** — every transaction is logged immutably:
  Trigger -> Agent Reasoning -> Policy Evaluation -> Razorpay Payload ->
  Webhook Verification -> Final State.
- **Graceful Failure Recovery** — on mid-checkout stock/price slippage, the
  system rejects the broken order and auto-negotiates a counter-offer bundle.

---

## Tech stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| Backend API      | Python 3.12 + FastAPI                             |
| Agent AI         | Google Gemini 2.5 Flash (via Google AI Studio)    |
| Database         | Neon PostgreSQL (free tier) + SQLAlchemy          |
| Cache / State    | Upstash Redis (free tier)                         |
| Payments         | Razorpay Test Mode APIs                           |
| Frontend         | Next.js 15 + Tailwind CSS + Shadcn UI (planned)   |
| Local webhooks   | ngrok                                             |

---

## Project structure

```text
apex-commerce/
├── backend/
│   ├── app/
│   │   ├── database/
│   │   │   ├── models.py       # SQLAlchemy tables
│   │   │   ├── session.py      # DB engine + connection
│   │   │   └── init_db.py      # Creates tables
│   │   ├── catalog/            # (Phase 2)
│   │   ├── payments/           # (Phase 3)
│   │   ├── enclave/            # (Phase 4) Deterministic policy rules
│   │   └── agents/             # (Phase 5) Gemini agents
│   ├── .env.example
│   ├── .gitignore
│   └── requirements.txt
└── frontend/  # (Phase 7)
```
---

## Local setup

1. **Clone and enter the backend**
```powershell
   git clone https://github.com/Subhra-Nandi/apex-commerce.git
   cd apex-commerce/backend
```

2. **Create and activate a virtual environment (Windows PowerShell)**
```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
```

3. **Install dependencies**
```powershell
   pip install -r requirements.txt
```

4. **Configure secrets** — copy the template and fill in your own keys:
```powershell
   Copy-Item .env.example .env
```
   You'll need: Razorpay test keys, a Gemini API key, and a Neon PostgreSQL URL.

5. **Create the database tables**
```powershell
   python -m app.database.init_db
```

---

## Build roadmap

- [ x ] **Phase 1** — Environment setup & database schema
- [ x ] **Phase 2** — Agent Commerce Interface (ACI) catalog engine
- [ x ] **Phase 3** — Razorpay SDK & webhook listener
- [ x ] **Phase 4** — Deterministic Policy Enclave
- [  ] **Phase 5** — Multi-agent intelligence engine (Gemini)
- [ ] **Phase 6** — Graceful failure & auto-recovery
- [ ] **Phase 7** — Next.js dashboard & live demo

---

## License

This project is for hackathon/demonstration purposes.
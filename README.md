# Revora — AI Revenue Recovery Agent

Revora is an AI-assisted revenue recovery system built for **Razorpay's AI Revenue Recovery track**.

It detects failed payments that may represent recoverable revenue, uses AI to diagnose the likely recovery path, verifies the AI recommendation against deterministic evidence, applies explicit recovery policies, and executes only bounded simulated recovery actions.

The key principle is simple:

> **AI recommends. Deterministic controls authorize. The executor stays bounded and auditable.**

---

## 🎯 Problem

Payment failures create revenue that is at risk, but not every failed payment should be retried automatically.

A recovery system needs to answer:

1. Which failed payments are worth recovering?
2. Why did the payment fail?
3. What intervention should be attempted?
4. Is there enough evidence to trust that recommendation?
5. Is the action allowed by policy?
6. When should the system stop and escalate to manual review?
7. How much revenue was actually recovered?

Revora is designed around this complete recovery workflow rather than simply predicting which payments might recover.

---

## 💡 Solution

Revora combines a deterministic recovery baseline with an AI-assisted recovery layer.

### Recovery flow

```text
Failed Payments
      │
      ▼
┌──────────────────────┐
│ Detect Opportunities │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   AI Diagnosis       │
│ failure + action     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Evidence Verification│
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Policy Authorization │
│ confidence / retries │
│ action / amount limit │
└──────────┬───────────┘
           │
       ┌───┴────────────┐
       │                │
       ▼                ▼
   Execute          Manual Review
       │
       ▼
 Simulated Recovery
       │
       ▼
 Audit Trail + Metrics
```

---

## 🤖 Where AI Is Used

Revora uses AI for the **diagnosis and recommendation layer**.

For each AI candidate, the system produces a diagnosis containing information such as:

- likely failure interpretation
- recommended recovery action
- AI confidence
- reasoning used for the recommendation

The AI recommendation is then converted into a deterministic representation before it can influence execution.

### Important safety boundary

The AI model **does not directly execute a payment action**.

Instead:

```text
AI Recommendation
       ↓
Deterministic Evidence Check
       ↓
Deterministic Policy Check
       ↓
Bounded Executor
```

This keeps financial execution outside the direct control of the model.

---

## 🛡️ Safety & Control Layer

Revora is intentionally designed with multiple gates before an automated recovery can execute.

### Evidence verification

The AI diagnosis must pass an evidence check before the action can proceed.

### Confidence threshold

Low-confidence recommendations are rejected from automatic execution and routed to manual review.

Current policy threshold:

```text
Minimum evidence confidence: 0.80
```

### Retry limit

Automatic recovery is bounded by a maximum retry count.

Current policy:

```text
Maximum automatic retry count: 2
```

### Action allowlist

Only supported automated actions can be executed.

Currently:

```text
retry_payment
```

Other recommendations are routed to manual review.

### Amount limit

High-value transactions are not automatically recovered.

Current automatic amount limit:

```text
₹50,000
```

Transactions above this limit are escalated for manual review.

### Idempotency

The executor includes a batch-level idempotency guard so the same transaction cannot be executed twice within the same recovery batch.

### Manual escalation

When a policy gate fails, the system does not force execution.

Instead:

```text
Automatic Recovery → Blocked → Manual Review
```

---

## 🛑 Stopping Rules

Revora stops automatic recovery when important safety conditions are not satisfied.

Examples include:

- low confidence
- failed evidence verification
- unsupported recovery action
- retry limit reached
- transaction amount exceeds the automatic limit
- duplicate execution within the recovery batch

This prevents the system from optimizing for recovery at the expense of safe execution.

---

## 💰 Measured Recovery

Revora evaluates recovery over a simulated payment batch.

The dashboard measures:

- total payments
- failed payments
- ground-truth recoverable payments
- baseline recovery opportunities
- AI candidates
- AI-approved candidates
- successful AI recoveries
- false approvals
- revenue at risk
- baseline recovered revenue
- AI incremental recovered revenue
- combined recovered revenue
- recovery rate

### Example benchmark

A representative 1,000-payment benchmark can be displayed in the dashboard with metrics such as:

```text
1,000 payments
252 failed payments
₹9.4L+ failed-payment revenue at risk
AI-assisted incremental recovery
0 false approvals in the displayed run
```

The exact numbers can vary between benchmark runs because the recovery dataset is generated by the simulator.

**All revenue figures shown by Revora are simulated benchmark results, not real payment processing or real customer funds.**

---

## 📊 Model Quality

Revora also reports AI decision quality using:

### Precision

```text
Correct AI approvals / Total AI approvals
```

### Recall

```text
Successful AI recoveries / Ground-truth recoverable payments
```

### F1 score

```text
2 × Precision × Recall / (Precision + Recall)
```

The dashboard also exposes false approvals so that aggressive automation cannot be presented as success without measuring safety.

Because Revora uses a conservative AI candidate budget, recall may be intentionally lower than a system that attempts many more candidates.

---

## 🔍 Audit Trail

Every AI candidate can be represented through a transaction-level audit record.

The audit trail contains information including:

```text
Transaction ID
Merchant ID
Amount
Payment method
Failure code
Failure type
Retry count
AI recommended action
Evidence verdict
Evidence confidence
Policy decision
Policy reason
Rules checked
Execution attempt
Execution result
Amount recovered
Final status
```

This makes the recovery decision explainable rather than presenting the AI result as a black box.

---

## 🧪 Simulation

Revora uses a simulated payment environment.

The executor does **not** process real payments.

Instead, the simulator uses the generated payment dataset's recoverability state to evaluate whether a simulated retry would succeed.

Example:

```text
Payment failed
      ↓
AI recommends retry
      ↓
Evidence passes
      ↓
Policy passes
      ↓
Simulated retry
      ↓
Success / Failure
```

This allows the system to measure recovery performance safely without moving real money.

---

## 🏗️ Architecture

### Frontend

- React
- TypeScript
- Vite
- Dashboard-based recovery workspace

### Backend

- Python
- FastAPI
- Recovery simulation and decision pipeline

### Core recovery modules

```text
omnix/
└── recovery/
    ├── models.py
    ├── simulator.py
    ├── detector.py
    ├── diagnosis.py
    ├── ai_diagnosis.py
    ├── ai_candidate.py
    ├── evidence.py
    ├── policy.py
    ├── executor.py
    └── experiment.py
```

> The internal Python package remains named `omnix` for implementation compatibility. **Revora** is the user-facing product name.

---

## 🔄 API

### Recovery benchmark

```http
GET /api/recovery/ai
```

Runs the AI-assisted recovery benchmark and returns aggregate metrics together with transaction-level audit information.

### Baseline endpoint

```http
GET /api/recovery
```

Provides the deterministic baseline recovery metrics.

---

## 🖥️ Running Locally

### 1. Start the backend

From the project root:

```bash
cd OMNIX-main
```

Then:

```bash
.\.venv\Scripts\python.exe -m omnix.server
```

The FastAPI backend will start locally.

### 2. Start the frontend

Open another terminal:

```bash
cd frontend
```

Install dependencies if required:

```bash
npm install
```

Start the development server:

```bash
npm run dev
```

Then open:

```text
http://localhost:5173/
```

---

## 🎬 Demo Flow

A short demo can follow this sequence:

### 1. Open Revora

Show the Revenue Recovery workspace and explain the revenue-at-risk problem.

### 2. Show the benchmark

Point out:

- payment batch size
- failed payments
- revenue at risk
- baseline recovery
- AI incremental recovery

### 3. Show the AI decision pipeline

```text
Detect
  ↓
AI Diagnosis
  ↓
Evidence Verification
  ↓
Policy Authorization
  ↓
Execute
```

### 4. Show a transaction-level decision

Open the Recovery Decisions section and demonstrate:

- failure reason
- AI recommendation
- confidence
- policy decision
- recovery result

### 5. Show the audit trail

Explain that the system records the evidence, rules checked, authorization result, and execution result.

### 6. Show stopping rules

Demonstrate that unsafe or unsupported actions are escalated instead of automatically executed.

### 7. End with measured recovery

Compare baseline recovery against AI-assisted incremental recovery.

---

## 🔐 Design Principles

Revora follows five core principles:

### 1. AI recommends, not executes

The model cannot directly move money.

### 2. Evidence before action

An AI recommendation must be supported by deterministic checks.

### 3. Policy before execution

Even a high-confidence recommendation can be blocked by business or safety rules.

### 4. Bounded execution

Retries, transaction amounts, and supported actions are explicitly constrained.

### 5. Every decision is auditable

The system records why a recovery was attempted, blocked, or escalated.

---

## ⚠️ Current Limitations

Revora is a hackathon prototype and intentionally uses a simulated recovery environment.

Current limitations include:

- no real payment gateway execution
- no production payment credentials
- no persistent cross-request idempotency store
- simulated payment outcomes
- AI candidate evaluation is bounded for benchmark/runtime control
- manual review is represented by the system's decision state rather than a production operations queue

These boundaries are intentional for safe demonstration and evaluation.

---

## 🚀 Why This Fits AI Revenue Recovery

Revora addresses the complete revenue recovery loop:

```text
Detect revenue at risk
        ↓
Understand the failure
        ↓
Choose an intervention
        ↓
Verify the recommendation
        ↓
Authorize against policy
        ↓
Execute safely
        ↓
Measure recovered revenue
        ↓
Record an audit trail
```

The system therefore goes beyond simply predicting payment recovery.

It demonstrates how an AI agent can operate inside a **bounded financial workflow with measurable outcomes and explicit safety controls**.

---

## 🧰 Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript |
| Build tool | Vite |
| Backend | Python + FastAPI |
| AI layer | AI-assisted payment diagnosis |
| Recovery engine | Python simulation |
| Policy engine | Deterministic rule-based authorization |
| Evidence layer | Deterministic verification |
| Execution | Simulated bounded recovery |
| Visualization | React dashboard |

---

## 📁 Project Structure

```text
project/
├── frontend/
│   └── src/
│       └── workspace/
│           └── views/
│               └── Recovery.tsx
│
├── omnix/
│   ├── server.py
│   └── recovery/
│       ├── ai_candidate.py
│       ├── ai_diagnosis.py
│       ├── diagnosis.py
│       ├── detector.py
│       ├── evidence.py
│       ├── executor.py
│       ├── experiment.py
│       ├── models.py
│       ├── policy.py
│       └── simulator.py
│
└── README.md
```

---

## 📌 Hackathon Positioning

**Product:** Revora

**Track:** AI Revenue Recovery

**Category:** AI-assisted financial workflow automation

**Core capability:**

> Detect recoverable revenue, diagnose failed payments, apply evidence and policy gates, execute bounded recovery actions, and measure the resulting recovery.

---

## ⚖️ Disclaimer

Revora is a hackathon prototype using simulated payment data and simulated recovery execution.

It does not process real payments, access real customer funds, or execute live financial transactions.


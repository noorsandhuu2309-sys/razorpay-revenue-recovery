⚡ Revora — AI Revenue Recovery Agent

Turn failed payments into recoverable revenue — with AI diagnosis, deterministic safety gates, bounded execution, and a complete audit trail.

Revora is an AI-assisted revenue recovery platform built for Razorpay's AI Revenue Recovery track.

Instead of treating payment recovery as a simple prediction problem, Revora builds an end-to-end decision system:

Detect → Diagnose → Verify → Authorize → Recover → Measure → Audit

The central design principle is:

AI recommends. Deterministic controls authorize. The executor stays bounded and auditable.

🚀 Why Revora?

A failed payment does not automatically mean lost revenue.

Some failures are recoverable. Some require a different intervention. Some should never be retried automatically.

Revora answers the complete set of questions a real recovery operation needs to answer:

💸 What revenue is at risk?

🔎 Which failed payments are worth pursuing?

🧠 Why did the payment fail?

🎯 What recovery action is appropriate?

🧾 What evidence supports that recommendation?

🛡️ Is the action permitted by policy?

⛔ When should automation stop?

👤 When should a case go to manual review?

📈 How much revenue did the strategy actually recover?

🧪 Did AI improve on the deterministic baseline?

📝 Can every decision be explained afterwards?

That makes Revora an AI decision system for revenue recovery, not just an AI prediction model.

🧠 Product Overview

                    FAILED PAYMENTS
                           │
                           ▼
                ┌─────────────────────┐
                │ Opportunity Detector│
                │ revenue at risk     │
                └──────────┬──────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │   AI DIAGNOSIS      │
                │ failure + action    │
                │ confidence + reason │
                └──────────┬──────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │ EVIDENCE VERIFIER   │
                │ deterministic check │
                └──────────┬──────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │ POLICY AUTHORIZER   │
                │ confidence          │
                │ retry limits        │
                │ action allowlist    │
                │ amount limits       │
                └──────────┬──────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
                 EXECUTE      MANUAL REVIEW
                    │
                    ▼
            SIMULATED RECOVERY
                    │
                    ▼
          METRICS + AUDIT TRAIL

✨ Core Features

💰 1. Revenue-at-Risk Detection

Revora starts with failed-payment data and identifies opportunities that may be recoverable.

The recovery dashboard can expose:

total payment volume

failed payments

recoverable payment opportunities

revenue at risk

baseline recovery

AI candidate volume

approved candidates

successful recoveries

false approvals

incremental AI recovery

combined recovered revenue

recovery rate

This keeps the product focused on the business outcome:

How much revenue can we safely recover?

🤖 2. AI Payment Diagnosis

AI is used where it creates the most value: understanding the failure and recommending an intervention.

For an AI candidate, Revora can represent:

likely failure interpretation

recommended recovery action

AI confidence

reasoning behind the recommendation

The AI recommendation is then converted into a deterministic representation before it can influence execution.

The safety boundary

AI Recommendation
       ↓
Deterministic Evidence Check
       ↓
Deterministic Policy Check
       ↓
Bounded Executor

The AI model does not directly execute a payment action.

🛡️ 3. Evidence-First Recovery

A model recommendation is not enough.

Before automation can proceed, Revora checks whether the recommendation is supported by the available evidence.

This creates a clear separation between:

what the AI believes

and

what the system is authorized to do.

The product also exposes claims, sources, provenance and verification state as part of the wider intelligence workspace.

🔐 4. Deterministic Policy Engine

Even a high-confidence AI recommendation can be rejected.

Revora applies explicit policy gates including:

Guardrail

Current policy

Minimum evidence confidence

0.80

Maximum automatic retries

2

Automatic transaction amount limit

₹50,000

Automated action

retry_payment

A recommendation can therefore reach:

AI Approved
    ↓
Evidence Passed
    ↓
Policy Passed
    ↓
Execute

or:

AI Recommendation
    ↓
Gate Failed
    ↓
Blocked
    ↓
Manual Review

⛔ 5. Safe Stopping Rules

Revora deliberately knows when not to automate.

Automatic recovery stops when conditions such as these are encountered:

low confidence

failed evidence verification

unsupported recovery action

retry limit reached

transaction amount exceeds the automatic limit

duplicate execution in the recovery batch

This prevents the system from optimizing recovery rate at the expense of safe execution.

⚙️ 6. Bounded & Idempotent Execution

The executor is intentionally constrained.

It operates only on supported actions, within configured limits, and includes a batch-level idempotency guard so that the same transaction is not executed twice within a recovery batch.

For the hackathon environment, the execution layer is simulated rather than connected to live payment rails.

🧪 7. Deterministic Baseline + AI Benchmark

Revora does not simply show an AI number and call it success.

It compares the AI-assisted strategy against a deterministic baseline.

The benchmark can report:

baseline recovery opportunities

AI candidates

AI-approved candidates

successful AI recoveries

false approvals

baseline recovered revenue

AI incremental recovered revenue

combined recovered revenue

recovery rate

This creates an actual experiment loop:

Baseline
   ↓
AI Strategy
   ↓
Policy + Evidence Gates
   ↓
Simulated Outcomes
   ↓
Compare Recovery
   ↓
Measure Incremental Value

All payment and revenue results in the demo are simulated benchmark results.

📊 8. AI Quality Metrics

Revora measures decision quality instead of hiding behind a single recovery number.

Precision

Correct AI approvals
─────────────────────
Total AI approvals

Recall

Successful AI recoveries
─────────────────────────
Ground-truth recoverable payments

F1

2 × Precision × Recall
───────────────────────
Precision + Recall

False approvals are explicitly surfaced so aggressive automation cannot be presented as success without measuring safety.

🧾 9. Transaction-Level Audit Trail

Every recovery candidate can be represented through an auditable decision record.

Example fields include:

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

This makes each recovery decision explainable instead of turning the AI into a black box.

🗺️ 10. Decision Intelligence Graph

Revora's intelligence workspace goes beyond a traditional recovery table.

The Graph view represents objects and relationships as an interactive intelligence graph.

It supports three ways of reading the same subgraph:

ORBIT
What is attached to this object, and how?

NETWORK
What shape is all of this?

CLUSTERS
What groups exist here?

The graph pipeline applies:

Payload
  ↓
Trust Lens
  ↓
Canvas Filters
  ↓
Render Model
  ↓
Interactive Graph

Filters are applied before the simulation/render layout is built, so excluded objects do not continue influencing the visual structure.

Graph capabilities

interactive object selection

shared selection across workspace views

Network view

Cluster/community view

Orbit exploration

focus mode

focus trail / breadcrumbs

path tracing

fullscreen graph

graph search / find

filters

recency filtering

evidence-only filtering

tracked-only filtering

minimum-strength filtering

hidden object families

hidden relationship classes

graph legend

relationship certainty key

object counts

relationship counts

hidden-object counts

empty/error states

connected-object/no-link state

🌐 11. ReVora Intelligence HUD

The upgraded graph experience adds a dense recovery decision-map layer around the real graph.

The visual language communicates the recovery pipeline:

PAYMENT
   →
DIAGNOSIS
   →
EVIDENCE
   →
POLICY
   →
OUTCOME

The graph HUD can surface live, model-derived telemetry such as:

object count

relationship count

community count

graph density

selected-object count

highest-connectivity object

object-family distribution

focus state

path-trace state

trust-calibrated state

live/filter/sync state

The visual system uses:

radar/grid overlays

decision lanes

signal bars

state indicators

compact telemetry cards

relationship/decision labels

responsive layouts

Important: the HUD visualizes the current graph model; it does not fabricate relationships.

🔎 12. Research & Evidence Workspace

Revora's wider intelligence workspace provides multiple lenses over the same underlying material.

Research spine

Ask / NOVA — conversational research

Claims — assertions and their evidence

Sources — source-backed research material

Graph — connected intelligence

Brief — what changed while you were away

The data model supports:

claims

confidence

supporting sources

contradicting sources

provenance

events

tracked objects

workspaces

graph relationships

communities

💬 13. Persistent NOVA Conversation

The NOVA conversation is tied to the workspace/Space rather than being an isolated chat.

Conversation turns can carry:

role

text

intent

model

execution ID

context

timestamp

This allows research context to survive reloads and remain connected to the same workspace.

🧠 14. Challenge / Idea Stress Testing

Revora also includes a Challenge surface for stress-testing an idea before committing to research.

A challenge can expose:

research questions

answered questions

panel size

vendors / viewpoints

stances

assumptions

counterarguments

evidence state

model-opinion disclaimers

The goal is to make the system useful before and after the recovery decision is made.

📑 15. Outputs & Generated Artifacts

The workspace includes an Outputs surface for documents generated from the intelligence already collected.

Supported output structures can include:

text sections

lists

tables

metrics

charts

Generated outputs are represented as artifacts rather than being treated as raw chat responses.

🧭 16. Intents — Persistent Monitoring

Revora supports standing intents around information the user cares about.

An intent can maintain:

creation time

last checked time

last hit time

hit count

recent hits

monitoring state

This turns one-off research into a repeatable intelligence workflow.

🤖 17. Agents & Execution Monitoring

The Agents surface exposes background agent work as observable execution rather than a hidden process.

Agent execution can expose:

worker status

execution mode

progress

current step

completed steps

duration

model usage

input/output token counts

estimated token state

cost

errors

controllability

pause state

pending redirects

generated artifacts

source count

execution trail

This makes AI execution measurable and inspectable.

🗺️ 18. TERRA Intelligence Surfaces

The wider workspace also contains the TERRA intelligence subsystem.

Available surfaces include:

🌍 World Map

📰 News

🔗 Relationships

📊 Analysis

🧠 Intel

🚨 Situation

💬 Ask

🤖 Terra Agents

The World Map is designed around countries, risk, places, routes and conditions.

The geospatial surface can operate with its own layers, search, routes, conditions, memory and geofences.

🧬 19. HELIX

HELIX provides a separate bioinformatics-oriented corpus and grounded answer layer within the same workspace architecture.

This demonstrates that the underlying intelligence framework can support multiple specialized domains rather than being hard-coded around a single dashboard.

🧰 Workspace Experience

Revora is structured as a real intelligence workspace rather than a single screen.

The shell provides shared context across views, including:

Spaces / workspaces

persistent selection

inspector

action bar

breadcrumbs

trust lens

activity panel

NOVA bar

command palette

appearance controls

view-level error boundaries

responsive navigation

fullscreen surfaces where appropriate

A view failure is contained so one broken surface does not have to take down the entire workspace.

🏗️ Architecture

┌────────────────────────────────────────────────────────────┐
│                    REVORA WORKSPACE                        │
│                                                            │
│  Research     Graph      Recovery       Agents    Outputs  │
│                                                            │
└────────────────────────────┬───────────────────────────────┘
                             │
                             ▼
                    FastAPI Application
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
     Intelligence       Recovery Engine      Simulation
          │                  │                  │
          ▼                  ▼                  ▼
     Claims/Sources     Diagnosis/Policy     Outcomes
          │                  │                  │
          └──────────────────┼──────────────────┘
                             ▼
                    Audit + Metrics Layer

🔄 Recovery Architecture

The recovery engine is intentionally modular:

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

The internal Python package remains named omnix for implementation compatibility.

Revora is the user-facing product name.

🖥️ Frontend Architecture

The frontend is built as a shared workspace shell with specialized views.

frontend/
└── src/
    ├── components/
    │   ├── ActionBar
    │   ├── Inspector
    │   ├── NovaBar
    │   ├── CommandPalette
    │   ├── Breadcrumbs
    │   ├── TrustLens
    │   └── ActivityPanel
    │
    ├── lib/
    │   ├── graphModel
    │   ├── views
    │   ├── auth
    │   └── appearance
    │
    ├── store/
    │   ├── workspace
    │   └── graphUi
    │
    └── views/
        ├── Recovery
        ├── GraphView
        ├── Home
        ├── Ask
        ├── Challenge
        ├── Compare
        ├── Timeline
        ├── Table
        ├── Brief
        ├── Claims
        ├── Sources
        ├── Outputs
        ├── Intents
        ├── Agents
        ├── Map
        ├── News
        ├── Relationships
        ├── Analysis
        ├── Intel
        ├── Situation
        ├── TerraAgents
        ├── Helix
        └── Settings

🔌 API

Recovery benchmark

GET /api/recovery/ai

Runs the AI-assisted recovery benchmark and returns aggregate recovery metrics together with transaction-level audit information.

Deterministic baseline

GET /api/recovery

Provides deterministic baseline recovery metrics.

Graph statistics

GET /api/graph/stats

Provides graph-level counts and distributions.

Summary

GET /api/summary

Combines graph statistics with evidence-layer counts such as claims and sources.

🧪 Simulation Environment

Revora uses a simulated payment environment.

The executor does not process real payments.

Instead, the simulator uses the generated payment dataset's recoverability state to determine whether a simulated retry succeeds.

Payment Failed
      ↓
AI Recommends Retry
      ↓
Evidence Passes
      ↓
Policy Passes
      ↓
Bounded Executor
      ↓
Simulated Retry
      ↓
Success / Failure
      ↓
Metrics + Audit

This gives the system measurable outcomes without moving real money.

📈 Example Benchmark

A representative dashboard run can show metrics such as:

1,000 payments
252 failed payments
₹9.4L+ revenue at risk
AI-assisted incremental recovery
0 false approvals in the displayed run

The exact numbers may vary between benchmark runs because the payment dataset is generated by the simulator.

All revenue figures shown by Revora are simulated benchmark results, not real payment processing or real customer funds.

🎬 Suggested Hackathon Demo

A strong demo can be structured as a short story instead of a feature tour.

01 — Start with the problem

“A failed payment is not necessarily lost revenue.”

Show the recovery dashboard and revenue at risk.

02 — Show the AI decision

Open a candidate and show:

failure reason

AI diagnosis

recommendation

confidence

03 — Prove the safety boundary

Show:

AI
 ↓
Evidence
 ↓
Policy
 ↓
Executor

Explain that the model cannot directly execute the payment action.

04 — Show a blocked case

Pick a transaction that violates a policy:

High amount
   ↓
Policy blocked
   ↓
Manual review

This is one of the most important trust moments in the demo.

05 — Show a successful recovery

Demonstrate:

Failure
 ↓
Diagnosis
 ↓
Evidence
 ↓
Authorization
 ↓
Retry
 ↓
Recovered revenue

06 — Open the Decision Graph

Show the interactive graph and switch between:

Network

Clusters

Orbit

Use selection, filters, focus and path tracing to demonstrate how decisions and evidence connect.

07 — End on measurable impact

Compare:

Deterministic Baseline
        vs
AI-Assisted Recovery

Then show:

incremental revenue

recovery rate

precision

recall

F1

false approvals

The final message:

Revora doesn't just predict recovery. It makes recovery decisions measurable, explainable and bounded.

🏆 Why Revora Is Different

Most payment-recovery demos stop at:

Failed payment
      ↓
ML prediction
      ↓
Retry

Revora goes further:

Failed payment
      ↓
Detect opportunity
      ↓
Diagnose failure with AI
      ↓
Verify evidence
      ↓
Apply deterministic policy
      ↓
Bound the execution
      ↓
Escalate unsafe cases
      ↓
Simulate the outcome
      ↓
Measure incremental revenue
      ↓
Audit every decision

That distinction matters.

The system is designed around financial workflow safety, not merely model accuracy.

🔐 Five Design Principles

1. AI recommends, not executes

The model cannot directly move money.

2. Evidence before action

A recommendation must pass deterministic checks.

3. Policy before execution

Confidence alone is never sufficient authorization.

4. Bounded execution

Actions, retries and transaction amounts are explicitly constrained.

5. Every decision is auditable

The system records why a recovery was attempted, blocked or escalated.

🛠️ Tech Stack

Layer

Technology

Frontend

React + TypeScript

Build tool

Vite

Backend

Python + FastAPI

AI layer

AI-assisted payment diagnosis

Recovery engine

Python simulation

Policy engine

Deterministic rule-based authorization

Evidence layer

Deterministic verification

Execution

Simulated bounded recovery

Visualization

Interactive React intelligence workspace

Graph rendering

Interactive Network / Orbit visualization

▶️ Running Locally

1. Start the backend

From the project root:

cd OMNIX-main

Then:

.\.venv\Scripts\python.exe -m omnix.server

The FastAPI backend starts locally.

2. Start the frontend

Open another terminal:

cd frontend

Install dependencies if required:

npm install

Start the development server:

npm run dev

Then open:

http://localhost:5173/

📁 High-Level Project Structure

OMNIX-main/
│
├── frontend/
│   └── src/
│       ├── components/
│       ├── lib/
│       ├── store/
│       └── views/
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

⚠️ Current Prototype Boundaries

Revora is a hackathon prototype and intentionally uses a simulated recovery environment.

Current boundaries include:

no live payment gateway execution

no production payment credentials

simulated payment outcomes

no persistent cross-request idempotency store

bounded AI candidate evaluation for runtime/benchmark control

manual review represented as a decision state rather than a production operations queue

These limitations are intentional.

They keep the demonstration safe while preserving the architecture needed for a production-grade evolution.

🚀 Future Production Path

The architecture naturally leaves room for a production deployment path:

SIMULATED PAYMENT DATA
        ↓
REAL PAYMENT EVENTS
        ↓
RECOVERY SCORING
        ↓
AI DIAGNOSIS
        ↓
EVIDENCE + POLICY
        ↓
PRODUCTION-SAFE EXECUTOR
        ↓
PAYMENT GATEWAY
        ↓
OBSERVED OUTCOME
        ↓
CONTINUOUS EVALUATION

Potential production extensions include:

real payment event ingestion

production-grade idempotency storage

operational manual-review queues

richer recovery action allowlists

merchant-specific policies

stronger observability

offline model evaluation

policy versioning

human feedback loops

experiment management

production authentication and authorization
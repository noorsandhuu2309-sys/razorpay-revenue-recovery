"""OMNIX Agent Squad — a family of specialized multi-agent units.

Each unit is an MCU-style named engine (like AVALON) with a small team of
subagents and a live, streamed console in the NEXUS tab:

  NOVA     — Natural-language Orchestration & Virtual Assistant (router)
  ORACLE   — Operational Research, Analysis & Contextual Learning Engine
  SENTINEL — Security & ENdpoint Threat INtelligence Evaluation Layer
  FORGE    — Framework for Orchestrated Refactoring & Generative Engineering
  ATLAS    — Automated Task Logistics & Adaptive Scheduling
  WARDEN   — Watchful Auditor for Risk, Data & Enforcement of Norms
  MUSE     — Multimodal Understanding & Synthesis Engine
  PULSE    — Performance & Usage Live System Evaluator

They share one framework (base.py) and one background-job engine (jobs.py),
and run on the same local Ollama models the core OMNIX agents already use.
"""

__version__ = "0.1.0"

# AI Quant Assistant for ETF Rotation Strategies

> **FTEC5660 Group Project · CUHK FinTech Competition 2026**

A LangGraph-powered agentic system that helps researchers, relationship
managers and compliance officers work with ETF sector-rotation strategies
end-to-end: market scan → factor computation → four-quadrant selection →
ETF mapping → multi-agent debate → human-in-the-loop approval → audit
trace.

The project implements **14 of the 18 agentic design patterns** from the
FTEC5660 syllabus and ships a production-grade Streamlit frontend with
full English / 简体中文 language switching.

---

## What the product does

Three user roles, one agent, thirteen task types. Typical user journeys:

| Role | Task | One-sentence outcome |
|---|---|---|
| Researcher | Weekly rotation report | 2 minutes → factor table + quadrants + ETF picks + HTML/DOCX/PDF export |
| Researcher | Backtest comparison | Parallel monthly vs weekly run with Sharpe / drawdown diff |
| Researcher | Signal-news conflict check | Cross-validates golden-zone sectors against negative news |
| Researcher | Multi-agent debate | Quant + Macro + Risk specialists debate, Coordinator fuses |
| RM | Client portfolio | ETF list filtered by client risk level (R1–R5) with talking points |
| RM | Performance explanation | Client-ready narrative with evidence snippets |
| Compliance | Decision trace review | Field-completeness audit on the latest trace |
| Compliance | Risk check | Concentration / liquidity / veto-list violation report |

---

## Quick start

### Requirements

- Python 3.10+
- A virtual environment is recommended

### Install

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Minimum required:

```env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://aihubmix.com/v1   # any OpenAI-compatible endpoint
OPENAI_MODEL=deepseek-v3.2
```

Optional (each enables a subset of tools):

```env
JINA_API_KEY=...            # enables search_news_cn (Chinese news)
ALPHAVANTAGE_API_KEY=...    # enables search_news (global news)
TUSHARE_TOKEN=...           # enables richer A-share data
TUSHARE_API_URL=...
```

### Run

**Streamlit frontend (recommended — covers all 9 modules):**

```bash
streamlit run frontend/app.py
```

The frontend defaults to **English**; flip the language radio in the sidebar
to switch the whole UI (and the Multi-Agent Debate output) to 中文.

**CLI:**

```bash
# interactive
python main.py --role researcher --market a_share

# single query
python main.py --role researcher --market a_share \
    --query "Generate this week's A-share sector rotation report"

# export the latest trace to HTML / DOCX / PDF
python main.py --report
```

**MCP server** (standalone — optional):

```bash
# stdio (used by the agent by default)
python -m mcp_server.news_mcp_server

# HTTP, so MCP Inspector or another agent can connect
python -m mcp_server.news_mcp_server --http --port 8765
```

---

## Architecture

```
User query
    │
    ▼
┌───────────────────┐
│  Input Guardrail  │  ← Pattern 18
└─────────┬─────────┘
          ▼
┌───────────────────┐
│     Router        │  ← Pattern 2  (structured task classification)
└─────────┬─────────┘
          ▼
┌───────────────────┐
│     Planner       │  ← Pattern 6
└─────────┬─────────┘
          ▼
     ┌────┴────────────────────────────────────────┐
     │                                             │
┌────▼─────────────┐                         ┌─────▼──────┐
│ Fixed subgraphs  │  7 node chains          │  Executor  │  ← Pattern 5 (ReAct)
│ (weekly / BT /   │  (deterministic)        │  + ToolNode│     with tool budget
│  conflict / RM / │                         │            │     + repeat detection
│  compliance ...) │                         └─────┬──────┘
└────┬─────────────┘                               │
     │                                             │
     └────────────────┬────────────────────────────┘
                      ▼
              ┌───────────────────┐
              │    Reflection     │  ← Pattern 4  (critic → revise)
              └─────────┬─────────┘
                        ▼
              ┌───────────────────┐
              │ Output Guardrail  │  ← Pattern 18 + HITL gate
              └─────────┬─────────┘
                        ▼
                    Final answer
```

Cross-cutting layers (applied on every run):

- **Pattern 8 Memory** — per-thread JSON profile injected into prompts
- **Pattern 11 Goal Monitoring** — explicit sub-goal checklist per task
- **Pattern 16 Resource Tracking** — tokens / $ / tool calls / latency
- **Pattern 10 MCP** — news & macro tools accessible via Model Context Protocol

---

## Pattern coverage

| # | Pattern | Location |
|---|---|---|
| 1 | Prompt Chaining | `agent/prompts/prompt_builder.py` (base + role + task) |
| 2 | Routing | `agent/graph.py::router_node` + `agent/router_schema.py` |
| 3 | Parallelization | Monthly/weekly backtest, news fan-out, 3 specialists in parallel |
| 4 | Reflection | `agent/patterns/reflection.py` (real critic → revise loop) |
| 5 | Tool Use | `tools/` (30+ tools) + LangGraph `ToolNode` |
| 6 | Planning | `agent/graph.py::planner_node` |
| 7 | Multi-Agent | `agent/patterns/multi_agent.py` (Quant / Macro / Risk + Coordinator) |
| 8 | Long-term Memory | `agent/patterns/memory.py` (per-thread JSON, cross-session) |
| 10 | MCP | `mcp_server/` (FastMCP server + client) |
| 11 | Goal Setting & Monitoring | `agent/patterns/goal_monitor.py` |
| 12 | Exception Handling | Router fallback · tool budget · repeat-call detection |
| 14 | RAG | `agent/patterns/rag.py` + `tools/rag_tools.py` (FAISS + embeddings) |
| 15 | Inter-agent Communication | `agent/patterns/inter_agent.py` (Pydantic message schema) |
| 16 | Resource-aware Optimisation | `agent/patterns/resource_tracker.py` |
| 17 | Reasoning (Self-Consistency) | `agent/patterns/reasoning.py` (3-sample majority vote) |
| 18 | Guardrails | `agent/patterns/guardrails.py` (input + output + HITL queue) |

Four patterns from the syllabus (9, 13 and two minor variants) are
intentionally out of scope and documented in `docs/A_PLUS_ARCHITECTURE.md`.

---

## Frontend — nine Streamlit modules

| Module | Purpose |
|---|---|
| Chat | Main conversation surface with live Pattern badges, Goal checklist, Resource meter, Guardrail panel and Reflection timeline |
| Multi-Agent Debate | Visualises Quant / Macro / Risk debate with per-vote evidence and disagreement resolution |
| Backtest Lab | Runs monthly and weekly backtests in parallel and compares metrics side-by-side |
| Decision Trace | Browses the `traces/` directory with JSON preview and file download |
| RAG Library | Queries the internal research corpus (FAISS index); rebuilds the index on demand |
| Pattern Dashboard | Per-thread histogram of pattern invocations |
| HITL Approval | Compliance approval queue for formal recommendations |
| MCP Inspector | Handshakes with the FastMCP server and calls tools interactively |
| Settings | Long-term memory profile editor plus `config/` YAML viewer |

All nine modules are bilingual (EN / 中文), toggled from the sidebar at any
time. Business values (market codes, role names, quadrant labels) are kept
in their canonical form and displayed with translated labels.

---

## Repository layout

```
ETF_Assistant/
├── main.py                     # CLI entry point
├── requirements.txt
├── .env.example

├── agent/
│   ├── graph.py                # Main LangGraph: Router / Planner / Executor / Reflector / Finalizer
│   ├── subgraph.py             # 7 fixed-shape task subgraphs
│   ├── state.py                # AgentState TypedDict (including output_language)
│   ├── router_schema.py        # Structured router output schema
│   ├── patterns/               # 14 pattern modules, one file each
│   └── prompts/                # Base / role / task / router / reflection prompts

├── tools/                      # 30+ LangChain tools (data, factor, score, map, backtest, news, trace, report, rag, mcp)
├── data/providers/             # akshare / tushare / yfinance adapters
├── backtest/                   # Rebalance loop + metrics
├── config/                     # Market, factor, threshold, ETF-mapping, risk YAML

├── frontend/
│   ├── app.py                  # Entry: navigation + hero + product tour
│   ├── i18n.py                 # EN / 中文 translation table + helpers
│   ├── _bootstrap.py           # Shared sys.path / dotenv / init_lang
│   └── pages/                  # Nine module pages

├── mcp_server/                 # FastMCP news & macro server + client
├── scripts/generate_report_html.py  # Trace → HTML / DOCX / PDF
├── templates/                  # Weekly report / talking-point templates
├── docs/                       # RAG source documents + architecture notes
├── traces/                     # Runtime artefacts (trace_*.json, reports, HITL queue, memory)
└── tests/manual/               # Smoke scripts for data sources & end-to-end flow
```

---

## Supported tasks

All thirteen task types are listed in `agent/prompts/task_prompts.py`. The
seven currently implemented as fixed subgraphs (deterministic, auditable
flows) are:

- `research_weekly_report`
- `research_backtest_compare`
- `research_conflict_check`
- `rm_explain_performance`
- `rm_client_portfolio`
- `compliance_trace_review`
- `compliance_risk_check`

The remaining six (`research_overlay_adjustment`, `rm_batch_talking_points`,
`rm_market_specific`, `compliance_veto_audit`, `compliance_drawdown_check`,
`generic`) fall through to the ReAct Executor with tool budget and
repeat-call protection.

---

## Demo scripts

```bash
# Researcher flow
python main.py --role researcher --market a_share --query "Generate this week's A-share sector rotation report"
python main.py --role researcher --market a_share --query "Compare monthly vs weekly rebalance backtests"
python main.py --role researcher --market a_share --query "Check if golden-zone sectors conflict with negative news"

# RM flow
python main.py --role rm --market a_share --query "Prepare an ETF portfolio for an R3 client"
python main.py --role rm --market a_share --query "Explain last week's performance and draft a client talking point"

# Compliance flow
python main.py --role compliance --market a_share --query "Audit the latest decision trace"
python main.py --role compliance --market a_share --query "Run a concentration / liquidity risk check"

# Export the most recent trace to HTML / DOCX / PDF
python main.py --report
```

Equivalent Chinese-language prompts all work out of the box (the base
system prompt mirrors the user's language automatically).

---

## Language handling

- **Default output language is English.** The base system prompt in
  `agent/prompts/base_prompt.py` instructs the LLM to mirror the user's
  input language, so asking in Chinese yields a Chinese answer and asking
  in English yields an English answer — across Chat, all seven fixed
  subgraphs and the generic executor.
- **The Multi-Agent Debate page reads `current_lang()` from the UI and
  threads it through `AgentState.output_language` into every specialist
  prompt.** Switching the sidebar radio changes Quant / Macro / Risk
  output language on the next run.
- **Quick-start buttons on the Chat page ship with both EN and ZH query
  variants.** Clicking a button sends the query in the currently selected
  UI language so the response language stays consistent.

---

## Runtime artefacts

Running the agent creates files under `traces/` (git-ignored):

- `traces/<date>/trace_*.json` — decision trace (full state snapshot)
- `traces/<date>/weekly_report.{html,docx,pdf}` — exported reports
- `traces/memory/<thread>.json` — long-term memory per user
- `traces/hitl/` — pending / approved / rejected HITL requests

---

## Known limitations

- A-shares are the most complete market; HK and US paths are scaffolded
  but not fully tuned.
- `ResourceTracker` prices are hard-coded for DeepSeek-V3.2; add a
  `config/models.yaml` before introducing a model switcher for multiple
  providers.
- The `tushare` client is not fully thread-safe; the 4-way parallel
  macro fetch in `multi_agent_debate_node` has a `_is_useful` filter and
  4-way redundancy, so worst case is a sparser Macro agent report — no
  crash.
- The backend still contains some Chinese code comments and log strings
  (prompts are English-first). A follow-up PR is planned to fully
  English-ify backend internals.
- There is no `pytest` suite yet; QA is via the manual smoke scripts in
  `tests/manual/`.

---

## License

No LICENSE file is currently included. If this is open-sourced or shared
beyond the team, add an appropriate license before distribution.

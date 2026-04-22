"""
Pattern 7: Multi-Agent Collaboration  (+ Pattern 3 Parallelization, Pattern 15 Inter-agent Comm).

Three specialist agents debate a sector-allocation question, and a coordinator
aggregates their structured reports into a final verdict. The specialists are
invoked **in parallel** (Pattern 3) via ``concurrent.futures``.

Specialists:
- **Quant Agent**  — sees only the factor table + quadrant distribution.
- **Macro Agent**  — sees macro events + recent news headlines.
- **Risk Agent**   — sees the veto_list + liquidity/concentration rules.

Each produces a structured ``AgentReport`` (Pattern 15). The Coordinator fuses
them into a ``DebateVerdict``. When there is disagreement, the Coordinator
invokes ``self_consistency_vote`` from Pattern 17 to resolve it.
"""
from __future__ import annotations

import concurrent.futures as _futures
import os
from dataclasses import dataclass
from typing import Any, Optional

from langchain_openai import ChatOpenAI

from agent.patterns.inter_agent import (
    AgentReport,
    AgentRole,
    AgentVote,
    DebateVerdict,
    Disagreement,
    Stance,
)
from agent.patterns.pattern_log import log_pattern_use
from agent.patterns.reasoning import self_consistency_vote


# ----------------------------- Specialist prompts -----------------------------

QUANT_SYSTEM = """You are the QUANT specialist in a multi-agent ETF advisory debate.

You only look at QUANTITATIVE evidence: factor scores and the four-quadrant
classification.

Available factors (ranked by information content):
- `ma_score`: moving-average trend strength (5 levels: -2/-1/0/+1/+2)
- `momentum`: 12-month momentum with recent-month skip (continuous)
- `trend_score`: composite = weighted(ma_rank, mom_rank) -- PRIMARY signal
- `consensus_score`: PLACEHOLDER ONLY in current build. Under-lying sub-factors
  (etf_flow_contrarian / smart_money / volatility_convergence) are not yet
  wired to real data sources, so consensus_score degenerates to a constant
  near 0.5 across sectors. **IGNORE consensus_score when ranking**; base your
  votes on `ma_score`, `momentum`, and `trend_score` instead.

Rules:
- Vote 'overweight' on Golden-zone sectors with top trend_score AND ma_score>=1.
- Vote 'neutral' on Left-side-watch sectors.
- Vote 'underweight' on Warning / Garbage zone sectors or sectors with negative ma_score AND momentum.
- NEVER veto a sector -- that is the Risk agent's job.
- Cite factor numbers verbatim in evidences (e.g., "ma_score=2, momentum=0.18").
- If a factor is truly missing, say so in caveats. Do NOT add caveats about consensus_score being constant (this is already known and documented above).

Output language: **Always write summary, rationale and caveats in Simplified Chinese (简体中文)**, even though these instructions are in English. Sector names must stay in Chinese exactly as given.

Return your output as a structured AgentReport with role='quant'."""

MACRO_SYSTEM = """You are the MACRO specialist in a multi-agent ETF advisory debate.

You look at MACRO + NEWS evidence in the following priority:
1. Macro events block (may contain sub-sections: 国内宏观快讯 / 宏观主题新闻 / 全球财经新闻)
2. News snippets block (sector-keyword news)
3. Observation pool (export/policy/defensive chains) — use as supplemental framing
4. Veto / negative list — use only as a hard filter, not as primary evidence

Interpretation rules:
- If the macro events block contains ANY named sub-section (e.g. 国内宏观快讯,
  全球财经新闻), treat it as sufficient macro evidence -- DO NOT claim "no
  macro data" in caveats.
- Only write a caveat of "no macro evidence" when the macro events text
  literally says it is empty (e.g. "(国内 + 全球宏观源此刻均返回空...").
- A sector-keyword news headline is valid macro evidence when it describes a
  policy, regulatory, monetary, fiscal or geopolitical theme.

Voting rules:
- 'overweight' on sectors whose macro logic + news flow are supportive.
- 'underweight' on sectors facing macro headwinds.
- 'veto' ONLY when there is a hard policy/regulatory block AND you cite a news
  snippet as evidence.
- Cite news/snippet content (with a short direct quote) in each vote's evidences.

Output language: **Always write summary, rationale and caveats in Simplified Chinese (简体中文)**. Sector names stay in Chinese as given.

Return your output as a structured AgentReport with role='macro'."""

RISK_SYSTEM = """You are the RISK specialist in a multi-agent ETF advisory debate.

You only look at RISK evidence: the IC overlay negative list, ETF liquidity
and scale thresholds, drawdown limits, and the client's risk level.

Rules:
- Use 'veto' on any sector present in the negative list or violating thresholds.
- Use 'underweight' on borderline sectors (e.g., marginally illiquid ETFs).
- Never vote 'overweight' — you are the brake, not the throttle.
- Cite the specific rule ID / threshold violated in evidences.
- For RM tasks, adjust veto severity by client_risk_level (R1/R2 stricter).

Output language: **Always write summary, rationale and caveats in Simplified Chinese (简体中文)**. Sector names stay in Chinese as given.

Return your output as a structured AgentReport with role='risk'."""


COORDINATOR_SYSTEM = """You are the COORDINATOR of a multi-agent ETF advisory debate.

You receive three structured reports from specialists (Quant, Macro, Risk) and
must produce a fused DebateVerdict.

Aggregation rules:
1. If ANY agent votes 'veto' on a sector, exclude it; record as a Disagreement
   if another agent was positive on it.
2. For remaining sectors, compute consensus stance = majority of Quant+Macro
   weighted by confidence. Underweight wins over Overweight if votes tie.
3. List disagreements between specialists explicitly (which agents were on
   which side and how you resolved it).
4. ``recommended_sectors`` = top sectors the ensemble is Overweight on
   (max 5, ordered by aggregated confidence).
5. ``vetoed_sectors`` = every sector receiving at least one 'veto'.
6. Write a plain-language ``narrative`` describing the aggregate view, calling
   out disagreements.

Output language: **Write the narrative in Simplified Chinese (简体中文)**. Sector names and labels stay in Chinese as given.

Output a single DebateVerdict."""


# ------------------------------ Specialist LLMs -------------------------------


@dataclass
class DebateInputs:
    """Everything the specialists need as ambient context."""
    market: str
    factor_summary: str = ""
    quadrant_summary: str = ""
    observation_pool: str = ""
    veto_list_text: str = ""
    macro_events: str = ""
    news_text: str = ""
    client_risk_level: Optional[str] = None
    user_question: str = ""


def _build_llm(model: str | None = None, temperature: float = 0.2) -> ChatOpenAI:
    model = model or os.getenv("OPENAI_MODEL", "deepseek-v3.2")
    return ChatOpenAI(model=model, temperature=temperature)


def _invoke_specialist(
    role: AgentRole,
    system_prompt: str,
    inputs: DebateInputs,
    model: str | None,
    thread_id: str,
) -> AgentReport:
    log_pattern_use(
        thread_id,
        7,
        "Multi-Agent",
        f"specialist_{role.value}",
        "spawn",
    )
    llm = _build_llm(model=model, temperature=0.2).with_structured_output(AgentReport)

    shared_context = f"""Market: {inputs.market}
Client risk level: {inputs.client_risk_level or 'N/A'}
User question: {inputs.user_question}

## Factor summary
{inputs.factor_summary or '(none)'}

## Quadrant distribution
{inputs.quadrant_summary or '(none)'}

## Observation pool
{inputs.observation_pool or '(none)'}

## Veto / negative list
{inputs.veto_list_text or '(none)'}

## Macro events
{inputs.macro_events or '(none)'}

## News snippets
{inputs.news_text[:2000] or '(none)'}
"""
    try:
        report: AgentReport = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": shared_context},
            ]
        )
    except Exception as exc:
        log_pattern_use(
            thread_id, 7, "Multi-Agent", f"specialist_{role.value}_failed", str(exc)
        )
        return AgentReport(
            role=role, summary=f"LLM error: {exc}", votes=[], caveats=["invocation failed"]
        )

    # Force role field so downstream logic is reliable
    report.role = role
    return report


def run_debate_parallel(
    inputs: DebateInputs,
    thread_id: str = "default",
    model: str | None = None,
) -> dict[str, Any]:
    """Run the three specialists in parallel (Pattern 3)."""
    log_pattern_use(
        thread_id, 3, "Parallelization", "fan_out_specialists", "3 specialists"
    )

    tasks = [
        (AgentRole.QUANT, QUANT_SYSTEM),
        (AgentRole.MACRO, MACRO_SYSTEM),
        (AgentRole.RISK,  RISK_SYSTEM),
    ]
    reports: dict[AgentRole, AgentReport] = {}
    with _futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_invoke_specialist, role, sys, inputs, model, thread_id): role
            for role, sys in tasks
        }
        for fut in _futures.as_completed(futures):
            role = futures[fut]
            try:
                reports[role] = fut.result()
            except Exception as exc:
                reports[role] = AgentReport(
                    role=role,
                    summary=f"Failed: {exc}",
                    votes=[],
                    caveats=["execution error"],
                )

    log_pattern_use(
        thread_id,
        7,
        "Multi-Agent",
        "specialists_done",
        f"reports from {[r.value for r in reports.keys()]}",
    )

    verdict = _aggregate(reports, inputs, thread_id=thread_id, model=model)
    return {
        "inputs": {
            "market": inputs.market,
            "client_risk_level": inputs.client_risk_level,
            "user_question": inputs.user_question,
        },
        "reports": {r.value: rep.model_dump() for r, rep in reports.items()},
        "verdict": verdict.model_dump(),
    }


# ----------------------------- Coordinator logic ------------------------------


def _aggregate(
    reports: dict[AgentRole, AgentReport],
    inputs: DebateInputs,
    thread_id: str,
    model: str | None = None,
) -> DebateVerdict:
    log_pattern_use(
        thread_id, 7, "Multi-Agent", "coordinator_aggregate", "fusing reports"
    )

    # Gather all sectors mentioned by any specialist
    sectors: set[str] = set()
    for rep in reports.values():
        for v in rep.votes:
            sectors.add(v.sector)

    final_stance: dict[str, Stance] = {}
    final_confidence: dict[str, float] = {}
    disagreements: list[Disagreement] = []
    vetoed: list[str] = []

    for sector in sectors:
        per_agent_votes: dict[AgentRole, AgentVote] = {}
        for role, rep in reports.items():
            for v in rep.votes:
                if v.sector == sector:
                    per_agent_votes[role] = v
                    break

        # Veto rule: any veto -> sector is out
        has_veto = any(v.stance == Stance.VETO for v in per_agent_votes.values())
        if has_veto:
            vetoed.append(sector)
            final_stance[sector] = Stance.VETO
            final_confidence[sector] = max(
                (v.confidence for v in per_agent_votes.values() if v.stance == Stance.VETO),
                default=0.5,
            )
            # Is anyone positive? record disagreement
            pro = [r for r, v in per_agent_votes.items() if v.stance == Stance.OVERWEIGHT]
            if pro:
                disagreements.append(
                    Disagreement(
                        sector=sector,
                        agents_pro=pro,
                        agents_con=[r for r, v in per_agent_votes.items() if v.stance == Stance.VETO],
                        resolution="Veto wins (Pattern 18 safety): risk agent's veto is non-negotiable.",
                    )
                )
            continue

        # No veto — compute majority stance
        weight_sum: dict[Stance, float] = {s: 0.0 for s in Stance}
        for v in per_agent_votes.values():
            weight_sum[v.stance] += v.confidence

        top_stance = max(weight_sum, key=weight_sum.get)
        top_score = weight_sum[top_stance]
        tied = [s for s, w in weight_sum.items() if w == top_score and s != top_stance]

        if tied and any(s == Stance.UNDERWEIGHT for s in tied + [top_stance]):
            top_stance = Stance.UNDERWEIGHT  # prudent tie-break

        # Check for meaningful disagreement: did any agent disagree?
        stances_present = {v.stance for v in per_agent_votes.values()}
        if len(stances_present) > 1:
            # Use self-consistency to resolve only when the score margin is thin.
            if top_score < 0.8 and model is not None:
                try:
                    resolved = _resolve_by_self_consistency(
                        sector=sector,
                        per_agent_votes=per_agent_votes,
                        inputs=inputs,
                        thread_id=thread_id,
                        model=model,
                    )
                    if resolved:
                        top_stance = resolved["chosen"]
                except Exception:
                    pass
            disagreements.append(
                Disagreement(
                    sector=sector,
                    agents_pro=[
                        r for r, v in per_agent_votes.items() if v.stance == Stance.OVERWEIGHT
                    ],
                    agents_con=[
                        r
                        for r, v in per_agent_votes.items()
                        if v.stance in {Stance.UNDERWEIGHT, Stance.VETO}
                    ],
                    resolution=f"confidence-weighted majority -> {top_stance.value}",
                )
            )

        final_stance[sector] = top_stance
        final_confidence[sector] = round(top_score / max(len(per_agent_votes), 1), 2)

    recommended = sorted(
        [s for s, st in final_stance.items() if st == Stance.OVERWEIGHT],
        key=lambda s: -final_confidence.get(s, 0),
    )[:5]

    narrative_parts = [
        f"Consensus across {len(reports)} specialists over {len(sectors)} sectors.",
        f"Recommended (overweight): {', '.join(recommended) if recommended else '无'}.",
        f"Vetoed: {', '.join(vetoed) if vetoed else '无'}.",
        f"Disagreements logged: {len(disagreements)}.",
    ]
    if disagreements[:3]:
        narrative_parts.append("Key disagreements: " + "; ".join(
            f"{d.sector}({d.resolution})" for d in disagreements[:3]
        ))

    return DebateVerdict(
        final_stance_per_sector=final_stance,
        confidence_per_sector=final_confidence,
        disagreements=disagreements,
        narrative=" ".join(narrative_parts),
        recommended_sectors=recommended,
        vetoed_sectors=vetoed,
    )


# --------------------- Self-Consistency branch (Pattern 17) --------------------


def _resolve_by_self_consistency(
    sector: str,
    per_agent_votes: dict[AgentRole, AgentVote],
    inputs: DebateInputs,
    thread_id: str,
    model: str,
) -> dict | None:
    """Call the coordinator LLM N times with higher temperature, majority wins."""
    from pydantic import BaseModel

    class TinyDecision(BaseModel):
        sector: str
        stance: Stance
        reason: str

    llm = _build_llm(model=model, temperature=0.7).with_structured_output(TinyDecision)
    prompt = f"""Three ETF specialists disagree on sector "{sector}". Decide its final stance.

Specialist votes:
""" + "\n".join(
        f"- {r.value}: stance={v.stance.value} conf={v.confidence} -- {v.rationale[:180]}"
        for r, v in per_agent_votes.items()
    ) + f"""

Market: {inputs.market}
Client risk: {inputs.client_risk_level or 'N/A'}

Give a final stance among [overweight, neutral, underweight, veto] and a 1-sentence reason.
"""

    def invoke(p: str) -> TinyDecision:
        return llm.invoke([{"role": "user", "content": p}])

    result = self_consistency_vote(
        thread_id=thread_id,
        prompt=prompt,
        llm_invoke=invoke,
        extract_choice=lambda d: d.stance,
        samples=3,
    )
    return result if result.get("chosen") else None

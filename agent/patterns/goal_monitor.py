"""
Pattern 11: Goal Setting and Monitoring.

Each user request is translated into an explicit ``GoalState`` object, and every
subsequent node checks off sub-goals as it completes them. This gives two
benefits:

1. **Explainability**: the UI renders a checklist of (sub_goal -> status).
2. **Early stop**: when all sub-goals are satisfied, the finaliser skips further
   tool calls.

The mapping from ``task_key`` to the expected goal template lives in
``TASK_GOAL_TEMPLATES``. Sub-goals are marked satisfied when their expected
signals (e.g. ``factor_df_computed``, ``etf_mapping_produced``) appear in the
task payload.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from agent.patterns.pattern_log import log_pattern_use


@dataclass
class SubGoal:
    id: str
    description: str
    signal_key: str          # the key in payload that satisfies this sub-goal
    satisfied: bool = False
    satisfied_at: str = ""

    def mark(self) -> None:
        self.satisfied = True
        self.satisfied_at = datetime.now().isoformat(timespec="seconds")


@dataclass
class GoalState:
    task_key: str
    objective: str
    sub_goals: list[SubGoal] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def progress(self) -> float:
        if not self.sub_goals:
            return 1.0
        return sum(1 for g in self.sub_goals if g.satisfied) / len(self.sub_goals)

    def is_complete(self) -> bool:
        return bool(self.sub_goals) and all(g.satisfied for g in self.sub_goals)

    def to_dict(self) -> dict:
        return asdict(self)


TASK_GOAL_TEMPLATES: dict[str, dict[str, Any]] = {
    "research_weekly_report": {
        "objective": "产出可审批的本期 A 股行业轮动周报",
        "sub_goals": [
            ("fetch_market_data", "获取市场与 ETF 行情数据", "market_data_summary"),
            ("compute_factors", "计算 5 因子与趋势/共识分", "factor_summary"),
            ("map_quadrants", "完成四象限划分", "quadrant_distribution"),
            ("apply_overlay", "套用观察池与负面清单", "observation_pool_filter"),
            ("map_etf", "产出 ETF 组合建议", "portfolio_recommendation"),
        ],
    },
    "rm_client_portfolio": {
        "objective": "生成适配客户风险等级的 ETF 组合与话术",
        "sub_goals": [
            ("compute_factors", "计算当期因子", "industries"),
            ("pick_sectors", "筛选候选行业", "industries"),
            ("map_etf", "映射行业到 ETF", "mapped"),
        ],
    },
    "rm_explain_performance": {
        "objective": "解释上期推荐表现并产出客户话术",
        "sub_goals": [
            ("load_history", "拉取近期决策 trace", "history"),
            ("enrich_news", "补充相关新闻证据", "news"),
        ],
    },
    "research_conflict_check": {
        "objective": "核查当期量化信号与主观观察池/负面清单/新闻的冲突",
        "sub_goals": [
            ("compute_factors", "计算因子", "market"),
            ("pick_golden", "定位黄金区重点行业", "golden_industries"),
            ("cross_validate_news", "新闻交叉验证", "news"),
        ],
    },
    "research_backtest_compare": {
        "objective": "完成月频 vs 周频参数回测对比",
        "sub_goals": [
            ("backtest_monthly", "月频回测", "monthly"),
            ("backtest_weekly", "周频回测", "weekly"),
        ],
    },
    "compliance_trace_review": {
        "objective": "审查最近一期 Decision Trace 的完整性",
        "sub_goals": [
            ("locate_trace", "定位最新 trace", "market"),
        ],
    },
    "compliance_risk_check": {
        "objective": "对当前组合执行合规风险检查",
        "sub_goals": [
            ("load_trace", "读取 trace", "market"),
        ],
    },
    "generic": {
        "objective": "响应通用问题并给出合理答复",
        "sub_goals": [
            ("respond", "产出最终回答", "final_response"),
        ],
    },
}


def init_goal_state(task_key: str, thread_id: str = "default") -> GoalState:
    """Build a GoalState from the task template."""
    log_pattern_use(
        thread_id,
        11,
        "Goal Setting & Monitoring",
        "init_goal",
        f"task={task_key}",
    )
    template = TASK_GOAL_TEMPLATES.get(task_key) or TASK_GOAL_TEMPLATES["generic"]
    sub_goals = [
        SubGoal(id=sid, description=desc, signal_key=sig)
        for (sid, desc, sig) in template["sub_goals"]
    ]
    return GoalState(
        task_key=task_key,
        objective=template["objective"],
        sub_goals=sub_goals,
        started_at=datetime.now().isoformat(timespec="seconds"),
    )


def update_goal_progress(
    goal: GoalState, payload: dict, thread_id: str = "default"
) -> GoalState:
    """Mark sub-goals satisfied based on keys/values present in payload."""
    if not goal or not payload:
        return goal
    changed = 0
    for sg in goal.sub_goals:
        if sg.satisfied:
            continue
        val = payload.get(sg.signal_key)
        if val is None:
            continue
        if isinstance(val, (list, dict, str)) and not val:
            continue
        sg.mark()
        changed += 1
    if changed:
        log_pattern_use(
            thread_id,
            11,
            "Goal Setting & Monitoring",
            "progress_update",
            f"+{changed} sub-goals -> progress={goal.progress():.0%}",
        )
    if goal.is_complete() and not goal.completed_at:
        goal.completed_at = datetime.now().isoformat(timespec="seconds")
    return goal


def goal_progress_snippet(goal: GoalState) -> str:
    """Render the goal state as a markdown checklist for the finaliser prompt."""
    if not goal or not goal.sub_goals:
        return ""
    lines = [
        f"## Goal (Pattern 11)",
        f"- Objective: {goal.objective}",
        f"- Progress: {int(goal.progress() * 100)}% ({sum(1 for g in goal.sub_goals if g.satisfied)}/{len(goal.sub_goals)})",
        "- Sub-goals:",
    ]
    for sg in goal.sub_goals:
        mark = "✅" if sg.satisfied else "⬜"
        lines.append(f"  - {mark} {sg.id} — {sg.description}")
    return "\n".join(lines)

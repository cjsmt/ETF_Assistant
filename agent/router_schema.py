from enum import Enum

from pydantic import BaseModel, Field


class TaskKey(str, Enum):
    GENERIC = "generic"
    RESEARCH_WEEKLY_REPORT = "research_weekly_report"
    RESEARCH_OVERLAY_ADJUSTMENT = "research_overlay_adjustment"
    RESEARCH_BACKTEST_COMPARE = "research_backtest_compare"
    RESEARCH_CONFLICT_CHECK = "research_conflict_check"
    RM_CLIENT_PORTFOLIO = "rm_client_portfolio"
    RM_EXPLAIN_PERFORMANCE = "rm_explain_performance"
    RM_BATCH_TALKING_POINTS = "rm_batch_talking_points"
    RM_MARKET_SPECIFIC = "rm_market_specific"
    COMPLIANCE_TRACE_REVIEW = "compliance_trace_review"
    COMPLIANCE_RISK_CHECK = "compliance_risk_check"
    COMPLIANCE_VETO_AUDIT = "compliance_veto_audit"
    COMPLIANCE_DRAWDOWN_CHECK = "compliance_drawdown_check"


class DataStrategy(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    HISTORY_FIRST = "history_first"
    FRESH_SCAN = "fresh_scan"
    HYBRID = "hybrid"


class RouterDecision(BaseModel):
    task_key: TaskKey = Field(description="最适合当前用户请求的任务类型。")
    data_strategy: DataStrategy = Field(
        description="推荐的数据策略：直接回答、优先查历史、优先做当前扫描、或历史与当前结合。"
    )
    should_use_tools: bool = Field(description="当前任务是否需要进入工具执行阶段。")
    requires_trace_save: bool = Field(description="当前任务完成后是否建议保存 Decision Trace。")
    confidence: float = Field(ge=0.0, le=1.0, description="本次路由判断的置信度。")
    route_reason: str = Field(description="用一句话说明为什么这样分类。")

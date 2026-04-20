from typing import TypedDict, Annotated, Optional
from langgraph.graph.message import add_messages

class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    user_input: str
    market: str
    role: str  # researcher | rm | compliance
    client_risk_level: Optional[str]  # R1-R5, only for RM role
    task_key: str
    route_reason: str
    route_confidence: float
    data_strategy: str
    should_use_tools: bool
    requires_trace_save: bool
    execution_plan: str
    tool_call_count: int
    last_tool_signature: str
    repeated_tool_call_count: int
    stop_reason: str
    workflow_context: str
    task_payload: dict
    latest_trace_path: str

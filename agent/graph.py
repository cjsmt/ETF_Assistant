import json
import os
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.prompts.prompt_builder import build_system_prompt
from agent.prompts.reflection_prompt import REFLECTION_PROMPT
from agent.prompts.router_prompts import build_router_prompt
from agent.prompts.task_prompts import infer_task_key
from agent.prompts.workflow_prompts import (
    build_executor_guidance,
    build_finalizer_guidance,
    build_planner_prompt,
)
from agent.router_schema import DataStrategy, RouterDecision
from agent.state import AgentState
from agent.subgraph import SUBGRAPH_EDGE_MAP, register_subgraph_nodes, route_after_planner
from tools import ALL_TOOLS

load_dotenv()

MAX_TOOL_CALLS = 8
MAX_REPEATED_TOOL_CALLS = 2


def _extract_user_input(state: AgentState) -> str:
    if state.get("user_input"):
        return state["user_input"]
    for msg in reversed(state["messages"]):
        if getattr(msg, "type", "") == "human":
            return getattr(msg, "content", "")
        if isinstance(msg, dict) and msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def _format_tool_signature(tool_calls) -> str:
    normalized = []
    for tc in tool_calls:
        normalized.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def _executor_next(state: AgentState) -> Literal["tools", "finalize", "end"]:
    if state.get("stop_reason"):
        return "finalize"
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return "end"


def build_graph(model_name: str = None) -> StateGraph:
    model = model_name or os.getenv("OPENAI_MODEL", "deepseek-v3.2")
    base_llm = ChatOpenAI(model=model, temperature=0)
    router_llm = base_llm.with_structured_output(RouterDecision)
    tool_llm = base_llm.bind_tools(ALL_TOOLS)
    tool_node = ToolNode(ALL_TOOLS)

    def router_node(state: AgentState):
        user_input = _extract_user_input(state)
        role = state.get("role", "researcher")
        router_prompt = build_router_prompt(
            role=role,
            market=state.get("market", "a_share"),
            user_input=user_input,
            client_risk_level=state.get("client_risk_level"),
        )
        try:
            decision = router_llm.invoke([{"role": "system", "content": router_prompt}])
            task_key = decision.task_key.value
            data_strategy = decision.data_strategy.value
            should_use_tools = decision.should_use_tools
            requires_trace_save = decision.requires_trace_save
            route_confidence = decision.confidence
            route_reason = decision.route_reason
        except Exception:
            task_key = infer_task_key(user_input=user_input, role=role)
            lowered = (user_input or "").lower()
            if any(x in lowered for x in ["为什么跌", "为什么涨", "解释", "trace", "审查", "回顾", "上周"]):
                data_strategy = DataStrategy.HISTORY_FIRST.value
            elif any(x in lowered for x in ["周报", "本周", "行业轮动", "扫描", "组合", "市场"]):
                data_strategy = DataStrategy.FRESH_SCAN.value
            else:
                data_strategy = DataStrategy.HYBRID.value if task_key != "generic" else DataStrategy.DIRECT_ANSWER.value
            should_use_tools = task_key != "generic"
            requires_trace_save = any(x in lowered for x in ["周报", "正式", "审批", "组合建议"])
            route_confidence = 0.5
            route_reason = f"结构化路由失败，回退到规则路由，任务识别为 {task_key}"

        return {
            "user_input": user_input,
            "task_key": task_key,
            "route_reason": route_reason,
            "route_confidence": route_confidence,
            "data_strategy": data_strategy,
            "should_use_tools": should_use_tools,
            "requires_trace_save": requires_trace_save,
            "stop_reason": "",
        }

    def planner_node(state: AgentState):
        planner_prompt = build_planner_prompt(
            role=state.get("role", "researcher"),
            market=state.get("market", "a_share"),
            task_key=state.get("task_key", "generic"),
            user_input=state.get("user_input", ""),
            data_strategy=state.get("data_strategy", "fresh_scan"),
            requires_trace_save=state.get("requires_trace_save", False),
            client_risk_level=state.get("client_risk_level"),
        )
        response = base_llm.invoke([{"role": "system", "content": planner_prompt}])
        return {"execution_plan": response.content}

    def executor_node(state: AgentState):
        today = datetime.now().strftime("%Y-%m-%d")
        date_hint = f"\n\n【系统信息】当前日期：{today}（解读「本周」「今日」「近250日」等相对时间时请以此为准）"
        user_input = _extract_user_input(state)
        system_prompt = build_system_prompt(
            role=state.get("role", "researcher"),
            market=state.get("market", "a_share"),
            user_input=user_input,
            client_risk_level=state.get("client_risk_level"),
        )
        execution_guidance = build_executor_guidance(
            plan=state.get("execution_plan", "无计划"),
            max_tool_calls=MAX_TOOL_CALLS,
            tool_call_count=state.get("tool_call_count", 0),
            data_strategy=state.get("data_strategy", "fresh_scan"),
            requires_trace_save=state.get("requires_trace_save", False),
        )
        system_msg = {"role": "system", "content": system_prompt + "\n\n" + execution_guidance + date_hint}
        response = tool_llm.invoke([system_msg] + state["messages"])

        updates = {"messages": [response], "stop_reason": ""}
        if hasattr(response, "tool_calls") and response.tool_calls:
            signature = _format_tool_signature(response.tool_calls)
            previous_signature = state.get("last_tool_signature", "")
            repeated_count = state.get("repeated_tool_call_count", 0)
            repeated_count = repeated_count + 1 if signature == previous_signature else 0
            tool_call_count = state.get("tool_call_count", 0) + len(response.tool_calls)
            stop_reason = ""
            if repeated_count >= MAX_REPEATED_TOOL_CALLS:
                stop_reason = "检测到重复工具调用模式，已提前停止以避免死循环。"
            elif tool_call_count >= MAX_TOOL_CALLS:
                stop_reason = "已达到工具调用预算上限，转入收束回答。"
            updates.update(
                {
                    "tool_call_count": tool_call_count,
                    "last_tool_signature": signature,
                    "repeated_tool_call_count": repeated_count,
                    "stop_reason": stop_reason,
                }
            )
        return updates

    def finalizer_node(state: AgentState):
        today = datetime.now().strftime("%Y-%m-%d")
        date_hint = f"\n\n【系统信息】当前日期：{today}（解读「本周」「今日」「近250日」等相对时间时请以此为准）"
        system_prompt = build_system_prompt(
            role=state.get("role", "researcher"),
            market=state.get("market", "a_share"),
            user_input=state.get("user_input", ""),
            client_risk_level=state.get("client_risk_level"),
        )
        final_guidance = build_finalizer_guidance(state.get("stop_reason")) + "\n\n" + REFLECTION_PROMPT
        workflow_context = state.get("workflow_context", "")
        if workflow_context:
            final_guidance += "\n\n## 固定子图上下文\n" + workflow_context
        response = base_llm.invoke([{"role": "system", "content": system_prompt + "\n\n" + final_guidance + date_hint}] + state["messages"])
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("planner", planner_node)
    register_subgraph_nodes(graph)
    graph.add_node("executor", executor_node)
    graph.add_node("tools", tool_node)
    graph.add_node("finalize", finalizer_node)
    graph.set_entry_point("router")
    graph.add_edge("router", "planner")
    graph.add_conditional_edges("planner", route_after_planner, SUBGRAPH_EDGE_MAP)

    graph.add_edge("weekly_prepare", "weekly_persist")
    graph.add_edge("weekly_persist", "finalize")
    graph.add_edge("trace_history", "trace_review")
    graph.add_edge("trace_review", "finalize")
    graph.add_edge("backtest_compare", "finalize")
    graph.add_edge("conflict_check", "finalize")
    graph.add_edge("rm_explain", "finalize")
    graph.add_edge("rm_portfolio_prepare", "rm_portfolio_persist")
    graph.add_edge("rm_portfolio_persist", "finalize")
    graph.add_edge("compliance_risk", "finalize")

    graph.add_conditional_edges("executor", _executor_next, {"tools": "tools", "finalize": "finalize", "end": END})
    graph.add_edge("tools", "executor")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=MemorySaver())


def run_agent(
    user_input: str,
    market: str = "a_share",
    role: str = "researcher",
    thread_id: str = "default",
    model_name: str = None,
    verbose: bool = True,
):
    app = build_graph(model_name=model_name)
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "messages": [{"role": "user", "content": user_input}],
        "user_input": user_input,
        "market": market,
        "role": role,
        "client_risk_level": None,
        "task_key": "",
        "route_reason": "",
        "route_confidence": 0.0,
        "data_strategy": "fresh_scan",
        "should_use_tools": True,
        "requires_trace_save": False,
        "execution_plan": "",
        "tool_call_count": 0,
        "last_tool_signature": "",
        "repeated_tool_call_count": 0,
        "stop_reason": "",
        "workflow_context": "",
        "task_payload": {},
        "latest_trace_path": "",
    }
    last_msg_count = 0
    last_state = None
    last_task_key = ""
    last_plan = ""
    last_route_reason = ""

    for state in app.stream(initial_state, config, stream_mode="values"):
        last_state = state
        if verbose and state.get("task_key") and state.get("task_key") != last_task_key:
            print(f"  ≈ 路由: {state['task_key']}", flush=True)
            last_task_key = state["task_key"]
        if verbose and state.get("route_reason") and state.get("route_reason") != last_route_reason:
            confidence = state.get("route_confidence", 0.0)
            strategy = state.get("data_strategy", "unknown")
            tools_flag = "是" if state.get("should_use_tools", True) else "否"
            print(f"  ≈ 路由说明: {state['route_reason']} | strategy={strategy} | tools={tools_flag} | conf={confidence:.2f}", flush=True)
            last_route_reason = state["route_reason"]
        if verbose and state.get("execution_plan") and state.get("execution_plan") != last_plan:
            print("  ≈ 计划已生成", flush=True)
            last_plan = state["execution_plan"]
        msgs = state.get("messages", [])
        if not verbose or len(msgs) <= last_msg_count:
            continue
        for m in msgs[last_msg_count:]:
            if hasattr(m, "tool_calls") and m.tool_calls:
                names = [tc.get("name", "?") for tc in m.tool_calls]
                print(f"  → 调用: {', '.join(names)}", flush=True)
            elif getattr(m, "type", "") == "tool" and hasattr(m, "name"):
                print(f"  ✓ 返回: {m.name}", flush=True)
        last_msg_count = len(msgs)

    if verbose and last_state and last_state.get("stop_reason"):
        print(f"  ≈ 收束: {last_state['stop_reason']}", flush=True)
    return last_state["messages"][-1].content

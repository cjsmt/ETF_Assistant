"""
Main LangGraph wiring for the ETF Rotation Agent.

Pattern coverage (see agent/patterns/*):
- 1 Prompt Chaining      -> prompts/prompt_builder
- 2 Routing              -> router_node + RouterDecision
- 3 Parallelization      -> subgraph.backtest_compare_node / conflict_check_node / multi_agent_debate_node
- 4 Reflection           -> reflect_node
- 5 Tool Use             -> ToolNode + ALL_TOOLS
- 6 Planning             -> planner_node
- 7 Multi-Agent          -> multi_agent_debate (subgraph)
- 8 Memory               -> memory.record_query + memory_snippet injection
- 10 MCP                 -> tools/mcp_tools + mcp_server/
- 11 Goal Setting        -> goal_monitor.init_goal_state / update_goal_progress
- 12 Exception Handling  -> router fallback + tool budget
- 14 RAG                 -> tools/rag_tools
- 15 Inter-agent Comm    -> patterns/inter_agent (pydantic messages)
- 16 Resource-aware      -> resource_tracker + CostCallbackHandler
- 17 Reasoning           -> self_consistency_vote inside multi_agent aggregator
- 18 Guardrails          -> input_guardrail_node / output_guardrail_node
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.patterns.goal_monitor import (
    goal_progress_snippet,
    init_goal_state,
    update_goal_progress,
)
from agent.patterns.guardrails import (
    input_guardrail,
    output_guardrail,
    redact_output,
    request_hitl_approval,
)
from agent.patterns.memory import (
    memory_context_snippet,
    record_query,
    set_last_trace,
)
from agent.patterns.pattern_log import PATTERN_LOG, log_pattern_use
from agent.patterns.reflection import CritiqueReport, run_reflection
from agent.patterns.resource_tracker import (
    CostCallbackHandler,
    NodeTimer,
    RESOURCE_TRACKER,
    resource_snippet,
)
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


def _log_graph(node_name: str, message: str, level: str = "INFO") -> None:
    print(f"[graph][{node_name}][{level}] {message}", flush=True)


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


def _route_after_guardrail(state: AgentState) -> str:
    """If input guardrail blocked, short-circuit to finalize."""
    if state.get("blocked"):
        return "finalize"
    return "router"


def _is_portfolio_task(task_key: str) -> bool:
    return task_key in {
        "research_weekly_report",
        "rm_client_portfolio",
        "research_multi_agent_debate",
    }


def build_graph(model_name: str | None = None) -> StateGraph:
    model = model_name or os.getenv("OPENAI_MODEL", "deepseek-v3.2")

    # --- LLM factory: lazily attaches callback per-thread (Pattern 16) ---
    def llm_for(thread_id: str, *, temperature: float = 0.0, structured: type | None = None):
        callbacks = [CostCallbackHandler(thread_id=thread_id, default_model=model)]
        base = ChatOpenAI(model=model, temperature=temperature, callbacks=callbacks)
        return base.with_structured_output(structured) if structured else base

    tool_node = ToolNode(ALL_TOOLS)

    # -------------------- PATTERN 18 INPUT GUARDRAIL --------------------
    def input_guardrail_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        user_input = _extract_user_input(state)
        _log_graph("input_guardrail", f"checking input ({len(user_input)} chars)")
        result = input_guardrail(user_input, thread_id=thread_id)
        updates: dict = {
            "input_guardrail": result.to_dict(),
            "user_input": user_input,
        }
        if not result.passed:
            _log_graph("input_guardrail", f"BLOCKED: {result.reason}", level="WARN")
            refusal = (
                f"⚠️ 输入未通过合规/安全护栏（风险等级：{result.risk_level}）。\n"
                f"原因：{result.reason}\n\n"
                "本 Agent 仅提供 A 股/港股/美股 ETF 轮动研究与合规辅助，不执行真实交易、"
                "不处理个人隐私、不会忽略系统指令。请调整问题后重试。"
            )
            updates.update(
                {
                    "blocked": True,
                    "stop_reason": "input_guardrail_blocked",
                    "messages": [{"role": "assistant", "content": refusal}],
                }
            )
        else:
            updates["blocked"] = False
        return updates

    # -------------------- PATTERN 8 MEMORY PREP --------------------
    def memory_prep_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        snippet = memory_context_snippet(thread_id)
        if snippet:
            log_pattern_use(thread_id, 8, "Memory", "inject_context", f"{len(snippet)} chars")
        return {"memory_snippet": snippet}

    # -------------------- PATTERN 2 ROUTER --------------------
    def router_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        with NodeTimer(thread_id, "router"):
            user_input = state.get("user_input") or _extract_user_input(state)
            role = state.get("role", "researcher")
            _log_graph("router", f"开始路由，role={role}，market={state.get('market', 'a_share')}")
            log_pattern_use(thread_id, 2, "Routing", "router", "structured_output")

            router_prompt = build_router_prompt(
                role=role,
                market=state.get("market", "a_share"),
                user_input=user_input,
                client_risk_level=state.get("client_risk_level"),
            )
            # inject memory snippet into the router as additional context
            mem_snip = state.get("memory_snippet") or ""
            if mem_snip:
                router_prompt = router_prompt + "\n\n" + mem_snip

            router_llm = llm_for(thread_id, temperature=0, structured=RouterDecision)
            try:
                decision = router_llm.invoke([{"role": "system", "content": router_prompt}])
                task_key = decision.task_key.value
                data_strategy = decision.data_strategy.value
                should_use_tools = decision.should_use_tools
                requires_trace_save = decision.requires_trace_save
                route_confidence = decision.confidence
                route_reason = decision.route_reason
                _log_graph(
                    "router",
                    f"结构化路由完成：task={task_key}，strategy={data_strategy}，"
                    f"tools={should_use_tools}，trace_save={requires_trace_save}，conf={route_confidence:.2f}",
                )
            except Exception as exc:
                log_pattern_use(thread_id, 12, "Exception Handling", "router_fallback", str(exc))
                task_key = infer_task_key(user_input=user_input, role=role)
                lowered = (user_input or "").lower()
                if any(x in lowered for x in ["多 agent", "多agent", "辩论", "debate", "三方", "quant macro risk"]):
                    task_key = "research_multi_agent_debate"
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
                _log_graph("router", f"结构化路由失败，已回退规则路由：{exc}", level="WARN")

            # keyword override to reach multi-agent path if requested
            lowered = (user_input or "").lower()
            if any(x in lowered for x in ["辩论", "三方", "多agent", "多 agent", "debate", "quant macro risk"]):
                task_key = "research_multi_agent_debate"
                should_use_tools = True
                _log_graph("router", "检测到辩论关键词，切换到 research_multi_agent_debate")

            # Pattern 11: initialise goal state immediately after routing
            goal = init_goal_state(task_key, thread_id=thread_id)

            # Pattern 8: persist query to long-term memory
            record_query(
                thread_id=thread_id,
                user_input=user_input,
                task_key=task_key,
                market=state.get("market", "a_share"),
                role=role,
                client_risk_level=state.get("client_risk_level"),
            )

        return {
            "user_input": user_input,
            "task_key": task_key,
            "route_reason": route_reason,
            "route_confidence": route_confidence,
            "data_strategy": data_strategy,
            "should_use_tools": should_use_tools,
            "requires_trace_save": requires_trace_save,
            "stop_reason": "",
            "goal_state": goal.to_dict(),
            "goal_progress": goal.progress(),
        }

    # -------------------- PATTERN 6 PLANNER --------------------
    def planner_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        with NodeTimer(thread_id, "planner"):
            _log_graph("planner", f"开始生成执行计划，task={state.get('task_key', 'generic')}")
            log_pattern_use(thread_id, 6, "Planning", "planner", "generate plan")
            planner_prompt = build_planner_prompt(
                role=state.get("role", "researcher"),
                market=state.get("market", "a_share"),
                task_key=state.get("task_key", "generic"),
                user_input=state.get("user_input", ""),
                data_strategy=state.get("data_strategy", "fresh_scan"),
                requires_trace_save=state.get("requires_trace_save", False),
                client_risk_level=state.get("client_risk_level"),
            )
            if state.get("memory_snippet"):
                planner_prompt += "\n\n" + state["memory_snippet"]
            base_llm = llm_for(thread_id, temperature=0)
            response = base_llm.invoke([{"role": "system", "content": planner_prompt}])
            _log_graph("planner", "执行计划已生成")
        return {"execution_plan": response.content}

    # -------------------- PATTERN 5 EXECUTOR (ReAct tool loop) --------------------
    def executor_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        with NodeTimer(thread_id, "executor"):
            today = datetime.now().strftime("%Y-%m-%d")
            date_hint = f"\n\n【系统信息】当前日期：{today}"
            user_input = _extract_user_input(state)
            _log_graph(
                "executor",
                f"tool_calls_used={state.get('tool_call_count', 0)}/{MAX_TOOL_CALLS}",
            )
            log_pattern_use(thread_id, 5, "Tool Use", "executor", "invoke LLM with tools")

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
            mem_snip = state.get("memory_snippet") or ""
            system_msg_content = (
                system_prompt
                + "\n\n"
                + execution_guidance
                + (("\n\n" + mem_snip) if mem_snip else "")
                + date_hint
            )
            tool_llm = llm_for(thread_id, temperature=0).bind_tools(ALL_TOOLS)
            t0 = time.time()
            response = tool_llm.invoke([{"role": "system", "content": system_msg_content}] + state["messages"])

            updates: dict = {"messages": [response], "stop_reason": ""}
            if hasattr(response, "tool_calls") and response.tool_calls:
                signature = _format_tool_signature(response.tool_calls)
                previous_signature = state.get("last_tool_signature", "")
                repeated_count = state.get("repeated_tool_call_count", 0)
                repeated_count = repeated_count + 1 if signature == previous_signature else 0
                tool_call_count = state.get("tool_call_count", 0) + len(response.tool_calls)
                stop_reason = ""
                if repeated_count >= MAX_REPEATED_TOOL_CALLS:
                    stop_reason = "检测到重复工具调用模式，已提前停止以避免死循环。"
                    log_pattern_use(thread_id, 12, "Exception Handling", "executor", "repeated calls")
                elif tool_call_count >= MAX_TOOL_CALLS:
                    stop_reason = "已达到工具调用预算上限，转入收束回答。"
                    log_pattern_use(thread_id, 16, "Resource-aware", "budget_cap", "tool cap hit")
                _log_graph(
                    "executor",
                    f"本轮产生 {len(response.tool_calls)} 个工具调用，累计 {tool_call_count}/{MAX_TOOL_CALLS}",
                )
                if stop_reason:
                    _log_graph("executor", stop_reason, level="WARN")
                updates.update(
                    {
                        "tool_call_count": tool_call_count,
                        "last_tool_signature": signature,
                        "repeated_tool_call_count": repeated_count,
                        "stop_reason": stop_reason,
                    }
                )
            else:
                _log_graph("executor", "本轮未继续调用工具，准备结束或进入收束")
            RESOURCE_TRACKER.add_node_time(thread_id, "executor_inner", (time.time() - t0) * 1000.0)
        return updates

    # Wrap ToolNode with timing
    def tools_wrapper_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        t0 = time.time()
        last_msg = state["messages"][-1]
        tool_calls = getattr(last_msg, "tool_calls", []) or []
        result = tool_node.invoke(state)
        elapsed = (time.time() - t0) * 1000.0
        for tc in tool_calls:
            tname = tc.get("name", "unknown")
            RESOURCE_TRACKER.add_tool_call(thread_id, tname, elapsed / max(len(tool_calls), 1))
            log_pattern_use(thread_id, 5, "Tool Use", "tool_node", f"{tname} completed")
        return result

    # -------------------- PATTERN 11 GOAL UPDATE --------------------
    def goal_update_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        goal_dict = state.get("goal_state") or {}
        if not goal_dict:
            return {}
        from agent.patterns.goal_monitor import GoalState, SubGoal

        goal = GoalState(
            task_key=goal_dict.get("task_key", ""),
            objective=goal_dict.get("objective", ""),
            sub_goals=[SubGoal(**sg) for sg in goal_dict.get("sub_goals", [])],
            started_at=goal_dict.get("started_at", ""),
            completed_at=goal_dict.get("completed_at", ""),
        )
        goal = update_goal_progress(goal, state.get("task_payload", {}), thread_id=thread_id)
        return {
            "goal_state": goal.to_dict(),
            "goal_progress": goal.progress(),
        }

    # -------------------- PATTERN 12 / finalizer draft --------------------
    def finalizer_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        with NodeTimer(thread_id, "finalize"):
            if state.get("blocked"):
                return {}  # already emitted refusal message

            today = datetime.now().strftime("%Y-%m-%d")
            date_hint = f"\n\n【系统信息】当前日期：{today}"
            _log_graph("finalize", "开始生成最终回答")
            log_pattern_use(thread_id, 6, "Planning", "finalize", "assemble final draft")

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

            # Goal progress block
            goal_dict = state.get("goal_state") or {}
            if goal_dict:
                from agent.patterns.goal_monitor import GoalState, SubGoal

                goal = GoalState(
                    task_key=goal_dict.get("task_key", ""),
                    objective=goal_dict.get("objective", ""),
                    sub_goals=[SubGoal(**sg) for sg in goal_dict.get("sub_goals", [])],
                )
                final_guidance += "\n\n" + goal_progress_snippet(goal)

            if state.get("memory_snippet"):
                final_guidance += "\n\n" + state["memory_snippet"]

            base_llm = llm_for(thread_id, temperature=0)
            response = base_llm.invoke(
                [{"role": "system", "content": system_prompt + "\n\n" + final_guidance + date_hint}]
                + state["messages"]
            )
            _log_graph("finalize", "最终回答已生成")

        # Mark goal as satisfied when we have a final response (generic path)
        updates = {"messages": [response]}
        goal_dict = state.get("goal_state") or {}
        if goal_dict:
            from agent.patterns.goal_monitor import GoalState, SubGoal

            goal = GoalState(
                task_key=goal_dict.get("task_key", ""),
                objective=goal_dict.get("objective", ""),
                sub_goals=[SubGoal(**sg) for sg in goal_dict.get("sub_goals", [])],
                started_at=goal_dict.get("started_at", ""),
                completed_at=goal_dict.get("completed_at", ""),
            )
            final_text = getattr(response, "content", "") or ""
            merged_payload = dict(state.get("task_payload") or {})
            merged_payload["final_response"] = final_text
            goal = update_goal_progress(goal, merged_payload, thread_id=thread_id)
            updates["goal_state"] = goal.to_dict()
            updates["goal_progress"] = goal.progress()
        return updates

    # -------------------- PATTERN 4 REFLECTION --------------------
    def reflect_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        if state.get("blocked"):
            return {}
        last = state["messages"][-1]
        draft = getattr(last, "content", "") or ""
        if len(draft) < 400:
            _log_graph("reflect", "草稿过短，跳过反思")
            return {"reflected": False, "reflection_rounds": []}

        with NodeTimer(thread_id, "reflect"):
            critic_llm = llm_for(thread_id, temperature=0, structured=CritiqueReport)
            revise_llm = llm_for(thread_id, temperature=0)

            def critic_invoke(p: str) -> CritiqueReport:
                return critic_llm.invoke([{"role": "user", "content": p}])

            def revise_invoke(p: str) -> str:
                resp = revise_llm.invoke([{"role": "user", "content": p}])
                return getattr(resp, "content", "") or ""

            result = run_reflection(
                thread_id=thread_id,
                user_input=state.get("user_input", ""),
                role=state.get("role", "researcher"),
                task_key=state.get("task_key", "generic"),
                draft_answer=draft,
                workflow_context=state.get("workflow_context", ""),
                critic_llm_invoke=critic_invoke,
                revise_llm_invoke=revise_invoke,
                max_rounds=1,
            )

        updates = {
            "reflection_rounds": result["rounds"],
            "reflected": result["reflected"],
        }
        if result["reflected"] and result["final_answer"] and result["final_answer"] != draft:
            from langchain_core.messages import AIMessage

            _log_graph("reflect", "草稿已根据 critique 修订")
            updates["messages"] = [AIMessage(content=result["final_answer"])]
        return updates

    # -------------------- PATTERN 18 OUTPUT GUARDRAIL + HITL --------------------
    def output_guardrail_node(state: AgentState):
        thread_id = state.get("thread_id", "default")
        if state.get("blocked"):
            return {}
        last = state["messages"][-1]
        text = getattr(last, "content", "") or ""
        is_portfolio = _is_portfolio_task(state.get("task_key", ""))
        result = output_guardrail(text, is_portfolio=is_portfolio, thread_id=thread_id)
        updates: dict = {"output_guardrail": result.to_dict()}

        if not result.passed:
            from langchain_core.messages import AIMessage

            redacted = redact_output(text)
            safe_msg = (
                f"⚠️ 输出未通过合规护栏（{result.reason}），以下为脱敏版本：\n\n{redacted}\n\n"
                "如需原始内容，请申请人工复核（HITL approval）。"
            )
            updates["messages"] = [AIMessage(content=safe_msg)]
            request_hitl_approval(
                thread_id=thread_id,
                task_key=state.get("task_key", "unknown"),
                payload={
                    "reason": result.reason,
                    "details": result.details,
                    "original_output": text[:4000],
                },
                requester="output_guardrail",
            )

        # HITL for formal deliverables
        if state.get("requires_trace_save") and is_portfolio and result.passed:
            hitl_rec = request_hitl_approval(
                thread_id=thread_id,
                task_key=state.get("task_key", "unknown"),
                payload={
                    "market": state.get("market"),
                    "role": state.get("role"),
                    "user_input": state.get("user_input"),
                    "final_answer": text[:4000],
                    "task_payload": state.get("task_payload", {}),
                },
                requester="finalizer",
            )
            updates["hitl_request"] = hitl_rec

        # Persist resource usage snapshot
        updates["resource_usage"] = RESOURCE_TRACKER.summary(thread_id)

        # remember last trace path for memory
        ltp = state.get("latest_trace_path", "")
        if ltp:
            set_last_trace(thread_id, ltp)

        return updates

    # -------------------- GRAPH WIRING --------------------
    graph = StateGraph(AgentState)

    graph.add_node("input_guardrail", input_guardrail_node)
    graph.add_node("memory_prep", memory_prep_node)
    graph.add_node("router", router_node)
    graph.add_node("planner", planner_node)
    register_subgraph_nodes(graph)
    graph.add_node("executor", executor_node)
    graph.add_node("tools", tools_wrapper_node)
    graph.add_node("goal_update", goal_update_node)
    graph.add_node("finalize", finalizer_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("output_guardrail", output_guardrail_node)

    graph.set_entry_point("input_guardrail")
    graph.add_conditional_edges(
        "input_guardrail",
        lambda s: "finalize" if s.get("blocked") else "memory_prep",
        {"memory_prep": "memory_prep", "finalize": "finalize"},
    )
    graph.add_edge("memory_prep", "router")
    graph.add_edge("router", "planner")
    graph.add_conditional_edges("planner", route_after_planner, SUBGRAPH_EDGE_MAP)

    # All subgraph nodes flow into goal_update -> finalize
    graph.add_edge("weekly_prepare", "weekly_persist")
    graph.add_edge("weekly_persist", "goal_update")
    graph.add_edge("trace_history", "trace_review")
    graph.add_edge("trace_review", "goal_update")
    graph.add_edge("backtest_compare", "goal_update")
    graph.add_edge("conflict_check", "goal_update")
    graph.add_edge("rm_explain", "goal_update")
    graph.add_edge("rm_portfolio_prepare", "rm_portfolio_persist")
    graph.add_edge("rm_portfolio_persist", "goal_update")
    graph.add_edge("compliance_risk", "goal_update")
    graph.add_edge("multi_agent_debate", "goal_update")

    graph.add_edge("goal_update", "finalize")

    graph.add_conditional_edges(
        "executor",
        _executor_next,
        {"tools": "tools", "finalize": "finalize", "end": "finalize"},
    )
    graph.add_edge("tools", "executor")

    graph.add_edge("finalize", "reflect")
    graph.add_edge("reflect", "output_guardrail")
    graph.add_edge("output_guardrail", END)

    return graph.compile(checkpointer=MemorySaver())


def run_agent(
    user_input: str,
    market: str = "a_share",
    role: str = "researcher",
    thread_id: str = "default",
    model_name: str | None = None,
    verbose: bool = True,
    client_risk_level: str | None = None,
    return_state: bool = False,
):
    """Run the agent. Returns either the final string, or the full final state when
    ``return_state=True`` (used by the Streamlit frontend for pattern panels)."""
    RESOURCE_TRACKER.reset(thread_id)
    PATTERN_LOG.clear(thread_id)

    app = build_graph(model_name=model_name)
    config = {"configurable": {"thread_id": thread_id}}
    initial_state: AgentState = {
        "messages": [{"role": "user", "content": user_input}],
        "user_input": user_input,
        "market": market,
        "role": role,
        "client_risk_level": client_risk_level,
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
        "thread_id": thread_id,
        "goal_state": {},
        "goal_progress": 0.0,
        "input_guardrail": {},
        "output_guardrail": {},
        "hitl_request": {},
        "blocked": False,
        "memory_snippet": "",
        "reflection_rounds": [],
        "reflected": False,
        "debate_result": {},
        "mcp_enabled": True,
        "resource_usage": {},
        "extra": {},
    }

    last_state = None
    last_task_key = ""
    last_plan = ""
    last_route_reason = ""
    last_msg_count = 0

    for state in app.stream(initial_state, config, stream_mode="values"):
        last_state = state
        if verbose and state.get("task_key") and state.get("task_key") != last_task_key:
            _log_graph("stream", f"当前任务路由：{state['task_key']}")
            last_task_key = state["task_key"]
        if verbose and state.get("route_reason") and state.get("route_reason") != last_route_reason:
            confidence = state.get("route_confidence", 0.0)
            strategy = state.get("data_strategy", "unknown")
            tools_flag = "是" if state.get("should_use_tools", True) else "否"
            _log_graph(
                "stream",
                f"路由说明：{state['route_reason']} | strategy={strategy} | tools={tools_flag} | conf={confidence:.2f}",
            )
            last_route_reason = state["route_reason"]
        if verbose and state.get("execution_plan") and state.get("execution_plan") != last_plan:
            _log_graph("stream", "执行计划已进入状态")
            last_plan = state["execution_plan"]
        msgs = state.get("messages", [])
        if not verbose or len(msgs) <= last_msg_count:
            continue
        for m in msgs[last_msg_count:]:
            if hasattr(m, "tool_calls") and m.tool_calls:
                names = [tc.get("name", "?") for tc in m.tool_calls]
                _log_graph("tools", f"触发工具调用：{', '.join(names)}")
            elif getattr(m, "type", "") == "tool" and hasattr(m, "name"):
                _log_graph("tools", f"工具返回：{m.name}")
        last_msg_count = len(msgs)

    if verbose and last_state and last_state.get("stop_reason"):
        _log_graph("stream", f"收束原因：{last_state['stop_reason']}", level="WARN")

    if last_state is None:
        return ""
    final_msg = last_state["messages"][-1]
    final_content = getattr(final_msg, "content", str(final_msg))
    if return_state:
        return {
            "final_answer": final_content,
            "state": last_state,
            "pattern_events": PATTERN_LOG.get(thread_id),
            "pattern_summary": PATTERN_LOG.summary(thread_id),
            "resource_usage": RESOURCE_TRACKER.summary(thread_id),
        }
    return final_content

import json
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from agent.state import AgentState
from tools.backtest_tools import run_backtest
from tools.data_tools import get_market_data
from tools.factor_tools import calc_factors_df
from tools.filter_tools import get_ic_overlay_config
from tools.mapping_tools import map_etf
from tools.news_tools import search_news_cn
from tools.report_tools import generate_report
from tools.scoring_tools import score_quadrant_df
from tools.trace_tools import TRACE_DIR, get_decision_history, save_decision_trace


SubgraphRoute = Literal[
    "weekly_prepare",
    "trace_history",
    "backtest_compare",
    "conflict_check",
    "rm_explain",
    "rm_portfolio_prepare",
    "compliance_risk",
    "executor",
    "finalize",
]

QUADRANT_ORDER = ["黄金配置区", "左侧观察区", "高危警示区", "垃圾规避区"]
TASK_ROUTE_MAP: dict[str, SubgraphRoute] = {
    "research_weekly_report": "weekly_prepare",
    "compliance_trace_review": "trace_history",
    "research_backtest_compare": "backtest_compare",
    "research_conflict_check": "conflict_check",
    "rm_explain_performance": "rm_explain",
    "rm_client_portfolio": "rm_portfolio_prepare",
    "compliance_risk_check": "compliance_risk",
}
SUBGRAPH_EDGE_MAP = {route: route for route in TASK_ROUTE_MAP.values()}
SUBGRAPH_EDGE_MAP.update({"executor": "executor", "finalize": "finalize"})


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _date_range(days: int) -> tuple[str, str]:
    end_date = _today()
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return start_date, end_date


def _stringify_tool_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


def _safe_invoke_tool(tool_obj, payload: dict) -> str:
    try:
        return _stringify_tool_result(tool_obj.invoke(payload))
    except Exception as exc:
        return f"Tool error: {exc}"


def _latest_trace_file() -> str:
    if not os.path.isdir(TRACE_DIR):
        return ""

    latest_path = ""
    latest_mtime = -1.0
    for date_folder in sorted(os.listdir(TRACE_DIR), reverse=True):
        folder = os.path.join(TRACE_DIR, date_folder)
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            if not (fname.startswith("trace_") and fname.endswith(".json")):
                continue
            path = os.path.join(folder, fname)
            mtime = os.path.getmtime(path)
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_path = path
    return latest_path


def _extract_keywords(text: str, max_keywords: int = 3) -> list[str]:
    normalized = text or ""
    for separator in ["，", ",", " ", "；", ";", "\n", "、"]:
        normalized = normalized.replace(separator, "|")

    unique_keywords: list[str] = []
    for word in (part.strip() for part in normalized.split("|")):
        if len(word) < 2 or word in unique_keywords:
            continue
        unique_keywords.append(word)
        if len(unique_keywords) >= max_keywords:
            break
    return unique_keywords


def _summarize_quadrants(quadrant_df) -> tuple[dict[str, list[str]], str]:
    quadrant_distribution: dict[str, list[str]] = {}
    summary_lines = []
    for label in QUADRANT_ORDER:
        items = quadrant_df[quadrant_df["quadrant"] == label]["industry"].tolist()
        quadrant_distribution[label] = items
        preview = "、".join(items[:8]) if items else "无"
        summary_lines.append(f"- {label}（{len(items)}）：{preview}")
    return quadrant_distribution, "\n".join(summary_lines)


def _concat_sections(*sections: str) -> str:
    return "\n\n".join(section for section in sections if section)


def _bump_tool_count(state: AgentState, increment: int) -> int:
    return state.get("tool_call_count", 0) + increment


def route_after_planner(state: AgentState) -> SubgraphRoute:
    if not state.get("should_use_tools", True):
        return "finalize"
    return TASK_ROUTE_MAP.get(state.get("task_key", "generic"), "executor")


def weekly_prepare_node(state: AgentState):
    market = state.get("market", "a_share")
    start_date, end_date = _date_range(days=365)

    market_data_summary = _safe_invoke_tool(
        get_market_data,
        {"market": market, "start_date": start_date, "end_date": end_date},
    )
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        return {
            "workflow_context": "固定周报子图执行失败：未计算出有效因子数据。",
            "task_payload": {},
            "stop_reason": "周报子图未获得有效因子数据。",
        }

    factor_summary = factor_df[
        ["industry", "trend_score", "consensus_score"]
    ].sort_values("trend_score", ascending=False).to_string(index=False)
    quadrant_df = score_quadrant_df(factor_df)
    quadrant_distribution, quadrant_summary = _summarize_quadrants(quadrant_df)
    overlay_text = _safe_invoke_tool(get_ic_overlay_config, {"market": market})
    preferred_industries = quadrant_df[
        quadrant_df["quadrant"].isin(["黄金配置区", "左侧观察区"])
    ]["industry"].head(6).tolist()
    etf_mapping_text = (
        _safe_invoke_tool(
            map_etf,
            {"industries": ",".join(preferred_industries), "market": market},
        )
        if preferred_industries
        else "无可映射行业。"
    )

    payload = {
        "decision_date": end_date,
        "market": market,
        "data_period": f"{start_date} to {end_date}",
        "quadrant_distribution": quadrant_distribution,
        "observation_pool_filter": overlay_text,
        "portfolio_recommendation": etf_mapping_text,
        "factor_summary": factor_summary,
        "config_version": "fixed-workflow-v1",
        "timestamp": end_date,
        "approval_status": "pending",
    }
    workflow_context = _concat_sections(
        "## 固定子图：投研周报主干结果",
        f"- 市场：{market}\n- 数据区间：{start_date} 至 {end_date}",
        "### 行情抓取摘要\n" + market_data_summary,
        "### 因子摘要（按趋势分排序）\n" + factor_summary,
        "### 四象限分布\n" + quadrant_summary,
        "### 观察池与负面清单\n" + overlay_text,
        "### 候选 ETF 映射\n" + etf_mapping_text,
    )
    return {
        "workflow_context": workflow_context,
        "task_payload": payload,
        "tool_call_count": _bump_tool_count(state, 5),
        "stop_reason": "",
    }


def weekly_persist_node(state: AgentState):
    payload = dict(state.get("task_payload", {}))
    workflow_context = state.get("workflow_context", "")
    report_preview = _safe_invoke_tool(
        generate_report,
        {
            "report_data": json.dumps(payload, ensure_ascii=False, indent=2),
            "template_type": "weekly_report",
            "role": state.get("role", "researcher"),
        },
    )

    save_result = "本次任务未要求保存 Decision Trace。"
    if state.get("requires_trace_save", False) and payload:
        save_result = _safe_invoke_tool(
            save_decision_trace,
            {"trace_json": json.dumps(payload, ensure_ascii=False)},
        )

    return {
        "workflow_context": _concat_sections(
            workflow_context,
            "### 周报模板预览\n" + report_preview,
            "### Trace 保存结果\n" + save_result,
        )
    }


def trace_history_node(state: AgentState):
    history_text = _safe_invoke_tool(get_decision_history, {"days": 7})
    latest_path = _latest_trace_file()
    if not latest_path:
        return {
            "workflow_context": "## 固定子图：合规 Trace 审查\n未找到任何可审查的 trace 文件。",
            "latest_trace_path": "",
            "task_payload": {},
            "stop_reason": "最近无 trace 可供审查。",
        }

    return {
        "workflow_context": _concat_sections(
            "## 固定子图：合规 Trace 审查",
            "### 最近 7 天 Trace 概览\n" + history_text,
        ),
        "latest_trace_path": latest_path,
        "stop_reason": "",
    }


def trace_review_node(state: AgentState):
    latest_path = state.get("latest_trace_path", "")
    workflow_context = state.get("workflow_context", "")
    if not latest_path or not os.path.isfile(latest_path):
        return {
            "workflow_context": _concat_sections(workflow_context, "未找到最新 trace 文件，无法继续审查。"),
            "stop_reason": "最新 trace 文件缺失。",
        }

    with open(latest_path, "r", encoding="utf-8") as file_obj:
        trace = json.load(file_obj)

    required_keys = [
        "decision_date",
        "market",
        "quadrant_distribution",
        "portfolio_recommendation",
        "risk_checks",
        "config_version",
        "approval_status",
    ]
    missing_keys = [key for key in required_keys if key not in trace]
    present_keys = [key for key in required_keys if key in trace]
    review_lines = [
        f"- 最新 trace 路径：{latest_path}",
        f"- 已包含关键字段：{', '.join(present_keys) if present_keys else '无'}",
        f"- 缺失关键字段：{', '.join(missing_keys) if missing_keys else '无'}",
        f"- 审批状态：{trace.get('approval_status', 'N/A')}",
        f"- 数据时间：{trace.get('decision_date', trace.get('timestamp', 'N/A'))}",
    ]
    if "risk_checks" in trace:
        review_lines.append(
            f"- 风险检查摘要：{json.dumps(trace.get('risk_checks'), ensure_ascii=False)}"
        )
    if "quadrant_distribution" in trace:
        review_lines.append(
            f"- 四象限摘要：{json.dumps(trace.get('quadrant_distribution'), ensure_ascii=False)}"
        )

    return {
        "workflow_context": _concat_sections(
            workflow_context,
            "### 最新 Trace 详细审查\n" + "\n".join(review_lines),
            "### 最新 Trace 原始内容\n" + json.dumps(trace, ensure_ascii=False, indent=2),
        ),
        "task_payload": trace,
    }


def backtest_compare_node(state: AgentState):
    market = state.get("market", "a_share")
    start_date, end_date = _date_range(days=730)
    monthly = _safe_invoke_tool(
        run_backtest,
        {"market": market, "start_date": start_date, "end_date": end_date, "rebalance_freq": "monthly"},
    )
    weekly = _safe_invoke_tool(
        run_backtest,
        {"market": market, "start_date": start_date, "end_date": end_date, "rebalance_freq": "weekly"},
    )
    payload = {
        "market": market,
        "start_date": start_date,
        "end_date": end_date,
        "monthly": monthly,
        "weekly": weekly,
    }
    return {
        "workflow_context": _concat_sections(
            "## 固定子图：参数回测对比（research_backtest_compare）",
            f"- 市场：{market}\n- 区间：{start_date} 至 {end_date}",
            "### Monthly 回测结果\n" + monthly,
            "### Weekly 回测结果\n" + weekly,
        ),
        "task_payload": payload,
        "tool_call_count": _bump_tool_count(state, 2),
    }


def conflict_check_node(state: AgentState):
    market = state.get("market", "a_share")
    start_date, end_date = _date_range(days=365)
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        return {
            "workflow_context": "固定冲突检查子图执行失败：因子为空。",
            "stop_reason": "冲突检查无有效因子数据。",
        }

    quadrant_df = score_quadrant_df(factor_df)
    golden = quadrant_df[quadrant_df["quadrant"] == "黄金配置区"]["industry"].head(3).tolist()
    overlay_text = _safe_invoke_tool(get_ic_overlay_config, {"market": market})
    news_parts = []
    for industry in golden:
        news = _safe_invoke_tool(search_news_cn, {"keywords": industry, "limit": 5})
        news_parts.append(f"#### {industry}\n{news}")

    payload = {
        "market": market,
        "golden_industries": golden,
        "overlay": overlay_text,
        "news": news_parts,
    }
    return {
        "workflow_context": _concat_sections(
            "## 固定子图：信号冲突检查（research_conflict_check）",
            f"- 市场：{market}\n- 黄金区候选：{'、'.join(golden) if golden else '无'}",
            "### 观察池 / 负面清单\n" + overlay_text,
            "### 重点行业新闻核验\n" + ("\n\n".join(news_parts) if news_parts else "无可核验行业。"),
        ),
        "task_payload": payload,
        "tool_call_count": _bump_tool_count(state, 2 + len(golden)),
    }


def rm_explain_node(state: AgentState):
    history = _safe_invoke_tool(get_decision_history, {"days": 14})
    keywords = _extract_keywords(state.get("user_input", ""), max_keywords=2)
    news_chunks = []
    for keyword in keywords:
        news_chunks.append(
            f"#### 关键词：{keyword}\n"
            f"{_safe_invoke_tool(search_news_cn, {'keywords': keyword, 'limit': 5})}"
        )

    payload = {"history": history, "keywords": keywords, "news": news_chunks}
    return {
        "workflow_context": _concat_sections(
            "## 固定子图：RM 业绩解释（rm_explain_performance）",
            "### 最近决策历史\n" + history,
            "### 相关事件补充\n" + ("\n\n".join(news_chunks) if news_chunks else "未提取到有效关键词，跳过新闻补充。"),
        ),
        "task_payload": payload,
        "tool_call_count": _bump_tool_count(state, 1 + len(news_chunks)),
    }


def rm_portfolio_prepare_node(state: AgentState):
    market = state.get("market", "a_share")
    start_date, end_date = _date_range(days=365)
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        return {
            "workflow_context": "固定 RM 组合子图执行失败：未获取有效因子。",
            "stop_reason": "RM 组合任务因子为空。",
        }

    quadrant_df = score_quadrant_df(factor_df)
    picks = quadrant_df[
        quadrant_df["quadrant"].isin(["黄金配置区", "左侧观察区"])
    ]["industry"].head(4).tolist()
    mapped = (
        _safe_invoke_tool(map_etf, {"industries": ",".join(picks), "market": market})
        if picks
        else "无可映射行业。"
    )
    risk_level = state.get("client_risk_level") or "未提供（默认中性口径）"
    payload = {
        "market": market,
        "risk_level": risk_level,
        "industries": picks,
        "mapped": mapped,
    }
    return {
        "workflow_context": _concat_sections(
            "## 固定子图：RM 客户组合（rm_client_portfolio）",
            f"- 市场：{market}\n- 客户风险等级：{risk_level}\n- 候选行业：{'、'.join(picks) if picks else '无'}",
            "### ETF 映射结果\n" + mapped,
        ),
        "task_payload": payload,
        "tool_call_count": _bump_tool_count(state, 2),
    }


def rm_portfolio_persist_node(state: AgentState):
    payload = dict(state.get("task_payload", {}))
    workflow_context = state.get("workflow_context", "")
    if not (state.get("requires_trace_save", False) and payload):
        return {"workflow_context": workflow_context}

    trace = {
        "decision_date": _today(),
        "market": payload.get("market", state.get("market", "a_share")),
        "portfolio_recommendation": payload.get("mapped", ""),
        "risk_level": payload.get("risk_level", "未提供"),
        "industries": payload.get("industries", []),
        "config_version": "fixed-workflow-v1",
        "approval_status": "pending",
    }
    save_result = _safe_invoke_tool(
        save_decision_trace,
        {"trace_json": json.dumps(trace, ensure_ascii=False)},
    )
    return {
        "workflow_context": _concat_sections(
            workflow_context,
            "### Trace 保存结果\n" + save_result,
        )
    }


def compliance_risk_node(state: AgentState):
    latest_path = _latest_trace_file()
    if not latest_path or not os.path.isfile(latest_path):
        return {
            "workflow_context": "## 固定子图：合规风险检查\n未找到可审查的 trace。",
            "stop_reason": "风险检查缺少 trace 输入。",
        }

    with open(latest_path, "r", encoding="utf-8") as file_obj:
        trace = json.load(file_obj)

    risk_checks = trace.get("risk_checks", {})
    concentration = risk_checks.get("concentration_risk", "未提供")
    liquidity = risk_checks.get("liquidity_risk", "未提供")
    macro = risk_checks.get("macro_risks", [])
    sector = risk_checks.get("sector_risks", [])
    missing = [
        key for key in ["risk_checks", "portfolio_recommendation", "approval_status"] if key not in trace
    ]
    return {
        "workflow_context": _concat_sections(
            "## 固定子图：合规风险检查（compliance_risk_check）",
            (
                f"- 审查 trace：{latest_path}\n"
                f"- 审批状态：{trace.get('approval_status', 'N/A')}\n"
                f"- 集中度风险：{concentration}\n"
                f"- 流动性风险：{liquidity}\n"
                f"- 宏观风险：{'；'.join(macro) if macro else '无'}\n"
                f"- 行业风险：{'；'.join(sector) if sector else '无'}\n"
                f"- 缺失关键字段：{', '.join(missing) if missing else '无'}"
            ),
        ),
        "task_payload": trace,
    }


SUBGRAPH_NODES: dict[str, Callable[[AgentState], dict[str, Any]]] = {
    "weekly_prepare": weekly_prepare_node,
    "weekly_persist": weekly_persist_node,
    "trace_history": trace_history_node,
    "trace_review": trace_review_node,
    "backtest_compare": backtest_compare_node,
    "conflict_check": conflict_check_node,
    "rm_explain": rm_explain_node,
    "rm_portfolio_prepare": rm_portfolio_prepare_node,
    "rm_portfolio_persist": rm_portfolio_persist_node,
    "compliance_risk": compliance_risk_node,
}


def register_subgraph_nodes(graph):
    for node_name, node_fn in SUBGRAPH_NODES.items():
        graph.add_node(node_name, node_fn)

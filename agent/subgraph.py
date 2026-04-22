import concurrent.futures as _futures
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from agent.state import AgentState
from agent.patterns.pattern_log import log_pattern_use
from agent.patterns.goal_monitor import init_goal_state, update_goal_progress
from agent.patterns.multi_agent import DebateInputs, run_debate_parallel
from agent.patterns.memory import memory_context_snippet
from tools.backtest_tools import run_backtest
from tools.data_tools import get_market_data
from tools.factor_tools import calc_factors_df
from tools.filter_tools import get_ic_overlay_config
from tools.mapping_tools import map_etf
from tools.news_tools import search_news_cn, get_macro_events
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
    "multi_agent_debate",
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
    "research_multi_agent_debate": "multi_agent_debate",
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


def _split_items(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[，,；;、/\n]+", raw or "") if item.strip()]


def _extract_code(text: str) -> str:
    match = re.search(r"\b(\d{6})\b", text or "")
    return match.group(1) if match else "-"


def _parse_overlay_text(overlay_text: str) -> tuple[dict[str, list[str]], list[str]]:
    observation_pool = {"export_chain": [], "policy_chain": [], "defensive": []}
    veto_list: list[str] = []
    current_section = ""
    active_veto = False

    for raw_line in overlay_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            label = line[1:-1]
            if "出口" in label:
                current_section = "export_chain"
                active_veto = False
            elif "政策" in label:
                current_section = "policy_chain"
                active_veto = False
            elif "防守" in label or "防御" in label:
                current_section = "defensive"
                active_veto = False
            else:
                current_section = ""
                active_veto = "(ACTIVE)" in label
            continue

        if line.startswith("Industries:"):
            industries = _split_items(line.split(":", 1)[1])
            if current_section:
                observation_pool[current_section].extend(industries)
            elif active_veto:
                veto_list.extend(industries)

    for key, values in observation_pool.items():
        observation_pool[key] = list(dict.fromkeys(values))
    veto_list = list(dict.fromkeys(veto_list))
    return observation_pool, veto_list


def _parse_mapping_text(
    mapping_text: str,
    offensive_industries: list[str] | None = None,
    allocation_industries: list[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    offensive_set = set(offensive_industries or [])
    allocation_set = set(allocation_industries or [])
    portfolio = {"offensive_layer": [], "allocation_layer": [], "defensive_layer": []}

    for raw_line in mapping_text.splitlines():
        line = raw_line.strip()
        if not line or " -> " not in line:
            continue

        sector, remainder = line.split(" -> ", 1)
        status_match = re.search(r"\[(PASS|FAIL)\]", remainder)
        detail_match = re.search(r"\(([^()]*)\)(?:\s+\|\s+Alternatives:.*)?$", remainder)
        etf_text = remainder[: status_match.start()].strip() if status_match else remainder.strip()
        rationale_parts = []
        if status_match:
            rationale_parts.append(f"流动性筛选 {status_match.group(1)}")
        if detail_match:
            rationale_parts.append(detail_match.group(1).strip())

        row = {
            "sector": sector.strip(),
            "weight": "-",
            "etf": etf_text or "待确认",
            "code": _extract_code(etf_text),
            "rationale": "；".join(part for part in rationale_parts if part) or "ETF 映射结果",
        }

        if row["sector"] in offensive_set:
            portfolio["offensive_layer"].append(row)
        elif row["sector"] in allocation_set:
            portfolio["allocation_layer"].append(row)
        else:
            portfolio["allocation_layer"].append(row)

    return portfolio


def _summarize_quadrants(quadrant_df) -> tuple[dict[str, list[str]], str]:
    quadrant_key_map = {
        "黄金配置区": "golden_zone",
        "左侧观察区": "left_side_zone",
        "高危警示区": "high_risk_zone",
        "垃圾规避区": "garbage_zone",
    }
    quadrant_distribution: dict[str, list[str]] = {}
    summary_lines = []
    for label in QUADRANT_ORDER:
        items = quadrant_df[quadrant_df["quadrant"] == label]["industry"].tolist()
        quadrant_distribution[quadrant_key_map[label]] = items
        preview = "、".join(items[:8]) if items else "无"
        summary_lines.append(f"- {label}（{len(items)}）：{preview}")
    return quadrant_distribution, "\n".join(summary_lines)


def _concat_sections(*sections: str) -> str:
    return "\n\n".join(section for section in sections if section)


def _bump_tool_count(state: AgentState, increment: int) -> int:
    return state.get("tool_call_count", 0) + increment


def _log_node(node_name: str, message: str, level: str = "INFO") -> None:
    print(f"[subgraph][{node_name}][{level}] {message}", flush=True)


def _log_step(node_name: str, step: int, total: int, message: str) -> None:
    _log_node(node_name, f"步骤 {step}/{total}: {message}")


def route_after_planner(state: AgentState) -> SubgraphRoute:
    if not state.get("should_use_tools", True):
        return "finalize"
    return TASK_ROUTE_MAP.get(state.get("task_key", "generic"), "executor")


def weekly_prepare_node(state: AgentState):
    market = state.get("market", "a_share")
    start_date, end_date = _date_range(days=365)
    _log_node("weekly_prepare", f"开始生成周报主干，market={market}，区间={start_date}~{end_date}")

    _log_step("weekly_prepare", 1, 5, "抓取市场数据摘要")
    market_data_summary = _safe_invoke_tool(
        get_market_data,
        {"market": market, "start_date": start_date, "end_date": end_date},
    )
    _log_node("weekly_prepare", "步骤 1/5 完成")

    _log_step("weekly_prepare", 2, 5, "计算行业因子")
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        _log_node("weekly_prepare", "步骤 2/5 失败：未计算出有效因子数据", level="WARN")
        return {
            "workflow_context": "固定周报子图执行失败：未计算出有效因子数据。",
            "task_payload": {},
            "stop_reason": "周报子图未获得有效因子数据。",
        }
    _log_node("weekly_prepare", f"步骤 2/5 完成：获得 {len(factor_df)} 个行业因子")

    factor_summary = factor_df[
        ["industry", "trend_score", "consensus_score"]
    ].sort_values("trend_score", ascending=False).to_string(index=False)

    _log_step("weekly_prepare", 3, 5, "划分四象限")
    quadrant_df = score_quadrant_df(factor_df)
    quadrant_distribution, quadrant_summary = _summarize_quadrants(quadrant_df)
    _log_node("weekly_prepare", "步骤 3/5 完成")

    _log_step("weekly_prepare", 4, 5, "读取观察池与负面清单")
    overlay_text = _safe_invoke_tool(get_ic_overlay_config, {"market": market})
    _log_node("weekly_prepare", "步骤 4/5 完成")
    preferred_industries = quadrant_df[
        quadrant_df["quadrant"].isin(["黄金配置区", "左侧观察区"])
    ]["industry"].head(6).tolist()
    offensive_industries = quadrant_df[quadrant_df["quadrant"] == "黄金配置区"]["industry"].head(3).tolist()
    allocation_industries = quadrant_df[quadrant_df["quadrant"] == "左侧观察区"]["industry"].head(3).tolist()
    etf_mapping_text = (
        _safe_invoke_tool(
            map_etf,
            {"industries": ",".join(preferred_industries), "market": market},
        )
        if preferred_industries
        else "无可映射行业。"
    )
    _log_node("weekly_prepare", f"步骤 5/5 完成：ETF 映射候选 {len(preferred_industries)} 个行业")
    observation_pool, veto_list = _parse_overlay_text(overlay_text)
    portfolio_recommendation = _parse_mapping_text(
        etf_mapping_text,
        offensive_industries=offensive_industries,
        allocation_industries=allocation_industries,
    )

    payload = {
        "decision_date": end_date,
        "market": market,
        "data_period": f"{start_date} to {end_date}",
        "quadrant_distribution": quadrant_distribution,
        "observation_pool_filter": observation_pool,
        "veto_list_exclusions": veto_list,
        "portfolio_recommendation": portfolio_recommendation,
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
    _log_node("weekly_persist", "开始生成周报模板预览")
    report_preview = _safe_invoke_tool(
        generate_report,
        {
            "report_data": json.dumps(payload, ensure_ascii=False, indent=2),
            "template_type": "weekly_report",
            "role": state.get("role", "researcher"),
        },
    )
    _log_node("weekly_persist", "模板预览已生成")

    save_result = "本次任务未要求保存 Decision Trace。"
    if state.get("requires_trace_save", False) and payload:
        _log_node("weekly_persist", "开始保存 Decision Trace")
        save_result = _safe_invoke_tool(
            save_decision_trace,
            {"trace_json": json.dumps(payload, ensure_ascii=False)},
        )
        _log_node("weekly_persist", "Decision Trace 已保存")
    else:
        _log_node("weekly_persist", "当前任务未要求保存 Trace", level="WARN")

    return {
        "workflow_context": _concat_sections(
            workflow_context,
            "### 周报模板预览\n" + report_preview,
            "### Trace 保存结果\n" + save_result,
        )
    }


def trace_history_node(state: AgentState):
    _log_step("trace_history", 1, 2, "读取最近 7 天 Trace 历史")
    history_text = _safe_invoke_tool(get_decision_history, {"days": 7})
    _log_step("trace_history", 2, 2, "定位最新 Trace 文件")
    latest_path = _latest_trace_file()
    if not latest_path:
        _log_node("trace_history", "未找到任何可审查的 Trace 文件", level="WARN")
        return {
            "workflow_context": "## 固定子图：合规 Trace 审查\n未找到任何可审查的 trace 文件。",
            "latest_trace_path": "",
            "task_payload": {},
            "stop_reason": "最近无 trace 可供审查。",
        }
    _log_node("trace_history", f"已定位最新 Trace: {latest_path}")

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
        _log_node("trace_review", "最新 Trace 文件缺失，无法继续审查", level="WARN")
        return {
            "workflow_context": _concat_sections(workflow_context, "未找到最新 trace 文件，无法继续审查。"),
            "stop_reason": "最新 trace 文件缺失。",
        }

    _log_node("trace_review", f"开始审查 Trace: {latest_path}")
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
    _log_node("trace_review", f"审查完成：缺失字段 {len(missing_keys)} 个")

    return {
        "workflow_context": _concat_sections(
            workflow_context,
            "### 最新 Trace 详细审查\n" + "\n".join(review_lines),
            "### 最新 Trace 原始内容\n" + json.dumps(trace, ensure_ascii=False, indent=2),
        ),
        "task_payload": trace,
    }


def backtest_compare_node(state: AgentState):
    """Pattern 3: Parallelization — run monthly + weekly backtests concurrently."""
    market = state.get("market", "a_share")
    thread_id = state.get("thread_id", "default")
    start_date, end_date = _date_range(days=730)
    _log_node("backtest_compare", f"开始参数回测对比（并行），market={market}，区间={start_date}~{end_date}")
    log_pattern_use(
        thread_id, 3, "Parallelization", "backtest_compare", "fan_out monthly+weekly"
    )

    freqs = [("monthly", "月频回测"), ("weekly", "周频回测")]
    results: dict[str, str] = {}
    with _futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(
                _safe_invoke_tool,
                run_backtest,
                {"market": market, "start_date": start_date, "end_date": end_date, "rebalance_freq": freq},
            ): freq
            for freq, _ in freqs
        }
        for fut in _futures.as_completed(futures):
            freq = futures[fut]
            results[freq] = fut.result()
            _log_node("backtest_compare", f"{freq} 回测完成")

    monthly, weekly = results.get("monthly", ""), results.get("weekly", "")
    _log_node("backtest_compare", "回测对比完成（并行）")
    payload = {
        "market": market,
        "start_date": start_date,
        "end_date": end_date,
        "monthly": monthly,
        "weekly": weekly,
        "executed_in_parallel": True,
    }
    return {
        "workflow_context": _concat_sections(
            "## 固定子图：参数回测对比（research_backtest_compare, 并行执行）",
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
    _log_step("conflict_check", 1, 3, "计算行业因子")
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        _log_node("conflict_check", "未获得有效因子数据", level="WARN")
        return {
            "workflow_context": "固定冲突检查子图执行失败：因子为空。",
            "stop_reason": "冲突检查无有效因子数据。",
        }

    _log_step("conflict_check", 2, 3, "读取观察池与负面清单")
    quadrant_df = score_quadrant_df(factor_df)
    golden = quadrant_df[quadrant_df["quadrant"] == "黄金配置区"]["industry"].head(3).tolist()
    overlay_text = _safe_invoke_tool(get_ic_overlay_config, {"market": market})
    _log_step("conflict_check", 3, 3, f"核验重点行业新闻（并行），共 {len(golden)} 个")
    thread_id = state.get("thread_id", "default")
    if golden:
        log_pattern_use(
            thread_id, 3, "Parallelization", "conflict_check", f"fan_out news for {len(golden)} sectors"
        )
    news_parts: list[str] = [""] * len(golden)
    with _futures.ThreadPoolExecutor(max_workers=min(5, max(len(golden), 1))) as pool:
        futures = {
            pool.submit(_safe_invoke_tool, search_news_cn, {"keywords": ind, "limit": 5}): idx
            for idx, ind in enumerate(golden)
        }
        for fut in _futures.as_completed(futures):
            idx = futures[fut]
            try:
                news_parts[idx] = f"#### {golden[idx]}\n{fut.result()}"
            except Exception as exc:
                news_parts[idx] = f"#### {golden[idx]}\nerror: {exc}"
    _log_node("conflict_check", "信号冲突检查完成（并行）")

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
    _log_step("rm_explain", 1, 2, "读取最近决策历史")
    history = _safe_invoke_tool(get_decision_history, {"days": 14})
    keywords = _extract_keywords(state.get("user_input", ""), max_keywords=2)
    _log_step("rm_explain", 2, 2, f"补充相关新闻，共 {len(keywords)} 个关键词")
    news_chunks = []
    for keyword in keywords:
        _log_node("rm_explain", f"正在检索关键词：{keyword}")
        news_chunks.append(
            f"#### 关键词：{keyword}\n"
            f"{_safe_invoke_tool(search_news_cn, {'keywords': keyword, 'limit': 5})}"
        )
    _log_node("rm_explain", "RM 业绩解释上下文准备完成")

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
    _log_step("rm_portfolio_prepare", 1, 3, "计算行业因子")
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        _log_node("rm_portfolio_prepare", "未获取有效因子数据", level="WARN")
        return {
            "workflow_context": "固定 RM 组合子图执行失败：未获取有效因子。",
            "stop_reason": "RM 组合任务因子为空。",
        }

    _log_step("rm_portfolio_prepare", 2, 3, "筛选候选行业")
    quadrant_df = score_quadrant_df(factor_df)
    picks = quadrant_df[
        quadrant_df["quadrant"].isin(["黄金配置区", "左侧观察区"])
    ]["industry"].head(4).tolist()
    _log_step("rm_portfolio_prepare", 3, 3, f"映射 ETF，共 {len(picks)} 个行业")
    mapped = (
        _safe_invoke_tool(map_etf, {"industries": ",".join(picks), "market": market})
        if picks
        else "无可映射行业。"
    )
    _log_node("rm_portfolio_prepare", "RM 组合建议准备完成")
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
        _log_node("rm_portfolio_persist", "当前任务未要求保存 Trace", level="WARN")
        return {"workflow_context": workflow_context}

    _log_node("rm_portfolio_persist", "开始保存 RM 组合 Trace")
    trace = {
        "decision_date": _today(),
        "market": payload.get("market", state.get("market", "a_share")),
        "portfolio_recommendation": _parse_mapping_text(payload.get("mapped", ""), allocation_industries=payload.get("industries", [])),
        "risk_level": payload.get("risk_level", "未提供"),
        "industries": payload.get("industries", []),
        "config_version": "fixed-workflow-v1",
        "approval_status": "pending",
    }
    save_result = _safe_invoke_tool(
        save_decision_trace,
        {"trace_json": json.dumps(trace, ensure_ascii=False)},
    )
    _log_node("rm_portfolio_persist", "RM 组合 Trace 已保存")
    return {
        "workflow_context": _concat_sections(
            workflow_context,
            "### Trace 保存结果\n" + save_result,
        )
    }


def compliance_risk_node(state: AgentState):
    _log_step("compliance_risk", 1, 2, "定位最新 Trace")
    latest_path = _latest_trace_file()
    if not latest_path or not os.path.isfile(latest_path):
        _log_node("compliance_risk", "未找到可审查的 Trace", level="WARN")
        return {
            "workflow_context": "## 固定子图：合规风险检查\n未找到可审查的 trace。",
            "stop_reason": "风险检查缺少 trace 输入。",
        }

    _log_step("compliance_risk", 2, 2, f"读取并审查 Trace: {latest_path}")
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
    _log_node("compliance_risk", f"风险检查完成：缺失关键字段 {len(missing)} 个")
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


def multi_agent_debate_node(state: AgentState):
    """Pattern 7 + 15 + 17: Multi-Agent Debate with structured messages & self-consistency.

    Three specialists (Quant/Macro/Risk) debate in parallel, a Coordinator
    resolves conflicts using self-consistency voting.
    """
    market = state.get("market", "a_share")
    thread_id = state.get("thread_id", "default")
    start_date, end_date = _date_range(days=365)

    _log_step("multi_agent_debate", 1, 4, "准备证据 — 计算因子")
    factor_df = calc_factors_df(market=market, start_date=start_date, end_date=end_date)
    if factor_df.empty:
        return {
            "workflow_context": "## 固定子图：多 Agent 辩论\n未计算出有效因子，无法进行辩论。",
            "stop_reason": "多 Agent 辩论缺少因子数据。",
        }

    _log_step("multi_agent_debate", 2, 4, "准备证据 — 四象限 + 观察池 + 宏观")
    quadrant_df = score_quadrant_df(factor_df)
    _, quadrant_summary = _summarize_quadrants(quadrant_df)
    factor_summary = factor_df.head(10).to_string(index=False)
    overlay_text = _safe_invoke_tool(get_ic_overlay_config, {"market": market})
    observation_pool, veto_list = _parse_overlay_text(overlay_text)
    observation_text = json.dumps(observation_pool, ensure_ascii=False, indent=2)
    veto_text = "; ".join(veto_list) if veto_list else "无"

    # Macro evidence — four-way parallel scrape (Pattern 3).
    # Instead of relying on a single macro feed (which often empties out due to
    # AKShare 金十 rate-limits or Alpha Vantage network issues), we run four
    # independent queries concurrently and concatenate whatever returns non-empty.
    # This makes the Macro specialist far more likely to see SOMETHING.
    golden = quadrant_df[quadrant_df["quadrant"] == "黄金配置区"]["industry"].head(4).tolist()
    sector_kw = "、".join(golden[:3]) if golden else market

    # Lazy-import to keep startup time low
    try:
        from tools.news_tools import search_news as _search_global_news  # type: ignore
    except Exception:
        _search_global_news = None

    with _futures.ThreadPoolExecutor(max_workers=4) as pool:
        fut_macro_cn = pool.submit(_safe_invoke_tool, get_macro_events, {})
        fut_macro_kw = pool.submit(
            _safe_invoke_tool,
            search_news_cn,
            {"keywords": "宏观 央行 货币政策 财政 地缘", "limit": 6},
        )
        fut_news_sector = pool.submit(
            _safe_invoke_tool, search_news_cn, {"keywords": sector_kw, "limit": 8}
        )
        if _search_global_news is not None:
            fut_news_global = pool.submit(
                _safe_invoke_tool,
                _search_global_news,
                {"keywords": sector_kw or "china equity", "source": "alphavantage", "limit": 6},
            )
        else:
            fut_news_global = None

        raw_macro_cn = fut_macro_cn.result() or ""
        raw_macro_kw = fut_macro_kw.result() or ""
        news_sector = fut_news_sector.result() or ""
        raw_global = fut_news_global.result() if fut_news_global else ""

    def _is_useful(txt: str) -> bool:
        t = (txt or "").strip()
        if len(t) < 40:
            return False
        low = t.lower()
        for neg in ("未找到", "暂无", "error", "未配置"):
            if neg in low:
                return False
        return True

    macro_blocks: list[str] = []
    if _is_useful(raw_macro_cn):
        macro_blocks.append("### 国内宏观快讯 (金十 / AKShare)\n" + raw_macro_cn)
    if _is_useful(raw_macro_kw):
        macro_blocks.append("### 宏观主题新闻 (政策/央行/地缘)\n" + raw_macro_kw)
    if _is_useful(raw_global):
        macro_blocks.append("### 全球财经新闻 (Alpha Vantage)\n" + raw_global)

    if macro_blocks:
        macro_events = "\n\n".join(macro_blocks)
    else:
        macro_events = "(国内 + 全球宏观源此刻均返回空；请基于观察池与否决清单做保守推断。)"
        _log_node(
            "multi_agent_debate",
            "所有 4 路宏观源均返回空，Macro Agent 将只依赖 observation pool / veto list",
            level="WARN",
        )

    news_text = news_sector or ""

    _log_node(
        "multi_agent_debate",
        f"证据就绪: factor={len(factor_summary)} chars, "
        f"quadrant={len(quadrant_summary)} chars, "
        f"macro={len(macro_events)} chars (blocks={len(macro_blocks)}), "
        f"news={len(news_text)} chars",
    )

    _log_step("multi_agent_debate", 3, 4, "运行 Quant/Macro/Risk 三 Agent（并行）")
    user_question = state.get("user_input", "")
    debate_inputs = DebateInputs(
        market=market,
        factor_summary=factor_summary,
        quadrant_summary=quadrant_summary,
        observation_pool=observation_text,
        veto_list_text=veto_text,
        macro_events=macro_events,
        news_text=news_text,
        client_risk_level=state.get("client_risk_level"),
        user_question=user_question,
    )
    model = os.getenv("OPENAI_MODEL", "deepseek-v3.2")
    debate_result = run_debate_parallel(
        debate_inputs, thread_id=thread_id, model=model
    )

    _log_step("multi_agent_debate", 4, 4, "协调者聚合完成，生成叙事")
    verdict = debate_result["verdict"]
    narrative = verdict.get("narrative", "")
    recommended = verdict.get("recommended_sectors", [])
    vetoed = verdict.get("vetoed_sectors", [])
    disagreements = verdict.get("disagreements", [])

    debate_md_lines = [
        "## 固定子图：多 Agent 辩论（research_multi_agent_debate）",
        f"- 市场：{market}  / 客户风险：{state.get('client_risk_level') or 'N/A'}",
        f"- 推荐超配：{', '.join(recommended) if recommended else '无'}",
        f"- 一致否决：{', '.join(vetoed) if vetoed else '无'}",
        f"- 分歧数：{len(disagreements)}",
        "",
        "### 三 Agent 报告摘要",
    ]
    for role, rep in debate_result["reports"].items():
        debate_md_lines.append(
            f"- **{role}** — {rep.get('summary', '')[:220]}"
        )
        for v in rep.get("votes", [])[:5]:
            debate_md_lines.append(
                f"  - {v['sector']}: {v['stance']} (conf={v['confidence']:.2f}) — {v['rationale'][:100]}"
            )
    if disagreements:
        debate_md_lines.append("\n### 分歧与解决")
        for d in disagreements[:10]:
            pro = ", ".join(d.get("agents_pro", []))
            con = ", ".join(d.get("agents_con", []))
            debate_md_lines.append(
                f"- **{d['sector']}**: 支持={pro or '无'} / 反对={con or '无'} → {d.get('resolution', '')}"
            )
    debate_md_lines.append("\n### 协调者叙事\n" + narrative)

    payload = {
        "market": market,
        "client_risk_level": state.get("client_risk_level"),
        "debate": debate_result,
        "recommended_sectors": recommended,
        "vetoed_sectors": vetoed,
        "factor_summary": factor_summary,
    }
    return {
        "workflow_context": "\n".join(debate_md_lines),
        "task_payload": payload,
        "debate_result": debate_result,
        "tool_call_count": _bump_tool_count(state, 5),
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
    "multi_agent_debate": multi_agent_debate_node,
}


def register_subgraph_nodes(graph):
    for node_name, node_fn in SUBGRAPH_NODES.items():
        graph.add_node(node_name, node_fn)

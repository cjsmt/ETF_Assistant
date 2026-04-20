"""
从最新 Decision Trace 生成周报 Markdown。
用法: python scripts/generate_report.py [trace路径]
不传参数时自动取今日最新 trace。
"""
import json
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACE_DIR = os.path.join(PROJECT_ROOT, "traces")
TEMPLATE_PATH = os.path.join(PROJECT_ROOT, "templates", "weekly_report.md")


def _week_range(d: datetime) -> str:
    """返回 2026年第9周 (2月24日-3月2日) 格式"""
    iso = d.isocalendar()
    w = iso[1]
    # 周一为周始
    start = d
    while start.weekday() != 0:  # 0=周一
        start = start.replace(day=start.day - 1) if start.day > 1 else start
    from datetime import timedelta
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return f"{d.year}年第{w}周 ({start.month}月{start.day}日-{end.month}月{end.day}日)"


def _load_latest_trace():
    """加载今日最新 trace"""
    today = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(TRACE_DIR, today)
    if not os.path.isdir(folder):
        return None, None
    files = [f for f in os.listdir(folder) if f.endswith(".json") and f.startswith("trace_")]
    if not files:
        return None, None
    files.sort(reverse=True)
    path = os.path.join(folder, files[0])
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def _trace_to_report_data(trace: dict) -> dict:
    """将 trace 转为周报模板变量"""
    qd = trace.get("quadrant_distribution", {})
    pr = trace.get("portfolio_recommendation", {})
    risk = trace.get("risk_checks", {})
    obs = trace.get("observation_pool_filter", {})
    veto = trace.get("veto_list_exclusions", [])
    news = trace.get("news_validation", {})

    # 四象限：优先用 quadrant_distribution，否则从 portfolio 推断
    golden = qd.get("golden_zone") or [x["sector"] for x in pr.get("offensive_layer", [])]
    left = qd.get("left_side_zone") or [x["sector"] for x in pr.get("allocation_layer", [])]
    danger = qd.get("high_risk_zone", qd.get("danger_zone", []))
    garbage = qd.get("garbage_zone", [])

    # ETF 表格
    rows = []
    for layer in [pr.get("offensive_layer", []), pr.get("allocation_layer", []), pr.get("defensive_layer", [])]:
        for item in layer:
            if item.get("sector") == "现金":
                continue
            etf = item.get("etf", "待确认")
            code = etf[:6] if etf and etf != "待确认" and len(etf) >= 6 else "-"
            rows.append(f"| {item.get('sector')} | {code} | {item.get('weight', '-')} | {item.get('rationale', '-')} |")
    cash = next((x.get("weight", "10") for x in pr.get("defensive_layer", []) if x.get("sector") == "现金"), "10")
    cash = str(cash).replace("%", "")

    # 观察池
    pool_parts = []
    if obs.get("export_chain"):
        pool_parts.append(f"出口链: {', '.join(obs['export_chain'])}")
    if obs.get("policy_chain"):
        pool_parts.append(f"政策链: {', '.join(obs['policy_chain'])}")
    if obs.get("defensive"):
        pool_parts.append(f"防守: {', '.join(obs['defensive'])}")
    pool_desc = "；".join(pool_parts) if pool_parts else "见配置"

    # 否决
    veto_details = "\n".join(f"- {v}" for v in veto) if veto else "无"

    # 风险
    risk_parts = []
    if risk.get("concentration_risk"):
        risk_parts.append(risk["concentration_risk"])
    if risk.get("liquidity_risk"):
        risk_parts.append(risk["liquidity_risk"])
    for r in risk.get("macro_risks", []):
        risk_parts.append(r)
    for r in risk.get("sector_risks", []):
        risk_parts.append(r)
    risk_warn = "；".join(risk_parts) if risk_parts else "见正文"

    decision_date = trace.get("decision_date", trace.get("timestamp", datetime.now().strftime("%Y-%m-%d")))
    try:
        dt = datetime.strptime(decision_date, "%Y-%m-%d")
    except Exception:
        dt = datetime.now()
    report_week = _week_range(dt)

    return {
        "date": decision_date,
        "report_week": report_week,
        "data_timestamp": trace.get("data_period", decision_date),
        "config_version": trace.get("config_version", "N/A"),
        "golden_industries": "、".join(golden) if golden else "无",
        "left_side_industries": "、".join(left) if left else "无",
        "danger_industries": "、".join(danger) if danger else "无",
        "garbage_industries": "、".join(garbage) if garbage else "无",
        "pool_description": pool_desc,
        "veto_details": veto_details,
        "etf_table": "\n".join(rows) if rows else "| - | - | - | - |",
        "cash_reserve": cash,
        "changes_vs_last": "（首次报告或无上期对比数据）",
        "risk_warnings": risk_warn,
    }


def main():
    if len(sys.argv) > 1:
        trace_path = sys.argv[1]
        with open(trace_path, "r", encoding="utf-8") as f:
            trace = json.load(f)
    else:
        trace, trace_path = _load_latest_trace()
        if not trace:
            print("未找到今日 trace，请指定路径: python scripts/generate_report.py <trace.json>")
            sys.exit(1)
        print(f"使用 trace: {trace_path}")

    data = _trace_to_report_data(trace)
    data["report_week"] = data.get("report_week", "")  # 明确报告周

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        tpl = f.read()

    for k, v in data.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))

    out_dir = os.path.dirname(trace_path if len(sys.argv) > 1 else os.path.join(TRACE_DIR, datetime.now().strftime("%Y-%m-%d")))
    out_path = os.path.join(out_dir, "weekly_report.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(tpl)

    print(f"\n报告已生成: {out_path}")
    print(f"报告周: {data.get('report_week', 'N/A')}")
    print(f"数据区间: {data.get('data_timestamp', 'N/A')}")


if __name__ == "__main__":
    main()

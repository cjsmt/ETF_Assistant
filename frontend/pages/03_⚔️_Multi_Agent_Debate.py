"""Page 3 — Multi-Agent Debate viewer (Pattern 7/15/17)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import pandas as pd
import streamlit as st

from agent.patterns.multi_agent import DebateInputs, run_debate_parallel
from agent.patterns.pattern_log import PATTERN_LOG
from agent.subgraph import calc_factors_df, score_quadrant_df, _summarize_quadrants
from tools.filter_tools import get_ic_overlay_config

st.set_page_config(page_title="Multi-Agent Debate", page_icon="⚔️", layout="wide")
st.title("⚔️ Multi-Agent Debate — Quant · Macro · Risk")
st.caption("Pattern 7 (Multi-Agent) + 15 (Structured Messages) + 17 (Self-Consistency). Specialists run in parallel.")

with st.sidebar:
    st.header("Debate inputs")
    market = st.selectbox("Market", ["a_share", "hk", "us"], index=0)
    risk = st.selectbox("Client risk", ["N/A", "R1", "R2", "R3", "R4", "R5"], index=3)
    user_q = st.text_area(
        "Question for the specialists",
        value="请对当期 A 股做行业配置辩论，推荐应超配哪些板块，并指出应否决的行业。",
        height=120,
    )
    run_btn = st.button("🚀 Run debate", use_container_width=True, type="primary")

if run_btn:
    with st.spinner("Collecting evidence (factors + quadrants + overlay)…"):
        df = calc_factors_df(market=market)
        if df.empty:
            st.error("因子计算为空，请先检查数据源是否可达。")
            st.stop()
        quadrant_df = score_quadrant_df(df)
        _, quadrant_summary = _summarize_quadrants(quadrant_df)
        factor_summary = df.head(10).to_string(index=False)
        overlay = get_ic_overlay_config.invoke({"market": market})

    with st.spinner("Running three specialists in parallel…"):
        inputs = DebateInputs(
            market=market,
            factor_summary=factor_summary,
            quadrant_summary=quadrant_summary,
            observation_pool=overlay,
            veto_list_text="见 overlay",
            macro_events="",
            news_text="",
            client_risk_level=None if risk == "N/A" else risk,
            user_question=user_q,
        )
        result = run_debate_parallel(inputs, thread_id="debate_page")

    st.session_state["last_debate"] = result

if "last_debate" in st.session_state:
    result = st.session_state["last_debate"]
    verdict = result["verdict"]
    reports = result["reports"]

    st.success(
        f"Debate complete · {len(reports)} specialists · "
        f"{len(verdict.get('recommended_sectors', []))} recommended · "
        f"{len(verdict.get('vetoed_sectors', []))} vetoed · "
        f"{len(verdict.get('disagreements', []))} disagreements"
    )

    st.markdown("### 🧠 Coordinator verdict")
    col1, col2, col3 = st.columns(3)
    col1.success("Overweight: " + (", ".join(verdict.get("recommended_sectors", [])) or "—"))
    col2.error("Vetoed: " + (", ".join(verdict.get("vetoed_sectors", [])) or "—"))
    col3.info(f"Disagreements: {len(verdict.get('disagreements', []))}")
    st.write(verdict.get("narrative", ""))

    st.markdown("### 👥 Specialist reports")
    tabs = st.tabs(["🔢 Quant", "🌍 Macro", "🛡️ Risk"])
    role_order = ["quant", "macro", "risk"]
    for role, tab in zip(role_order, tabs):
        rep = reports.get(role, {})
        with tab:
            if not rep:
                st.info(f"{role} agent did not produce a report.")
                continue
            st.markdown(f"**Summary**: {rep.get('summary', '')}")
            if rep.get("caveats"):
                for c in rep["caveats"]:
                    st.caption(f"⚠ caveat: {c}")
            votes = rep.get("votes", [])
            if votes:
                df = pd.DataFrame(votes)
                df_show = df[["sector", "stance", "confidence", "rationale"]].copy()
                st.dataframe(df_show, use_container_width=True, hide_index=True)
                with st.expander("📎 Evidences"):
                    for v in votes:
                        if v.get("evidences"):
                            st.markdown(f"**{v['sector']}**")
                            for e in v["evidences"]:
                                st.markdown(
                                    f"- [{e.get('source', '?')}|w={e.get('weight', 0):.2f}] {e.get('content', '')[:160]}"
                                )

    st.markdown("### 🗣️ Disagreements & resolution")
    disagreements = verdict.get("disagreements", [])
    if not disagreements:
        st.caption("No disagreements this round — consensus across specialists.")
    else:
        rows = [
            {
                "sector": d["sector"],
                "pro": ", ".join(d.get("agents_pro", [])),
                "con": ", ".join(d.get("agents_con", [])),
                "resolution": d.get("resolution", ""),
            }
            for d in disagreements
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with st.expander("🔎 Raw debate JSON"):
        st.json(result)

else:
    st.info("Configure inputs on the left and press **Run debate** to start.")

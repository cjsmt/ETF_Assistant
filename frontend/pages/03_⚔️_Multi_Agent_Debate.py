"""Page 3 — Multi-Agent Debate viewer (Pattern 7/15/17)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import pandas as pd
import streamlit as st

from agent.patterns.multi_agent import DebateInputs, run_debate_parallel
from agent.patterns.pattern_log import PATTERN_LOG
from agent.subgraph import calc_factors_df, score_quadrant_df, _summarize_quadrants
from frontend.i18n import current_lang, t
from tools.filter_tools import get_ic_overlay_config

st.title(t("debate.title"))
st.caption(t("debate.caption"))

MARKET_LABELS = {
    "a_share": t("common.market.a_share"),
    "hk":      t("common.market.hk"),
    "us":      t("common.market.us"),
}

with st.sidebar:
    st.header(t("debate.sidebar.header"))
    market = st.selectbox(
        t("common.market"),
        options=list(MARKET_LABELS.keys()),
        format_func=lambda k: MARKET_LABELS[k],
        index=0,
    )
    risk = st.selectbox(t("common.client_risk"), ["N/A", "R1", "R2", "R3", "R4", "R5"], index=3)
    user_q = st.text_area(
        t("debate.question"),
        value=t("debate.question.default"),
        height=120,
    )
    run_btn = st.button(t("debate.run"), use_container_width=True, type="primary")

if run_btn:
    with st.spinner(t("debate.spinner.evidence")):
        df = calc_factors_df(market=market)
        if df.empty:
            st.error(t("debate.err.empty_factor"))
            st.stop()
        quadrant_df = score_quadrant_df(df)
        _, quadrant_summary = _summarize_quadrants(quadrant_df)
        factor_summary = df.head(10).to_string(index=False)
        overlay = get_ic_overlay_config.invoke({"market": market})

    with st.spinner(t("debate.spinner.parallel")):
        lang = current_lang()
        inputs = DebateInputs(
            market=market,
            factor_summary=factor_summary,
            quadrant_summary=quadrant_summary,
            observation_pool=overlay,
            veto_list_text="see overlay" if lang == "en" else "见 overlay",
            macro_events="",
            news_text="",
            client_risk_level=None if risk == "N/A" else risk,
            user_question=user_q,
            output_language=lang,
        )
        result = run_debate_parallel(inputs, thread_id="debate_page")

    st.session_state["last_debate"] = result

if "last_debate" in st.session_state:
    result = st.session_state["last_debate"]
    verdict = result["verdict"]
    reports = result["reports"]

    st.success(
        t(
            "debate.complete",
            n_reports=len(reports),
            n_rec=len(verdict.get("recommended_sectors", [])),
            n_veto=len(verdict.get("vetoed_sectors", [])),
            n_dis=len(verdict.get("disagreements", [])),
        )
    )

    st.markdown(f"### {t('debate.section.verdict')}")
    col1, col2, col3 = st.columns(3)
    col1.success(f"{t('debate.overweight')}: " + (", ".join(verdict.get("recommended_sectors", [])) or "—"))
    col2.error(f"{t('debate.vetoed')}: " + (", ".join(verdict.get("vetoed_sectors", [])) or "—"))
    col3.info(f"{t('debate.disagreements')}: {len(verdict.get('disagreements', []))}")
    st.write(verdict.get("narrative", ""))

    st.markdown(f"### {t('debate.section.reports')}")
    tabs = st.tabs([t("debate.tab.quant"), t("debate.tab.macro"), t("debate.tab.risk")])
    role_order = ["quant", "macro", "risk"]
    for role, tab in zip(role_order, tabs):
        rep = reports.get(role, {})
        with tab:
            if not rep:
                st.info(t("debate.no_report", role=role))
                continue
            st.markdown(f"**{t('debate.summary')}**: {rep.get('summary', '')}")
            if rep.get("caveats"):
                for c in rep["caveats"]:
                    st.caption(f"⚠ {t('debate.caveat')}: {c}")
            votes = rep.get("votes", [])
            if votes:
                df = pd.DataFrame(votes)
                df_show = df[["sector", "stance", "confidence", "rationale"]].copy()
                st.dataframe(df_show, use_container_width=True, hide_index=True)
                with st.expander(t("debate.evidences")):
                    for v in votes:
                        if v.get("evidences"):
                            st.markdown(f"**{v['sector']}**")
                            for e in v["evidences"]:
                                st.markdown(
                                    f"- [{e.get('source', '?')}|w={e.get('weight', 0):.2f}] {e.get('content', '')[:160]}"
                                )

    st.markdown(f"### {t('debate.section.dis')}")
    disagreements = verdict.get("disagreements", [])
    if not disagreements:
        st.caption(t("debate.consensus"))
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

    with st.expander(t("debate.raw")):
        st.json(result)

else:
    st.info(t("debate.wait"))

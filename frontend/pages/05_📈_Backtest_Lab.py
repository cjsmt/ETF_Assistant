"""Page 5 — Backtest Lab."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

from datetime import datetime, timedelta

import streamlit as st

from frontend.i18n import t
from tools.backtest_tools import run_backtest

st.title(t("bt.title"))
st.caption(t("bt.caption"))

MARKET_LABELS = {
    "a_share": t("common.market.a_share"),
    "hk":      t("common.market.hk"),
    "us":      t("common.market.us"),
}
FREQ_LABELS = {
    "monthly": t("bt.title.monthly"),
    "weekly":  t("bt.title.weekly"),
}

with st.sidebar:
    st.header(t("bt.sidebar.header"))
    market = st.selectbox(
        t("common.market"),
        options=list(MARKET_LABELS.keys()),
        format_func=lambda k: MARKET_LABELS[k],
        index=0,
    )
    today = datetime.now()
    default_start = (today - timedelta(days=730)).strftime("%Y-%m-%d")
    default_end = today.strftime("%Y-%m-%d")
    start_date = st.text_input(t("bt.start"), value=default_start)
    end_date = st.text_input(t("bt.end"), value=default_end)
    freqs = st.multiselect(
        t("bt.freqs"),
        options=list(FREQ_LABELS.keys()),
        default=list(FREQ_LABELS.keys()),
        format_func=lambda k: FREQ_LABELS[k],
    )
    run = st.button(t("bt.run"), use_container_width=True, type="primary")

if run:
    import concurrent.futures as _f

    results: dict[str, str] = {}
    with st.spinner(t("bt.running", n=len(freqs))):
        with _f.ThreadPoolExecutor(max_workers=max(len(freqs), 1)) as pool:
            futures = {
                pool.submit(
                    run_backtest.invoke,
                    {
                        "market": market,
                        "start_date": start_date,
                        "end_date": end_date,
                        "rebalance_freq": freq,
                    },
                ): freq
                for freq in freqs
            }
            for fut in _f.as_completed(futures):
                freq = futures[fut]
                try:
                    results[freq] = str(fut.result())
                except Exception as exc:
                    results[freq] = f"Error: {exc}"
    st.session_state["backtest_results"] = results

results = st.session_state.get("backtest_results", {})
if results:
    cols = st.columns(len(results))
    for col, (freq, out) in zip(cols, results.items()):
        with col:
            st.markdown(f"### {FREQ_LABELS.get(freq, freq.title())}")
            st.code(out[:6000], language="text")
else:
    st.info(t("bt.wait"))

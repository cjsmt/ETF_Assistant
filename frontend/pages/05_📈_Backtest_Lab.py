"""Page 5 — Backtest Lab."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

from datetime import datetime, timedelta

import streamlit as st

from tools.backtest_tools import run_backtest

st.set_page_config(page_title="Backtest Lab", page_icon="📈", layout="wide")
st.title("📈 Backtest Lab")
st.caption("Run monthly/weekly rebalance backtests. (Pattern 3 Parallelization is demoed below.)")

with st.sidebar:
    st.header("Parameters")
    market = st.selectbox("Market", ["a_share", "hk", "us"], index=0)
    today = datetime.now()
    default_start = (today - timedelta(days=730)).strftime("%Y-%m-%d")
    default_end = today.strftime("%Y-%m-%d")
    start_date = st.text_input("Start date", value=default_start)
    end_date = st.text_input("End date", value=default_end)
    freqs = st.multiselect(
        "Rebalance freqs (run in parallel)",
        ["monthly", "weekly"],
        default=["monthly", "weekly"],
    )
    run = st.button("🏁 Run backtest(s) in parallel", use_container_width=True, type="primary")

if run:
    import concurrent.futures as _f

    results: dict[str, str] = {}
    with st.spinner(f"Running {len(freqs)} backtests in parallel…"):
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
            st.markdown(f"### {freq.title()} rebalance")
            st.code(out[:6000], language="text")
else:
    st.info("Select frequencies on the left and click **Run backtest(s)**.")

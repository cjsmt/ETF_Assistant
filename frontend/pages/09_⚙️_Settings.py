"""Page 9 — Settings (Memory + Overlay config)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import os

import streamlit as st

from agent.patterns.memory import list_all_threads, load_memory, save_memory, update_profile
from frontend._bootstrap import PROJECT_ROOT

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")

tab_mem, tab_overlay = st.tabs(["🧠 Memory (Pattern 8)", "📋 IC Overlay config"])

with tab_mem:
    st.caption("Inspect and edit the long-term memory stored per thread.")
    threads = list_all_threads()
    if not threads:
        st.info("No memory threads yet.")
    else:
        thread = st.selectbox("Thread", threads)
        mem = load_memory(thread)
        st.markdown("### Profile")
        c1, c2, c3 = st.columns(3)
        risk = c1.selectbox(
            "Risk level",
            ["", "R1", "R2", "R3", "R4", "R5"],
            index=(["", "R1", "R2", "R3", "R4", "R5"].index(mem.profile.risk_level or "")),
        )
        market = c2.selectbox(
            "Preferred market",
            ["", "a_share", "hk", "us"],
            index=(["", "a_share", "hk", "us"].index(mem.profile.preferred_market or "")),
        )
        note = c3.text_input("Note", value=mem.profile.note)
        if st.button("💾 Save profile"):
            update_profile(thread, risk_level=risk or None, preferred_market=market or None, note=note)
            st.success("Saved.")

        st.markdown("### Query history")
        import pandas as pd

        if mem.query_history:
            st.dataframe(pd.DataFrame(mem.query_history[-30:]), use_container_width=True, hide_index=True)
        else:
            st.caption("No history yet.")

        st.markdown("### Task counter")
        if mem.task_counter:
            st.bar_chart(mem.task_counter)
        else:
            st.caption("No tasks counted yet.")

with tab_overlay:
    st.caption("Edit the subjective observation pool + negative list that the Risk agent enforces.")
    config_dir = os.path.join(PROJECT_ROOT, "config")
    editable = [
        "subjective_pool.yaml",
        "veto_list.yaml",
        "etf_mapping.yaml",
        "factor_params.yaml",
        "risk_params.yaml",
        "quadrant_thresholds.yaml",
    ]
    available = [f for f in editable if os.path.isfile(os.path.join(config_dir, f))]
    if not available:
        st.warning(f"No config files found under `{config_dir}`.")
    else:
        picked = st.selectbox("File", available)
        path = os.path.join(config_dir, picked)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        edited = st.text_area(picked, value=content, height=500)
        if st.button(f"💾 Save {picked}"):
            with open(path, "w", encoding="utf-8") as f:
                f.write(edited)
            st.success(f"Saved {picked}. The agent will pick up changes on the next run.")

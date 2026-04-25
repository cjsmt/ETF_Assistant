"""Page 9 — Settings (Memory + Overlay config)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import os

import streamlit as st

from agent.patterns.memory import list_all_threads, load_memory, save_memory, update_profile
from frontend._bootstrap import PROJECT_ROOT
from frontend.i18n import t

st.title(t("cfg.title"))

tab_mem, tab_overlay = st.tabs([t("cfg.tab.memory"), t("cfg.tab.overlay")])

with tab_mem:
    st.caption(t("cfg.mem.caption"))
    threads = list_all_threads()
    if not threads:
        st.info(t("cfg.mem.empty"))
    else:
        thread = st.selectbox(t("cfg.mem.thread"), threads)
        mem = load_memory(thread)
        st.markdown(f"### {t('cfg.mem.profile')}")
        c1, c2, c3 = st.columns(3)
        risk = c1.selectbox(
            t("cfg.mem.risk"),
            ["", "R1", "R2", "R3", "R4", "R5"],
            index=(["", "R1", "R2", "R3", "R4", "R5"].index(mem.profile.risk_level or "")),
        )
        market = c2.selectbox(
            t("cfg.mem.market"),
            ["", "a_share", "hk", "us"],
            index=(["", "a_share", "hk", "us"].index(mem.profile.preferred_market or "")),
        )
        note = c3.text_input(t("cfg.mem.note"), value=mem.profile.note)
        if st.button(t("cfg.mem.save_profile")):
            update_profile(thread, risk_level=risk or None, preferred_market=market or None, note=note)
            st.success(t("cfg.mem.saved"))

        st.markdown(f"### {t('cfg.mem.history')}")
        import pandas as pd

        if mem.query_history:
            st.dataframe(pd.DataFrame(mem.query_history[-30:]), use_container_width=True, hide_index=True)
        else:
            st.caption(t("cfg.mem.no_history"))

        st.markdown(f"### {t('cfg.mem.tasks')}")
        if mem.task_counter:
            st.bar_chart(mem.task_counter)
        else:
            st.caption(t("cfg.mem.no_tasks"))

with tab_overlay:
    st.caption(t("cfg.overlay.caption"))
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
        st.warning(t("cfg.overlay.none", path=config_dir))
    else:
        picked = st.selectbox(t("cfg.overlay.file"), available)
        path = os.path.join(config_dir, picked)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        edited = st.text_area(picked, value=content, height=500)
        if st.button(t("cfg.overlay.save", name=picked)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(edited)
            st.success(t("cfg.overlay.saved", name=picked))

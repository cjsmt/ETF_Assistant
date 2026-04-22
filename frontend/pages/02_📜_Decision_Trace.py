"""Page 2 — Browse Decision Traces."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import json
import os

import streamlit as st

from frontend._bootstrap import TRACE_DIR

st.set_page_config(page_title="Decision Trace", page_icon="📜", layout="wide")
st.title("📜 Decision Trace Explorer")
st.caption("Browse and inspect every audit-grade trace written by the agent.")


def list_traces() -> list[dict]:
    out = []
    if not os.path.isdir(TRACE_DIR):
        return out
    for date_folder in sorted(os.listdir(TRACE_DIR), reverse=True):
        folder = os.path.join(TRACE_DIR, date_folder)
        if not os.path.isdir(folder) or date_folder in {"hitl", "memory"}:
            continue
        for fn in sorted(os.listdir(folder), reverse=True):
            if not (fn.startswith("trace_") and fn.endswith(".json")):
                continue
            path = os.path.join(folder, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    trace = json.load(f)
            except Exception:
                continue
            out.append(
                {
                    "date": date_folder,
                    "file": fn,
                    "path": path,
                    "decision_date": trace.get("decision_date", date_folder),
                    "market": trace.get("market", "?"),
                    "approval_status": trace.get("approval_status", "?"),
                    "trace": trace,
                }
            )
    return out


traces = list_traces()

left, right = st.columns([1, 2])

with left:
    st.markdown(f"### {len(traces)} traces on disk")
    if not traces:
        st.info("No traces yet. Run the agent first.")
    idx = st.radio(
        "Select a trace",
        options=list(range(len(traces))),
        format_func=lambda i: (
            f"{traces[i]['decision_date']} · {traces[i]['market']} · "
            f"{traces[i]['approval_status']} · {traces[i]['file'][:20]}"
        ),
        index=0 if traces else None,
    )

with right:
    if traces and idx is not None:
        trace = traces[idx]["trace"]
        st.markdown(f"### 🗂️ {traces[idx]['file']}")
        st.caption(f"Path: `{traces[idx]['path']}`")

        colA, colB, colC = st.columns(3)
        colA.metric("Market", trace.get("market", "-"))
        colB.metric("Approval", trace.get("approval_status", "-"))
        colC.metric("Version", trace.get("config_version", "-"))

        if trace.get("portfolio_recommendation"):
            st.markdown("#### 📊 Portfolio recommendation")
            import pandas as pd

            for layer, rows in trace["portfolio_recommendation"].items():
                if not rows:
                    continue
                st.markdown(f"**{layer}**")
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True, hide_index=True)

        if trace.get("quadrant_distribution"):
            st.markdown("#### 🧭 Quadrant distribution")
            qd = trace["quadrant_distribution"]
            for k, v in qd.items():
                st.markdown(f"- **{k}** ({len(v)}): {', '.join(v[:12])}")

        if trace.get("risk_checks"):
            st.markdown("#### ⚠️ Risk checks")
            st.json(trace["risk_checks"])

        with st.expander("🔎 Raw JSON"):
            st.json(trace)

        st.download_button(
            "⬇️ Download trace JSON",
            data=json.dumps(trace, ensure_ascii=False, indent=2),
            file_name=traces[idx]["file"],
            mime="application/json",
        )

"""Page 6 — HITL Approval Queue (Pattern 18)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import streamlit as st

from agent.patterns.guardrails import decide_hitl, list_hitl_queue

st.set_page_config(page_title="HITL Approval", page_icon="🛡️", layout="wide")
st.title("🛡️ Human-in-the-Loop Approval Queue")
st.caption("Pattern 18 / Guardrails. Formal deliverables cannot ship to clients until approved here.")

status_filter = st.selectbox(
    "Filter by status", ["all", "pending", "approved", "rejected"], index=0
)
queue = list_hitl_queue(status=status_filter)

col_list, col_detail = st.columns([1, 2])

with col_list:
    st.markdown(f"### {len(queue)} requests")
    if not queue:
        st.info("Queue is empty.")
    idx = st.radio(
        "Select a request",
        options=list(range(len(queue))),
        format_func=lambda i: (
            f"{queue[i]['status']:>8} · {queue[i]['created_at']} · {queue[i]['task_key']} "
            f"({queue[i]['id'][:25]})"
        ),
        index=0 if queue else None,
    )

with col_detail:
    if queue and idx is not None:
        rec = queue[idx]
        st.markdown(f"### Request `{rec['id']}`")
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Status", rec.get("status", "?"))
        sc2.metric("Task", rec.get("task_key", "?"))
        sc3.metric("Requester", rec.get("requester", "?"))

        with st.expander("📦 Payload", expanded=True):
            st.json(rec.get("payload", {}))

        if rec.get("status") == "pending":
            st.markdown("### ✅ Decide")
            reviewer = st.text_input("Reviewer name", value="compliance_officer")
            comment = st.text_area("Comment", height=80)
            a, b = st.columns(2)
            if a.button("✅ Approve", type="primary", use_container_width=True):
                decide_hitl(rec["id"], approved=True, reviewer=reviewer, comment=comment)
                st.success("Approved.")
                st.rerun()
            if b.button("❌ Reject", use_container_width=True):
                decide_hitl(rec["id"], approved=False, reviewer=reviewer, comment=comment)
                st.error("Rejected.")
                st.rerun()
        else:
            st.success(
                f"Decided **{rec.get('decision')}** by `{rec.get('decided_by')}` "
                f"at `{rec.get('decided_at')}`.\n\nComment: {rec.get('comment') or '—'}"
            )

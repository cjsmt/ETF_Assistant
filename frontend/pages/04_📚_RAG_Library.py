"""Page 4 — RAG Research Library (Pattern 14)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import os

import streamlit as st

from agent.patterns.rag import rebuild_rag_index, search_research_library
from frontend._bootstrap import DOCS_DIR

st.set_page_config(page_title="RAG Library", page_icon="📚", layout="wide")
st.title("📚 Research Library — Pattern 14 (RAG)")
st.caption("Semantic search over the internal thinking framework, architecture doc, and business model.")

col_search, col_admin = st.columns([3, 1])

with col_admin:
    st.markdown("### 🔧 Index")
    files = []
    for root, _, names in os.walk(DOCS_DIR):
        if "rag_index" in root:
            continue
        for n in names:
            if n.lower().endswith((".md", ".txt")):
                files.append(os.path.relpath(os.path.join(root, n), DOCS_DIR))
    st.caption(f"Source files ({len(files)}):")
    for f in files:
        st.markdown(f"- `{f}`")
    if st.button("🔁 Rebuild index", use_container_width=True):
        with st.spinner("Re-embedding documents…"):
            result = rebuild_rag_index()
        st.success(f"Index rebuilt · {result}")

with col_search:
    st.markdown("### 🔎 Search")
    query = st.text_input(
        "Ask a question",
        value="什么是 smart money 因子？",
        placeholder="e.g. 如何划分四象限？ / 业务模式是什么？",
    )
    k = st.slider("Top-k chunks", min_value=1, max_value=10, value=4)

    if query:
        with st.spinner("Retrieving…"):
            results = search_research_library(query, k=k, thread_id="rag_page")
        if not results:
            st.warning("No chunks found. Try rebuilding the index on the right.")
        else:
            st.success(f"{len(results)} chunks retrieved.")
            for i, c in enumerate(results, 1):
                with st.expander(
                    f"[{i}] {c.source} · score={c.score:.4f}", expanded=(i == 1)
                ):
                    st.write(c.content)

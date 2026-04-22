"""Page 8 — MCP Server Inspector (Pattern 10)."""
from __future__ import annotations

import frontend._bootstrap  # noqa: F401

import streamlit as st

from mcp_server.news_mcp_client import call_mcp_tool, list_mcp_tools

st.set_page_config(page_title="MCP Inspector", page_icon="🔌", layout="wide")
st.title("🔌 MCP Server Inspector — Pattern 10")
st.caption(
    "The ETF agent consumes the news/macro tools via the **Model Context Protocol**. "
    "This page lists the tools exposed by the MCP server and lets you invoke them live."
)

mode = st.radio("Transport", ["stdio", "http"], horizontal=True)
url = st.text_input("HTTP URL (only if mode=http)", value="http://127.0.0.1:8765/mcp")

if "mcp_tools" not in st.session_state:
    st.session_state["mcp_tools"] = None

if st.button("🔍 Discover tools on the MCP server", type="primary"):
    with st.spinner("Handshake with MCP server…"):
        tools = list_mcp_tools(mode=mode, url=url if mode == "http" else None)
    st.session_state["mcp_tools"] = tools

tools = st.session_state.get("mcp_tools")
if tools:
    st.success(f"{len(tools)} MCP tools exposed.")
    for t in tools:
        with st.expander(f"🛠️ {t['name']}"):
            st.caption(t.get("description", ""))
            if t.get("input_schema"):
                st.json(t["input_schema"])

    st.divider()
    st.markdown("### ▶️ Invoke a tool")
    names = [t["name"] for t in tools]
    pick = st.selectbox("Tool", names)
    args_text = st.text_area(
        "Arguments (JSON)",
        value='{"keywords": "半导体", "limit": 10}',
        height=120,
    )
    if st.button("Call tool via MCP"):
        import json

        try:
            args = json.loads(args_text) if args_text.strip() else {}
        except Exception as exc:
            st.error(f"Invalid JSON: {exc}")
            args = None

        if args is not None:
            with st.spinner("Calling MCP…"):
                out = call_mcp_tool(pick, args, mode=mode, url=url)
            st.markdown("#### Result")
            st.code(out[:8000], language="text")
else:
    st.info("Press the **Discover** button to enumerate MCP tools.")

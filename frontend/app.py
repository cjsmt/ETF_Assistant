"""
AI Quant Assistant for ETF Rotation Strategies — Frontend (Streamlit)

TODO: This is a placeholder. Implement the full UI after backend is stable.
Run with: streamlit run frontend/app.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

st.set_page_config(page_title="AI Quant Assistant - ETF Rotation", layout="wide")

st.title("AI Quant Assistant for ETF Rotation Strategies")
st.caption("ETF 行业轮动智能顾问引擎")

with st.sidebar:
    st.header("Settings")
    market = st.selectbox("Market / 市场", ["a_share", "hk", "us"], index=0)
    role = st.selectbox("Role / 角色", ["researcher", "rm", "compliance"], index=0)
    
    if role == "rm":
        risk_level = st.selectbox("Client Risk Level / 客户风险等级", ["R1", "R2", "R3", "R4", "R5"], index=2)
    else:
        risk_level = None
    
    st.divider()
    st.markdown("**Quick Actions**")
    if st.button("Generate Weekly Report / 生成周报"):
        st.session_state["quick_action"] = "生成本周行业轮动周报"
    if st.button("Run Backtest / 跑回测"):
        st.session_state["quick_action"] = "回测过去一年的策略表现"

# Chat interface
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask the AI Quant Assistant... / 输入指令...")

if "quick_action" in st.session_state:
    prompt = st.session_state.pop("quick_action")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Agent is thinking..."):
            # TODO: Connect to agent/graph.py
            # from agent.graph import run_agent
            # response = run_agent(prompt, market=market, role=role)
            response = f"[Agent Placeholder] Received: '{prompt}'\n\nMarket: {market}, Role: {role}\n\nTODO: Connect to LangGraph agent backend."
            st.markdown(response)
    st.session_state.messages.append({"role": "assistant", "content": response})

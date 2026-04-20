from agent.prompts.prompt_builder import build_system_prompt


# 兼容旧用法：未传入运行时上下文时，退回为研究员 + A 股 + 通用任务。
SYSTEM_PROMPT = build_system_prompt(
    role="researcher",
    market="a_share",
    user_input="",
    client_risk_level=None,
)

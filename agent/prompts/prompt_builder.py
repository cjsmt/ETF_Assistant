from agent.prompts.base_prompt import BASE_SYSTEM_PROMPT
from agent.prompts.role_prompts import get_role_prompt
from agent.prompts.task_prompts import get_task_prompt, infer_task_key


def build_system_prompt(
    role: str,
    market: str,
    user_input: str,
    client_risk_level: str | None = None,
) -> str:
    task_key = infer_task_key(user_input=user_input, role=role)

    runtime_context = [
        "## 运行时上下文",
        f"- 当前角色：{role}",
        f"- 当前市场：{market}",
        f"- 当前识别任务：{task_key}",
    ]
    if role == "rm":
        runtime_context.append(f"- 客户风险等级：{client_risk_level or '未提供'}")

    market_context = """## 市场约束
- 必须优先在当前市场内完成分析与推荐，不跨市场混合比较，除非用户明确要求跨市场比较。
- 若当前市场配置不完整或缺少可用数据，要明确指出，不可默认虚构行业或 ETF。"""

    role_prompt = get_role_prompt(role)
    task_prompt = get_task_prompt(task_key)

    return "\n\n".join(
        [
            BASE_SYSTEM_PROMPT,
            "\n".join(runtime_context),
            market_context,
            role_prompt,
            task_prompt,
        ]
    )

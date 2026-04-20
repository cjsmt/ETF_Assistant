from agent.prompts.task_prompts import TASK_SUMMARIES, get_allowed_task_keys_for_role


def build_router_prompt(
    role: str,
    market: str,
    user_input: str,
    client_risk_level: str | None = None,
) -> str:
    allowed_task_keys = get_allowed_task_keys_for_role(role)
    task_lines = [f"- `{task_key}`: {TASK_SUMMARIES[task_key]}" for task_key in allowed_task_keys]
    risk_line = ""
    if role == "rm":
        risk_line = f"- 客户风险等级：{client_risk_level or '未提供'}\n"

    return f"""你是一个结构化分类 Router，负责将用户请求映射为一个明确的任务类型，并产出可供后续工作流使用的结构化决策。

## 当前上下文
- 当前角色：{role}
- 当前市场：{market}
{risk_line}- 用户请求：{user_input}

## 你只能从以下任务类型中选择一个
{chr(10).join(task_lines)}

## 分类规则
1. 只能选择一个最匹配的 `task_key`。
2. `data_strategy` 的含义：
   - `direct_answer`：无需工具或只需直接说明。
   - `history_first`：优先查历史 trace 或历史结论。
   - `fresh_scan`：优先做当前市场扫描、因子、象限、映射等现时分析。
   - `hybrid`：需要同时参考历史与当前数据。
3. `should_use_tools` 仅在确实需要数据、trace、新闻、回测等时为 true。
4. `requires_trace_save` 适用于正式建议、正式周报、正式审批材料等。
5. `route_reason` 要简洁具体，说明分类依据。
6. 若存在歧义，优先选最接近用户直接目标的任务，而不是选过于宽泛的 generic。
"""

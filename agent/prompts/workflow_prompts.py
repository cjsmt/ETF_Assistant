def build_planner_prompt(
    role: str,
    market: str,
    task_key: str,
    user_input: str,
    data_strategy: str = "fresh_scan",
    requires_trace_save: bool = False,
    client_risk_level: str | None = None,
) -> str:
    risk_line = ""
    if role == "rm":
        risk_line = f"- 客户风险等级：{client_risk_level or '未提供'}\n"

    return f"""你是一个受控工作流中的 Planner，负责把用户问题拆成清晰、有限、可执行的步骤。

请基于以下上下文生成一个简短执行计划：
- 当前角色：{role}
- 当前市场：{market}
- 当前任务：{task_key}
- 数据策略：{data_strategy}
- 完成后是否建议保存 trace：{'是' if requires_trace_save else '否'}
{risk_line}- 用户问题：{user_input}

要求：
1. 只输出一个简洁的 Markdown 计划，不要调用工具，不要输出最终答案。
2. 计划应包含以下四部分：
   - 任务目标
   - 建议步骤（3 到 6 步）
   - 预期工具
   - 停止条件
3. 优先最小化工具调用次数；能复用历史 trace 时不要重复全量扫描。
4. 若是正式建议、正式周报、正式审批材料，计划中应说明最后需要保存 trace。
"""


def build_executor_guidance(
    plan: str,
    max_tool_calls: int,
    tool_call_count: int,
    data_strategy: str = "fresh_scan",
    requires_trace_save: bool = False,
) -> str:
    remaining = max(max_tool_calls - tool_call_count, 0)
    return f"""## 执行阶段约束
你当前处于受控 Executor 阶段，请严格围绕已给出的计划执行，不要无计划地扩展问题。

### 当前计划
{plan}

### 执行规则
1. 每一轮只做离当前目标最近的一步。
2. 若已有足够信息形成结论，直接输出最终答案，不要继续调用工具。
3. 避免重复调用同一工具并传入相同参数。
4. 若发现缺少关键数据，明确说明缺失项与影响。
5. 当前推荐数据策略：`{data_strategy}`。若是 `history_first`，优先查历史 trace；若是 `fresh_scan`，优先做当前分析；若是 `hybrid`，先历史再现时或按计划组合。
6. {'这是正式建议/正式材料，结尾应保存 trace。' if requires_trace_save else '若不是正式材料，不必为了形式而强行保存 trace。'}

### 工具预算
- 最大工具调用数：{max_tool_calls}
- 已使用工具调用数：{tool_call_count}
- 剩余预算：{remaining}
"""


def build_finalizer_guidance(stop_reason: str | None = None) -> str:
    extra = ""
    if stop_reason:
        extra = f"\n### 结束原因\n- {stop_reason}\n- 请基于当前已获得的信息给出尽可能完整、但不过度推断的结论。\n"

    return f"""你当前处于 Finalizer 阶段。
不要再调用任何工具，请基于已有上下文完成最终回答。

要求：
1. 明确区分已确认事实、推理判断、风险提示、待确认项。
2. 若由于工具预算或重复调用保护而提前结束，要诚实说明结论边界。
3. 输出要符合当前角色与任务场景。
{extra}
"""

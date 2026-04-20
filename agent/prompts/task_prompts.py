from typing import Dict


TASK_PROMPTS: Dict[str, str] = {
    "generic": """## 任务覆盖：通用任务
当用户意图不属于任何高频模板时，按以下原则执行：
1. 先判断目标属于研究、投顾、还是合规审查。
2. 只调用完成该任务所必需的工具。
3. 输出中说明你的判断依据、风险提示和待确认项。
""",
    "research_weekly_report": """## 任务覆盖：投研周报 / 轮动研究
适用问题：生成本周/本日周报、行业轮动总结、市场扫描。

推荐流程：
1. `get_market_data` 获取近一段时间市场数据。
2. `calc_factors` 计算趋势与共识因子。
3. `score_quadrant` 完成四象限划分。
4. `get_ic_overlay_config` 获取观察池与负面清单并做主观过滤。
5. 仅对高优先级行业做 `search_news_cn` 或 `search_news` 交叉验证。
6. `map_etf` 生成组合落地建议。
7. 若用户要求正式材料，可 `generate_report` 并 `save_decision_trace`。

输出建议：
- 四象限分布
- 观察池/否决说明
- ETF 组合建议
- 风险提示
- 必要时附上研究摘要或报告结构
""",
    "research_overlay_adjustment": """## 任务覆盖：观察池 / 负面清单调整复跑
适用问题：加入观察池、移除负面清单、重跑一遍、比较修改前后结果。

推荐流程：
1. 明确用户要调整的规则、行业或配置项。
2. 若需要对比历史，先用 `get_decision_history` 获取上次结果。
3. 重新运行市场扫描、因子计算与象限划分。
4. 结合观察池/负面清单解释哪些行业入选、哪些被剔除。
5. 输出“修改前 vs 修改后”的变化摘要。

输出重点：
- 哪个配置变了
- 哪些行业或 ETF 结果改变了
- 改变的原因是什么
""",
    "research_backtest_compare": """## 任务覆盖：参数调整与回测对比
适用问题：改动量窗口、改权重、改阈值、比较回测表现。

推荐流程：
1. 明确对比的参数组。
2. 对每组参数分别形成信号或读取对应输入。
3. 使用 `run_backtest` 做历史表现比较。
4. 输出收益、Sharpe、最大回撤、稳定性与适用环境差异。

输出重点：
- 参数对比表
- 核心指标优劣
- 该参数是否值得采纳
""",
    "research_conflict_check": """## 任务覆盖：信号冲突检查
适用问题：量化信号与负面清单/新闻/观察池是否冲突。

推荐流程：
1. 先定位高分行业或重点行业。
2. 调 `get_ic_overlay_config` 查观察池与否决规则。
3. 对冲突行业补充 `search_news_cn` / `search_news` 验证。
4. 输出冲突点、证据与建议处理方式。

输出重点：
- 哪些行业存在冲突
- 冲突来自哪类规则或新闻
- 最终保留、降级还是剔除
""",
    "rm_client_portfolio": """## 任务覆盖：客户适配组合与话术
适用问题：某客户风险等级适合什么组合、生成客户话术。

推荐流程：
1. 识别客户风险等级；若缺失，明确说明默认假设或提醒补充。
2. 基于当前市场信号形成 ETF 候选。
3. 按适当性和风险约束调整仓位与表达口径。
4. 输出客户可理解的话术、推荐理由、风险提示和观察点。

输出重点：
- 适合该客户的组合建议
- 简明理由
- 客户沟通话术
- 不确定性与风险揭示
""",
    "rm_explain_performance": """## 任务覆盖：解释上期推荐表现
适用问题：为什么跌了、为什么涨了、怎么给客户解释。

推荐流程：
1. 优先使用 `get_decision_history` 查上期 trace。
2. 对比本期与上期行业、象限、新闻、风险变化。
3. 若需要补充事实，再查新闻或行情。
4. 输出“发生了什么、为什么、现在怎么看、对客户怎么说”。

输出重点：
- 上期建议与本期变化
- 驱动因素
- 建议如何解释给客户
- 当前是否继续持有/观察
""",
    "rm_batch_talking_points": """## 任务覆盖：批量客户话术
适用问题：多个客户、多个风险等级、批量准备沟通材料。

推荐流程：
1. 识别客户列表及其风险等级。
2. 在统一市场观点基础上，按客户风险等级做轻重调整。
3. 保持统一模板输出，便于一线人员快速使用。

输出重点：
- 每位客户的简洁建议
- 专属话术
- 风险提示
""",
    "rm_market_specific": """## 任务覆盖：指定市场范围的客户建议
适用问题：客户只要港股 ETF、只看 A 股、只做美股等。

推荐流程：
1. 严格在指定市场内筛选行业与 ETF。
2. 说明市场边界对结论的影响。
3. 按客户口径给出简洁建议与风险提示。
""",
    "compliance_trace_review": """## 任务覆盖：Decision Trace 审查
适用问题：拉出本期 trace、查看完整决策链、审查留痕是否完整。

推荐流程：
1. 优先使用 `get_decision_history` 获取近期 trace。
2. 检查是否包含时间戳、配置版本、因子结果、象限、否决明细、组合、风险检查。
3. 输出审查结论、缺失项与是否具备审批基础。

输出重点：
- trace 是否完整
- 关键字段是否齐备
- 还缺什么材料
""",
    "compliance_risk_check": """## 任务覆盖：风控与合规检查
适用问题：组合是否违反风控规则、集中度或流动性要求。

推荐流程：
1. 获取当前组合与风险相关信息。
2. 检查单一 ETF 权重、行业集中度、现金留存、流动性等。
3. 若证据不足，明确指出无法完成审查。

输出重点：
- 通过 / 不通过 / 有条件通过
- 违规项或风险点
- 建议整改项
""",
    "compliance_veto_audit": """## 任务覆盖：否决行业审查
适用问题：这期否决了哪些行业、理由是否充分。

推荐流程：
1. 获取当前或最近一期 trace。
2. 结合观察池与负面清单复核否决明细。
3. 对理由不足的行业标记需补证据。

输出重点：
- 否决行业清单
- 对应规则或理由
- 证据充分性评价
""",
    "compliance_drawdown_check": """## 任务覆盖：回测最差回撤与历史风险
适用问题：回测最差月度回撤、历史最大回撤、尾部风险说明。

推荐流程：
1. 若已有回测结果，优先复用。
2. 必要时使用 `run_backtest` 获取历史表现。
3. 输出最大回撤、发生阶段、潜在风险暴露与审批建议。
""",
}


TASK_SUMMARIES: Dict[str, str] = {
    "generic": "通用任务，无法归入高频模板时使用。",
    "research_weekly_report": "生成本周/本日行业轮动周报或市场扫描。",
    "research_overlay_adjustment": "调整观察池或负面清单后重跑并比较变化。",
    "research_backtest_compare": "参数调整、窗口变化、权重变化后的回测对比。",
    "research_conflict_check": "检查量化信号与观察池、否决规则、新闻是否冲突。",
    "rm_client_portfolio": "针对客户风险等级生成 ETF 组合建议与沟通话术。",
    "rm_explain_performance": "解释上期推荐为何上涨或下跌，以及如何对客户解释。",
    "rm_batch_talking_points": "面向多个客户批量生成统一模板的话术。",
    "rm_market_specific": "在指定市场范围内生成客户建议，如仅港股或仅美股。",
    "compliance_trace_review": "拉取并审查本期或近期 Decision Trace。",
    "compliance_risk_check": "检查组合是否违反风控规则、集中度或流动性要求。",
    "compliance_veto_audit": "审查本期被否决的行业及理由是否充分。",
    "compliance_drawdown_check": "检查历史最大回撤、最差月度回撤与尾部风险。",
}


ROLE_TASK_MAP = {
    "researcher": [
        ("research_backtest_compare", ["回测", "sharpe", "最大回撤", "参数", "窗口", "权重", "对比"]),
        ("research_overlay_adjustment", ["观察池", "负面清单", "否决", "移除", "加入", "重跑", "重新跑"]),
        ("research_conflict_check", ["冲突", "分歧", "一致", "否命中", "负面消息", "利空"]),
        ("research_weekly_report", ["周报", "日报", "本周", "行业轮动", "市场扫描"]),
    ],
    "rm": [
        ("rm_batch_talking_points", ["批量", "5个客户", "多个客户", "客户列表"]),
        ("rm_explain_performance", ["为什么跌", "为什么涨", "解释", "归因", "上周推荐"]),
        ("rm_market_specific", ["只要港股", "只要美股", "只要a股", "只看港股", "只看美股", "指定市场"]),
        ("rm_client_portfolio", ["客户", "风险等级", "r1", "r2", "r3", "r4", "r5", "话术", "组合"]),
    ],
    "compliance": [
        ("compliance_drawdown_check", ["回撤", "最差月度", "最大回撤", "drawdown"]),
        ("compliance_veto_audit", ["否决", "剔除", "排除", "veto"]),
        ("compliance_risk_check", ["合规", "风控", "违反", "集中度", "流动性", "仓位上限"]),
        ("compliance_trace_review", ["trace", "决策链", "审批", "审查", "留痕"]),
    ],
}


def get_allowed_task_keys_for_role(role: str) -> list[str]:
    role_keys = [task_key for task_key, _ in ROLE_TASK_MAP.get(role, [])]
    return role_keys + ["generic"]


def infer_task_key(user_input: str, role: str) -> str:
    text = (user_input or "").lower()
    for task_key, keywords in ROLE_TASK_MAP.get(role, []):
        if any(keyword in text for keyword in keywords):
            return task_key
    return "generic"


def get_task_prompt(task_key: str) -> str:
    return TASK_PROMPTS.get(task_key, TASK_PROMPTS["generic"])

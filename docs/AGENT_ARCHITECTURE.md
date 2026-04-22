# AI Quant Assistant for ETF Rotation Strategies — 架构设计

---

## 一、定位

面向银行/券商财富管理与投研条线的 **ETF 行业轮动智能顾问引擎**。

以 LLM 为核心"投研大脑"，动态调用量化计算工具与外部信息源，自主完成推理、判断、反思，输出可审计的 ETF 组合建议与机构级交付物（周报、话术卡、Decision Trace）。

因子计算与回测由代码精确执行，LLM 不做数学；LLM 负责意图理解、工具调度、信息交叉验证、风险自检与自然语言输出。

---

## 二、用户角色与典型使用场景

### 2.1 投研团队

负责每周/每日行业轮动研究与报告撰写。

| 场景 | Agent 行为 |
|------|-----------|
| "生成本周 A 股行业轮动周报" | 拉数据→算因子→四象限→检查观察池/负面清单→映射ETF→对比上周变化→生成标准模板周报 |
| "把消费复苏链加入本期观察池，重新跑一遍" | 更新配置→重新执行信号流水线→主动对比修改前后结果差异 |
| "半导体从负面清单移除，看信号怎么变" | 移除规则→重跑→对比→标注变化原因 |
| "动量窗口从12个月改6个月，回测表现对比" | 分别用两组参数回测→输出 Sharpe/MDD/年化对比表 |
| "本期有没有量化信号和负面清单冲突的行业？" | 检查黄金区行业是否命中否决规则→如有冲突，搜新闻补充判断→标注分歧 |

### 2.2 RM / 投顾

面对客户的一线，需要适配客户画像的建议与沟通话术。

| 场景 | Agent 行为 |
|------|-----------|
| "张总是稳健型(R2)，生成适合他的ETF组合和话术" | 基于本周信号→按 R2 风险约束过滤→调整权重→生成话术卡片 |
| "客户问上周推荐的为什么跌了，帮我准备解释" | 查上期 Decision Trace→对比本期因子变化→搜相关新闻→生成归因话术 |
| "下午见5个客户(R2/R3/R3/R4/R2)，批量生成话术" | 根据各客户风险等级→分别适配→批量输出话术卡片 |
| "客户只要港股ETF" | 切换市场配置→在港股市场内独立跑信号→输出港股ETF组合 |

### 2.3 风控 / 合规

审查 Agent 产出，审批后才可对客。

| 场景 | Agent 行为 |
|------|-----------|
| "拉出本期 Decision Trace" | 输出完整决策链：因子值、象限、否决明细、规则版本、数据时间戳 |
| "检查本期组合是否违反风控规则" | 按机构配置的风控参数（仓位上限、集中度、流动性门槛等）逐项检查 |
| "这期否决了哪些行业？理由？" | 输出否决明细表（行业、规则ID、匹配条件、原因） |
| "回测最差月度回撤" | 调用回测工具→输出最大回撤及发生时间 |

---

## 三、Agent 核心循环：ReAct + Reflection

```
                        用户指令
                           │
                           ▼
                 ┌───────────────────┐
                 │    Reasoning      │ ◄──────────────────────┐
                 │  LLM 思考：       │                        │
                 │  当前任务需要      │                        │
                 │  哪些数据和工具？  │                        │
                 │  下一步该做什么？  │                        │
                 └────────┬──────────┘                        │
                          │                                   │
                          ▼                                   │
                 ┌───────────────────┐                        │
                 │    Action         │                        │
                 │  调用工具：        │                        │
                 │  get_market_data  │                        │
                 │  calc_factors     │                        │
                 │  search_news     │                        │
                 │  run_backtest    │                        │
                 │  ...             │                        │
                 └────────┬──────────┘                        │
                          │                                   │
                          ▼                                   │
                 ┌───────────────────┐                        │
                 │    Observation    │                        │
                 │  接收工具返回结果  │                        │
                 └────────┬──────────┘                        │
                          │                                   │
                          ▼                                   │
                 ┌───────────────────┐    发现问题/需补充信息  │
                 │    Reflection     │ ───────────────────────┘
                 │  审视结果：        │
                 │  · 是否合理？      │
                 │  · 是否符合风控？  │
                 │  · 需要补充信息？  │
                 └────────┬──────────┘
                          │ 满意
                          ▼
                 ┌───────────────────┐
                 │  输出交付物        │
                 │  + Decision Trace │
                 └───────────────────┘
```

LLM 可在此循环中往返多次。例如：算完因子后发现某行业信号异常强→主动搜新闻验证→发现负面消息→标记分歧→调整组合→再自检风控→最终输出。

---

## 四、Agent 工具集

### 4.1 量化工具（代码精确计算）

| 工具 | 输入 | 输出 |
|------|------|------|
| `get_market_data` | 市场、日期范围 | 行业指数+ETF 日线数据 |
| `calc_factors` | 行情数据、因子参数 | 因子表（每行业5因子+趋势分+共识分） |
| `score_quadrant` | 因子分数、阈值 | 每行业的象限标签与分数 |
| `map_etf` | 行业列表、ETF池、过滤条件 | 行业→推荐ETF（代码/规模/成交） |
| `run_backtest` | 历史信号、参数 | 年化收益/MDD/Sharpe/净值曲线 |
| `get_decision_history` | 日期范围 | 历史 Decision Trace 列表 |
| `get_etf_flow_detail` | ETF代码、天数 | 逐日资金流明细 |

### 4.2 信息工具（为 LLM 判断提供依据）

| 工具 | 输入 | 输出 |
|------|------|------|
| `search_news` | 关键词、日期范围 | 新闻标题+摘要 |
| `get_macro_events` | 日期范围 | 宏观事件日历 |
| `get_ic_overlay_config` | 市场 | 当前观察池+负面清单配置 |

### 4.3 输出工具

| 工具 | 输入 | 输出 |
|------|------|------|
| `save_decision_trace` | 完整决策数据 | JSON 存档 |
| `generate_report` | 结构化数据、模板类型、用户角色 | 周报/话术卡/审批单（按角色适配） |

---

## 五、LLM 在流程中的角色

| 环节 | LLM 做什么 | 代码做什么 |
|------|-----------|-----------|
| 意图理解 | 解析用户指令，判断所需工具与执行顺序 | — |
| 数据与因子 | — | 拉数据、计算因子（精确） |
| 信号解读 | 审视因子结果，识别异常，决定是否需要补充信息 | — |
| 主观判断 | 交叉验证：因子信号 vs 负面清单 vs 新闻，推理判断分歧 | 规则匹配（配置化部分） |
| 组合审视 | Reflection：检查集中度、风格偏移、与风控参数的合规性 | — |
| 归因分析 | 对比上期 Decision Trace 与本期因子变化，结合新闻归因 | 拉历史数据 |
| 交付物生成 | 按角色生成周报/话术/审批单 | 模板填充 |

---

## 六、因子体系

### 6.1 趋势因子（价）

| 因子 | 定义 | 参数 | 来源 |
|------|------|------|------|
| MA Score | MA10 > MA20 > MA60 → +2；部分多头 → +1；空头 → -2；其他 → 0 | 周期：10/20/60 | 华西证券 |
| 12M Momentum | 过去250交易日累计涨跌幅，去最近20天 | 窗口：250日，skip：20日 | 华鑫证券 |

### 6.2 共识因子（量）

| 因子 | 定义 | 参数 | 来源 |
|------|------|------|------|
| ETF资金流逆向 | 过去20天该行业ETF散户净流入之和，取反 | 窗口：20日 | 浙商证券 |
| 北向/主力净流入 | 过去20天北向或主力大单净流入之和 | 窗口：20日 | 华鑫证券 |
| 波动率收敛 | 过去20天ETF每日净流入的std，取反 | 窗口：20日 | thinking.txt |

### 6.3 合成

```
趋势得分 = w1 × rank(MA Score) + w2 × rank(12M Momentum)
共识得分 = w3 × rank(ETF逆向) + w4 × rank(北向) + w5 × rank(波动率收敛)
```

权重在 `config/factor_params.yaml` 中配置。

### 6.4 四象限

| 象限 | 条件 | 操作建议 |
|------|------|---------|
| 黄金配置区 | 趋势↑ + 共识↑ | 重仓 |
| 左侧观察区 | 趋势≈ + 共识↑ + 波动率收敛显著 | 关注等突破 |
| 高危警示区 | 趋势↑ + 共识↓ | 分批止盈 |
| 垃圾规避区 | 趋势↓ + 共识↓ | 回避 |

---

## 七、多市场支持

客户选定市场后，在该市场内独立执行完整信号流水线，不跨市场混合比较。

| 配置项 | A股 | 港股 | 美股 |
|--------|-----|------|------|
| 行业分类 | 申万一级 | 恒生行业分类 | GICS Sectors |
| 共识因子差异 | 北向资金 | 南向资金 | 机构持仓变动 |
| 数据源 | AKShare | AKShare + yfinance | yfinance |

同一套代码，切换 YAML 配置即可。

---

## 八、多 Agent 协作（可选）

可升级为多角色协作模式：

```
                      用户指令
                         │
                         ▼
               ┌──────────────────┐
               │   Coordinator    │
               │   分配任务、汇总  │
               └───┬──────┬───┬──┘
                   │      │   │
          ┌────────┘      │   └────────┐
          ▼               ▼            ▼
  ┌──────────────┐ ┌────────────┐ ┌────────────┐
  │Quant Analyst │ │  Macro     │ │   Risk     │
  │量化分析师     │ │ Strategist │ │  Manager   │
  │              │ │ 宏观策略师  │ │  风控经理   │
  │调用：         │ │调用：       │ │审视：       │
  │calc_factors  │ │search_news │ │风控参数检查 │
  │score_quadrant│ │get_macro   │ │集中度/流动性│
  │map_etf       │ │get_overlay │ │适当性匹配   │
  └──────┬───────┘ └─────┬──────┘ └─────┬──────┘
         │               │              │
         └───────┬────────┘              │
                 ▼                       │
         ┌──────────────┐                │
         │ Coordinator  │◄───────────────┘
         │ 汇总意见      │
         │ 解决分歧      │
         │ 输出+Trace   │
         └──────────────┘
```

当量化信号与宏观判断冲突时，Coordinator 综合各 Agent 意见后输出最终建议，并在 Decision Trace 中记录分歧与裁决理由。

---

## 九、配置体系（YAML）

所有参数可由机构自行调整，Agent 读取配置后执行。

| 配置文件 | 内容 |
|----------|------|
| `market_*.yaml` | 市场配置：行业分类列表、数据源选择 |
| `factor_params.yaml` | 因子参数：MA周期、动量窗口、合成权重 |
| `quadrant_thresholds.yaml` | 四象限阈值 |
| `subjective_pool.yaml` | 宏观观察池（外需/内需/防御子池及所含行业） |
| `veto_list.yaml` | 负面清单（规则ID、适用行业、条件、说明） |
| `etf_mapping.yaml` | 行业→ETF映射表、流动性/规模过滤阈值 |
| `risk_params.yaml` | 机构风控参数：仓位上限、集中度、流动性门槛、客户风险等级约束（由机构自行填入） |
| `report_templates/` | 周报/话术卡/审批单模板 |

---

## 十、Decision Trace（审计留痕）

每次 Agent 执行后生成 JSON 存档，包含：

| 字段 | 内容 |
|------|------|
| timestamp | 执行时间 |
| data_timestamp | 数据截止时间 |
| config_version | 配置文件版本号 |
| reasoning_chain | LLM 完整推理链（每步 Reasoning/Action/Observation/Reflection） |
| factor_scores | 每个行业的5因子值+趋势分+共识分 |
| quadrant_results | 四象限划分结果 |
| veto_details | 否决明细（行业、规则ID、原因） |
| etf_portfolio | 最终ETF组合+权重 |
| risk_check | 风控自检结果（通过/违规项） |
| approval_status | 审批状态（待审批/已审批/审批人/时间） |

风控/合规可随时调取任意日期的 Trace 进行审查。

---

## 十一、项目目录

```
AI_Quant_Assistant_for_ETF_Rotation_Strategies/
│
├── config/                         # YAML 配置
│   ├── market_a_share.yaml
│   ├── market_hk.yaml
│   ├── market_us.yaml
│   ├── factor_params.yaml
│   ├── quadrant_thresholds.yaml
│   ├── subjective_pool.yaml
│   ├── veto_list.yaml
│   ├── etf_mapping.yaml
│   └── risk_params.yaml
│
├── tools/                          # Agent 可调用的工具
│   ├── data_tools.py               # get_market_data, get_etf_flow_detail
│   ├── factor_tools.py             # calc_factors
│   ├── scoring_tools.py            # score_quadrant
│   ├── filter_tools.py             # get_ic_overlay_config
│   ├── mapping_tools.py            # map_etf
│   ├── backtest_tools.py           # run_backtest
│   ├── news_tools.py               # search_news, get_macro_events
│   ├── trace_tools.py              # save_decision_trace, get_decision_history
│   └── report_tools.py             # generate_report
│
├── agent/
│   ├── graph.py                    # LangGraph StateGraph（ReAct 循环）
│   ├── state.py                    # AgentState 定义
│   ├── prompts/
│   │   ├── system_prompt.py        # 系统提示词
│   │   ├── reflection_prompt.py    # 反思提示词
│   │   └── multi_agent_prompts.py  # 多角色提示词（可选）
│   └── multi_agent.py              # 多Agent协作（可选）
│
├── data/
│   ├── providers/
│   │   ├── base.py
│   │   ├── akshare_provider.py
│   │   └── yfinance_provider.py
│   └── cache/
│
├── backtest/
│   ├── runner.py
│   ├── portfolio.py
│   └── metrics.py
│
├── templates/                      # 输出模板
│   ├── weekly_report.md            # 周报模板
│   ├── talking_points.md           # 话术卡模板
│   └── approval_form.md            # 审批单模板
│
├── traces/                         # Decision Trace 存档（按日期）
│
├── main.py
└── requirements.txt
```

---

## 十二、技术栈

| 组件 | 选型 |
|------|------|
| Agent 框架 | LangGraph (StateGraph + ToolNode) |
| LLM | OpenAI / Claude / DeepSeek API |
| 数据 | AKShare + yfinance |
| 计算 | pandas + numpy |
| 配置 | YAML |
| 存储 | JSON（Decision Trace） |
| MCP | 新闻/宏观事件 API |

---

## 十三、实现优先级

| 阶段 | 内容 | 交付物 |
|------|------|--------|
| P0 | 工具函数 + LangGraph ReAct 循环 + A股因子 + 回测 | Agent 可执行"生成周报"完整流程 |
| P1 | 新闻MCP + LLM主观判断 + Reflection + Decision Trace | 完整 Agentic 体验 |
| P2 | 多Agent协作 + 多角色输出适配 + 多市场配置 | 展示扩展性 |
| P3 | Web UI / API + 审批流 | 产品化 demo |

---

## 十四、PPT 架构图标注

1. **ReAct Loop** — LLM 自主循环 Reasoning → Action → Observation → Reflection
2. **Decision Trace** — 完整推理链可回放，绑定数据时间戳+规则版本+审批记录
3. **Risk Guardrails** — 机构自定义风控参数，Agent 在 Reflection 阶段自检
4. **Human-in-the-loop** — Agent 建议须经审批后才可对客
5. **代码归代码，LLM 归 LLM** — 因子/回测精确计算，LLM 负责推理与判断

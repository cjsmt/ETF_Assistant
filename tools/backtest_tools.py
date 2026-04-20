from langchain_core.tools import tool


@tool
def run_backtest(
    market: str = "a_share",
    start_date: str = "2023-01-01",
    end_date: str = "2025-01-01",
    rebalance_freq: str = "monthly",
) -> str:
    """
    Run a historical backtest of the ETF rotation strategy.
    在每个调仓日运行因子+四象限→取黄金配置区→等权分配→用 ETF 价格算收益。

    Args:
        market: Market identifier (a_share, hk, us)
        start_date: Backtest start date YYYY-MM-DD
        end_date: Backtest end date YYYY-MM-DD
        rebalance_freq: Rebalance frequency ('monthly' or 'weekly')
    """
    try:
        from backtest.pipeline import run_backtest_pipeline

        result = run_backtest_pipeline(
            market=market,
            start_date=start_date,
            end_date=end_date,
            rebalance_freq=rebalance_freq,
        )
    except Exception as e:
        return f"[BACKTEST] 回测执行失败: {e}\n请确保 TUSHARE_TOKEN 已设置且数据可获取。"

    if "error" in result:
        return f"[BACKTEST] {result['error']}"

    m = result.get("metrics", {})
    ann_ret = m.get("annualized_return", 0) * 100
    mdd = m.get("max_drawdown", 0) * 100
    sharpe = m.get("sharpe_ratio", 0)
    calmar = m.get("calmar_ratio", 0)

    summary = (
        f"[BACKTEST] {market} | {start_date} ~ {end_date} | 调仓: {rebalance_freq}\n"
        f"年化收益: {ann_ret:.2f}% | 最大回撤: {mdd:.2f}% | 夏普: {sharpe:.2f} | Calmar: {calmar:.2f}\n"
        f"净值曲线与交易记录见 nav_series / trades。"
    )
    return summary

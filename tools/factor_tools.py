import os
import random
import time
import yaml
import pandas as pd
from langchain_core.tools import tool

from .data_tools import _get_provider, _load_market_config

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")

def _get_factor_provider(market: str):
    cfg = _load_market_config(market)
    return _get_provider(cfg)

def _load_factor_params() -> dict:
    with open(os.path.join(CONFIG_DIR, "factor_params.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def _calc_ma_score(close: pd.Series, short_p: int, mid_p: int, long_p: int) -> int:
    if len(close) < long_p:
        return 0
    ma_s = close.rolling(short_p).mean().iloc[-1]
    ma_m = close.rolling(mid_p).mean().iloc[-1]
    ma_l = close.rolling(long_p).mean().iloc[-1]
    if ma_s > ma_m > ma_l:
        return 2
    if ma_s < ma_m < ma_l:
        return -2
    if ma_s > ma_m or ma_m > ma_l:
        return 1
    if ma_s < ma_m or ma_m < ma_l:
        return -1
    return 0

def _calc_momentum(close: pd.Series, window: int, skip: int) -> float:
    if len(close) < window:
        return 0.0
    end_idx = len(close) - 1 - skip
    start_idx = end_idx - window + skip
    if start_idx < 0 or end_idx < 0:
        return 0.0
    return (close.iloc[end_idx] / close.iloc[start_idx]) - 1.0

def _calc_etf_flow_contrarian(net_inflow: pd.Series) -> float:
    if net_inflow.empty:
        return 0.0
    return -net_inflow.sum()

def _calc_smart_money(net_inflow: pd.Series) -> float:
    if net_inflow.empty:
        return 0.0
    return net_inflow.sum()

def _calc_volatility_convergence(net_inflow: pd.Series, window: int) -> float:
    if len(net_inflow) < window:
        return 0.0
    return -net_inflow.tail(window).std()

def _cross_sectional_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True)

@tool
def calc_factors(market: str = "a_share", start_date: str = "2024-01-01", end_date: str = "2025-03-01") -> str:
    """
    Calculate all 5 factors for every industry in the specified market
    and return composite trend + consensus scores.

    Args:
        market: Market identifier (a_share, hk, us)
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
    """
    params = _load_factor_params()
    market_cfg = _load_market_config(market)
    provider = _get_factor_provider(market)

    ma_p = params["ma_score"]
    mom_p = params["momentum"]
    efc_p = params["etf_flow_contrarian"]
    sm_p = params["smart_money"]
    vc_p = params["volatility_convergence"]
    weights = params["composite_weights"]

    rows = []
    provider_name = market_cfg.get("data_provider", "")
    for i, ind in enumerate(market_cfg.get("industries", [])):
        if i > 0:
            # AKShare/Tushare 均有接口限流，请求间隔避免 IP 超限
            if provider_name == "akshare":
                time.sleep(0.8 + random.random() * 0.7)
            elif provider_name == "tushare":
                time.sleep(0.6 + random.random() * 0.5)  # Tushare: 避免"IP数量超限"
        code, name = ind["code"], ind["name"]
        df = provider.get_industry_index_daily(code, start_date, end_date)
        if df.empty:
            continue

        ma = _calc_ma_score(df["close"], ma_p["short_period"], ma_p["mid_period"], ma_p["long_period"])
        mom = _calc_momentum(df["close"], mom_p["window"], mom_p["skip_recent"])

        etf_flow_val = 0.0
        smart_money_val = 0.0
        vol_conv_val = 0.0

        rows.append({
            "industry": name,
            "code": code,
            "ma_score": ma,
            "momentum": round(mom, 4),
            "etf_flow_contrarian": etf_flow_val,
            "smart_money": smart_money_val,
            "volatility_convergence": vol_conv_val,
        })

    if not rows:
        return "No factor data computed."

    result_df = pd.DataFrame(rows)

    result_df["ma_rank"] = _cross_sectional_rank(result_df["ma_score"])
    result_df["mom_rank"] = _cross_sectional_rank(result_df["momentum"])
    result_df["efc_rank"] = _cross_sectional_rank(result_df["etf_flow_contrarian"])
    result_df["sm_rank"] = _cross_sectional_rank(result_df["smart_money"])
    result_df["vc_rank"] = _cross_sectional_rank(result_df["volatility_convergence"])

    tw = weights["trend"]
    cw = weights["consensus"]
    result_df["trend_score"] = tw["ma_score"] * result_df["ma_rank"] + tw["momentum"] * result_df["mom_rank"]
    result_df["consensus_score"] = (
        cw["etf_flow_contrarian"] * result_df["efc_rank"]
        + cw["smart_money"] * result_df["sm_rank"]
        + cw["volatility_convergence"] * result_df["vc_rank"]
    )
    result_df["trend_score"] = result_df["trend_score"].round(4)
    result_df["consensus_score"] = result_df["consensus_score"].round(4)

    output = result_df[["industry", "ma_score", "momentum", "trend_score", "consensus_score"]].sort_values("trend_score", ascending=False)
    return output.to_string(index=False)


def calc_factors_df(market: str = "a_share", start_date: str = "2024-01-01", end_date: str = "2025-03-01") -> pd.DataFrame:
    """计算因子并返回 DataFrame，供回测等程序化调用。"""
    params = _load_factor_params()
    market_cfg = _load_market_config(market)
    provider = _get_factor_provider(market)

    ma_p = params["ma_score"]
    mom_p = params["momentum"]
    weights = params["composite_weights"]

    rows = []
    provider_name = market_cfg.get("data_provider", "")
    for i, ind in enumerate(market_cfg.get("industries", [])):
        if i > 0:
            if provider_name == "akshare":
                time.sleep(0.8 + random.random() * 0.7)
            elif provider_name == "tushare":
                time.sleep(0.6 + random.random() * 0.5)
        code, name = ind["code"], ind["name"]
        df = provider.get_industry_index_daily(code, start_date, end_date)
        if df.empty:
            continue

        ma = _calc_ma_score(df["close"], ma_p["short_period"], ma_p["mid_period"], ma_p["long_period"])
        mom = _calc_momentum(df["close"], mom_p["window"], mom_p["skip_recent"])
        rows.append({
            "industry": name, "code": code, "ma_score": ma, "momentum": round(mom, 4),
            "etf_flow_contrarian": 0.0, "smart_money": 0.0, "volatility_convergence": 0.0,
        })

    if not rows:
        return pd.DataFrame()

    result_df = pd.DataFrame(rows)
    result_df["ma_rank"] = _cross_sectional_rank(result_df["ma_score"])
    result_df["mom_rank"] = _cross_sectional_rank(result_df["momentum"])
    result_df["efc_rank"] = _cross_sectional_rank(result_df["etf_flow_contrarian"])
    result_df["sm_rank"] = _cross_sectional_rank(result_df["smart_money"])
    result_df["vc_rank"] = _cross_sectional_rank(result_df["volatility_convergence"])

    tw, cw = weights["trend"], weights["consensus"]
    result_df["trend_score"] = (tw["ma_score"] * result_df["ma_rank"] + tw["momentum"] * result_df["mom_rank"]).round(4)
    result_df["consensus_score"] = (
        cw["etf_flow_contrarian"] * result_df["efc_rank"] + cw["smart_money"] * result_df["sm_rank"]
        + cw["volatility_convergence"] * result_df["vc_rank"]
    ).round(4)
    return result_df[["industry", "trend_score", "consensus_score"]].sort_values("trend_score", ascending=False)

"""
Tushare 数据提供商，用于替代 AKShare 的 fund_etf_spot_em 等易断连接口。
支持自定义 API 地址（如代理）。含重试 + 超时保护。
"""
import os
import time
from typing import Dict, List

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from .base import BaseDataProvider

MAX_RETRIES = 3
RETRY_BASE_WAIT = 3  # 首次重试等待秒数，后续指数退避


def _to_ts_date(s: str) -> str:
    """YYYY-MM-DD -> YYYYMMDD"""
    return s.replace("-", "")


def _code_to_ts(code: str) -> str:
    """801120 -> 801120.SI(申万), 512400 -> 512400.SH, 159996 -> 159996.SZ"""
    if code.startswith(("51", "56")):
        return f"{code}.SH"
    if code.startswith(("15", "16")):
        return f"{code}.SZ"
    if code.startswith("80"):
        return f"{code}.SI"
    return f"{code}.SI"


def _retry_call(func, label: str, retries: int = MAX_RETRIES):
    """带指数退避重试的 API 调用包装。"""
    for attempt in range(retries):
        try:
            return func()
        except Exception as e:
            err_str = str(e)
            is_retryable = any(kw in err_str.lower() for kw in [
                "timed out", "timeout", "connection", "ip数量超限",
                "reset by peer", "broken pipe", "服务器繁忙",
            ])
            if is_retryable and attempt < retries - 1:
                wait = RETRY_BASE_WAIT * (2 ** attempt)
                print(f"[TushareProvider] {label} 第{attempt+1}次失败: {e} → {wait}s 后重试", flush=True)
                time.sleep(wait)
            else:
                raise
    return None  # unreachable


class TushareProvider(BaseDataProvider):
    """Tushare Pro 数据提供商（含重试 + 超时保护）。"""

    def __init__(self):
        import tushare as ts
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise ValueError("请设置环境变量 TUSHARE_TOKEN")
        self._pro = ts.pro_api(token)
        api_url = os.getenv("TUSHARE_API_URL", "").strip()
        if api_url:
            self._pro._DataApi__token = token
            self._pro._DataApi__http_url = api_url.rstrip("/")

    def get_industry_index_daily(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """申万行业指数需用 sw_daily，index_daily 不包含申万。"""
        ts_code = _code_to_ts(code)
        try:
            df = _retry_call(
                lambda: self._pro.sw_daily(
                    ts_code=ts_code,
                    start_date=_to_ts_date(start_date),
                    end_date=_to_ts_date(end_date),
                ),
                label=f"sw_daily({ts_code})",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "trade_date": "date", "open": "open", "high": "high",
                "low": "low", "close": "close", "vol": "volume"
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df[["date", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            print(f"[TushareProvider] index_daily({ts_code}) 最终失败: {e}")
            return pd.DataFrame()

    def get_etf_daily(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        ts_code = _code_to_ts(code)
        try:
            df = _retry_call(
                lambda: self._pro.fund_daily(
                    ts_code=ts_code,
                    start_date=_to_ts_date(start_date),
                    end_date=_to_ts_date(end_date),
                ),
                label=f"fund_daily({ts_code})",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "trade_date": "date", "open": "open", "high": "high",
                "low": "low", "close": "close", "vol": "volume", "amount": "turnover"
            })
            df["date"] = pd.to_datetime(df["date"])
            df["turnover"] = df["turnover"] * 1000  # 千元 -> 元
            return df[["date", "open", "high", "low", "close", "volume", "turnover"]]
        except Exception as e:
            print(f"[TushareProvider] fund_daily({ts_code}) 最终失败: {e}")
            return pd.DataFrame()

    def get_etf_fund_flow(self, code: str, days: int = 20) -> pd.DataFrame:
        # Tushare 无直接资金流接口，返回空
        return pd.DataFrame()

    def get_northbound_flow(self, start_date: str, end_date: str) -> pd.DataFrame:
        # Tushare 有 hsgt 相关接口，此处暂不实现
        return pd.DataFrame()

    def get_etf_info_batch(self, codes: List[str]) -> Dict[str, dict]:
        """从 fund_daily 取最新成交额；规模尝试 etf_share_size，否则置 0。"""
        result = {}
        for code in codes:
            ts_code = _code_to_ts(code)
            size = 0.0
            turnover = 0.0
            name = "N/A"
            try:
                # 最近交易日 fund_daily（取最近一个月内的最新）
                from datetime import datetime, timedelta
                end = datetime.now()
                start = end - timedelta(days=60)
                df = self._pro.fund_daily(
                    ts_code=ts_code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                if not df.empty:
                    df = df.sort_values("trade_date", ascending=False)
                    latest = df.iloc[0]
                    turnover = float(latest.get("amount", 0) or 0) * 1000  # 千元 -> 元
                # 尝试规模（etf_share_size 需高积分，失败则置 0）
                try:
                    sz = self._pro.etf_share_size(ts_code=ts_code)
                    if sz is not None and not sz.empty:
                        sz = sz.sort_values("trade_date", ascending=False)
                        # total_amount 总规模(万元) 或 tot_vol 总份额
                        val = sz.iloc[0].get("total_amount") or sz.iloc[0].get("tot_vol") or 0
                        size = float(val) * 1e4 if val else 0  # 万元->元
                except Exception:
                    pass
                # 名称从 fund_basic
                try:
                    fb = self._pro.fund_basic(ts_code=ts_code)
                    if fb is not None and not fb.empty:
                        name = str(fb.iloc[0].get("name", "N/A"))
                except Exception:
                    pass
            except Exception as e:
                print(f"[TushareProvider] get_etf_info({code}) 失败: {e}")
            result[code] = {
                "code": code,
                "name": name,
                "fund_size": size,
                "avg_daily_turnover": turnover,
            }
        return result

    def get_etf_info(self, code: str) -> dict:
        return self.get_etf_info_batch([code])[code]

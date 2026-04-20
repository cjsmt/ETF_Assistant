import random
import time
import akshare as ak
import pandas as pd
from typing import Dict, List, Callable, TypeVar
from .base import BaseDataProvider

T = TypeVar("T")

def _backoff_retry(fn: Callable[[], T], max_tries: int = 5, name: str = "api") -> T:
    """指数退避 + 随机抖动，减少被反爬识别为机器人的概率。"""
    for i in range(max_tries):
        try:
            return fn()
        except Exception as e:
            print(f"[AKShareProvider] {name} 第{i+1}次失败: {e}")
            if i == max_tries - 1:
                raise
            wait = min(30, 2 ** i) + random.random()  # 1~2s, 2~3s, 4~5s, 8~9s, 16~17s
            print(f"  → {wait:.1f}s 后重试（指数退避+抖动）...")
            time.sleep(wait)
    raise RuntimeError(f"{name} 重试 {max_tries} 次后仍失败")

class AKShareProvider(BaseDataProvider):
    """AKShare data provider for A-share market."""

    def _fetch_index_hist(self, code: str, max_retries: int = 3) -> pd.DataFrame:
        """拉取申万行业指数日线（指数退避+抖动重试）。"""
        try:
            return _backoff_retry(
                lambda: ak.index_hist_sw(symbol=code, period="day"),
                max_tries=max_retries,
                name=f"index_hist_sw({code})",
            )
        except Exception:
            return pd.DataFrame()

    def get_industry_index_daily(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        df = self._fetch_index_hist(code)
        if df.empty:
            return pd.DataFrame()
        try:
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume"
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            print(f"[AKShareProvider] Failed to process industry index {code}: {e}")
            return pd.DataFrame()

    def get_etf_daily(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="qfq")
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "turnover"
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            print(f"[AKShareProvider] Failed to get ETF {code}: {e}")
            return pd.DataFrame()

    def get_etf_fund_flow(self, code: str, days: int = 20) -> pd.DataFrame:
        try:
            df = ak.fund_etf_fund_daily_em()
            df = df[df["基金代码"] == code].tail(days)
            df = df.rename(columns={"净值日期": "date", "日增长额": "net_inflow"})
            df["date"] = pd.to_datetime(df["date"])
            return df[["date", "net_inflow"]].reset_index(drop=True)
        except Exception as e:
            print(f"[AKShareProvider] Failed to get ETF fund flow {code}: {e}")
            return pd.DataFrame()

    def get_northbound_flow(self, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向")
            df = df.rename(columns={"日期": "date", "净流入": "net_inflow"})
            df["date"] = pd.to_datetime(df["date"])
            df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
            return df.sort_values("date").reset_index(drop=True)
        except Exception as e:
            print(f"[AKShareProvider] Failed to get northbound flow: {e}")
            return pd.DataFrame()

    def _fetch_etf_spot_df(self, max_retries: int = 5) -> pd.DataFrame:
        """拉取全市场 ETF 行情。东方财富易断连，用指数退避+抖动。"""
        try:
            return _backoff_retry(
                ak.fund_etf_spot_em,
                max_tries=max_retries,
                name="fund_etf_spot_em",
            )
        except Exception:
            return pd.DataFrame()

    def _get_etf_info_yf_fallback(self, code: str) -> dict:
        """yfinance 备用：A 股 ETF 512xxx→.SS，159xxx→.SZ"""
        try:
            import yfinance as yf
            suffix = ".SS" if code.startswith(("51", "56")) else ".SZ"  # 上交所 51/56，深交所 15/16
            ticker = yf.Ticker(code + suffix)
            info = ticker.info
            vol = float(info.get("averageDailyVolume10Day", 0) or 0)
            close = float(info.get("previousClose", 0) or 0)
            turnover = vol * close if vol and close else 0
            return {
                "code": code,
                "name": info.get("shortName", "N/A"),
                "fund_size": float(info.get("totalAssets", 0) or 0),
                "avg_daily_turnover": turnover,
            }
        except Exception as e:
            return {"code": code, "name": "N/A", "fund_size": 0, "avg_daily_turnover": 0}

    def get_etf_info_batch(self, codes: List[str]) -> Dict[str, dict]:
        """一次拉取全市场 ETF；失败时用 yfinance 逐个拉取备用。"""
        df = self._fetch_etf_spot_df()
        result = {}
        if df.empty:
            if codes:
                print("[AKShareProvider] fund_etf_spot_em 不可用，改用 yfinance 备用...")
            for i, code in enumerate(codes):
                if i > 0:
                    time.sleep(0.4)  # 限流保护
                result[code] = self._get_etf_info_yf_fallback(code)
            return result
        for code in codes:
            row = df[df["代码"] == code]
            if row.empty:
                result[code] = {"code": code, "name": "N/A", "fund_size": 0, "avg_daily_turnover": 0}
            else:
                r = row.iloc[0]
                result[code] = {
                    "code": code,
                    "name": r.get("名称", "N/A"),
                    "fund_size": float(r.get("最新规模", 0) or 0),
                    "avg_daily_turnover": float(r.get("成交额", 0) or 0),
                }
        return result

    def get_etf_info(self, code: str) -> dict:
        return self.get_etf_info_batch([code]).get(code, {"code": code, "name": "N/A", "fund_size": 0, "avg_daily_turnover": 0})

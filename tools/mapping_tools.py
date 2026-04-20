import os
import yaml
from langchain_core.tools import tool

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def _get_etf_provider():
    """ETF 信息优先用 Tushare（稳定），无 token 时用 AKShare+yfinance 备用。"""
    if os.getenv("TUSHARE_TOKEN"):
        try:
            from data.providers.tushare_provider import TushareProvider
            return TushareProvider()
        except Exception as e:
            print(f"[map_etf] Tushare 初始化失败，回退 AKShare: {e}")
    from data.providers.akshare_provider import AKShareProvider
    return AKShareProvider()

@tool
def map_etf(industries: str, market: str = "a_share") -> str:
    """
    Map a list of industry names to recommended ETFs,
    applying liquidity and fund size filters.

    Args:
        industries: Comma-separated industry names (e.g. '有色金属,军工,银行')
        market: Market identifier
    """
    with open(os.path.join(CONFIG_DIR, "etf_mapping.yaml"), "r", encoding="utf-8") as f:
        mapping_cfg = yaml.safe_load(f)

    filters = mapping_cfg.get("filters", {})
    min_turnover = filters.get("min_daily_turnover", 50_000_000)
    min_size = filters.get("min_fund_size", 200_000_000)
    mapping = mapping_cfg.get("mapping", {})

    provider = _get_etf_provider()
    industry_list = [s.strip() for s in industries.split(",")]

    codes_to_fetch = []
    ind_to_entry = {}
    for ind_name in industry_list:
        if ind_name not in mapping:
            continue
        entry = mapping[ind_name]
        primary = entry.get("primary", {})
        code = primary.get("code", "")
        ind_to_entry[ind_name] = (entry, code)
        if code:
            codes_to_fetch.append(code)

    info_map = provider.get_etf_info_batch(codes_to_fetch) if codes_to_fetch else {}

    results = []
    for ind_name in industry_list:
        if ind_name not in mapping:
            results.append(f"{ind_name}: No ETF mapping found.")
            continue

        entry, code = ind_to_entry.get(ind_name, (mapping[ind_name], mapping[ind_name].get("primary", {}).get("code", "")))
        primary = entry.get("primary", {})
        name = primary.get("name", "")
        info = info_map.get(code, {"fund_size": 0, "avg_daily_turnover": 0})
        size = info.get("fund_size", 0)
        turnover = info.get("avg_daily_turnover", 0)

        passed = True
        notes = []
        if turnover < min_turnover:
            notes.append(f"turnover {turnover/1e6:.0f}M < {min_turnover/1e6:.0f}M threshold")
            passed = False
        if size < min_size:
            notes.append(f"size {size/1e8:.1f}亿 < {min_size/1e8:.1f}亿 threshold")
            passed = False

        status = "PASS" if passed else "FAIL"
        alt_str = ""
        alts = entry.get("alternatives", [])
        if alts:
            alt_str = f" | Alternatives: {', '.join(a['code']+' '+a['name'] for a in alts)}"

        result_line = f"{ind_name} -> {code} {name} [{status}] (size={size/1e8:.1f}亿, turnover={turnover/1e6:.0f}M){' (' + '; '.join(notes) + ')' if notes else ''}{alt_str}"
        results.append(result_line)

    return "\n".join(results)

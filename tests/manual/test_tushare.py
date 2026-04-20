"""
单独测试 Tushare 能否正常拉取数据。
用法: python test_tushare.py
需在 .env 中配置 TUSHARE_TOKEN 和（可选）TUSHARE_API_URL
"""
import os
from dotenv import load_dotenv
load_dotenv()

def main():
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        print("请设置 .env 中的 TUSHARE_TOKEN")
        return

    print("=" * 50)
    print("Tushare 连通性测试")
    print("=" * 50)

    try:
        from data.providers.tushare_provider import TushareProvider
        provider = TushareProvider()
    except Exception as e:
        print(f"初始化失败: {e}")
        return

    # 1. ETF 信息（替代 fund_etf_spot_em）
    print("\n1. 测试 get_etf_info_batch（512400 有色金属ETF）...")
    try:
        info = provider.get_etf_info_batch(["512400"])
        r = info["512400"]
        print(f"   ✓ 成功: {r['name']}, 规模={r['fund_size']/1e8:.1f}亿, 成交额={r['avg_daily_turnover']/1e6:.0f}万")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    # 2. 申万行业指数
    print("\n2. 测试 get_industry_index_daily（801120 食品饮料）...")
    try:
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=60)
        df = provider.get_industry_index_daily("801120", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        print(f"   ✓ 成功，共 {len(df)} 条")
        if not df.empty:
            print(f"   最近: {df['date'].iloc[-1]} 收盘={df['close'].iloc[-1]}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    print("\n" + "=" * 50)

if __name__ == "__main__":
    main()

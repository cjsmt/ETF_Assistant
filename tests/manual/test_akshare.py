"""
单独测试 AKShare 是否能正常拉取东方财富数据。
用法: python test_akshare.py
"""
import akshare as ak

def main():
    print("=" * 50)
    print("AKShare 连通性测试")
    print("=" * 50)
    print(f"akshare 版本: {ak.__version__}\n")

    # 1. 测试 fund_etf_spot_em（ETF 全市场行情）
    print("1. 测试 fund_etf_spot_em（ETF 行情）...")
    try:
        df = ak.fund_etf_spot_em()
        print(f"   ✓ 成功，共 {len(df)} 只 ETF")
        if not df.empty:
            print(f"   示例: {df['代码'].iloc[0]} {df['名称'].iloc[0]}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    # 2. 测试 index_hist_sw（申万行业指数日线）
    print("\n2. 测试 index_hist_sw（801120 食品饮料）...")
    try:
        df = ak.index_hist_sw(symbol="801120", period="day")
        print(f"   ✓ 成功，共 {len(df)} 条日线")
        if not df.empty:
            last = df.iloc[-1]
            print(f"   最近: {last['日期']} 收盘={last['收盘']}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    print("\n" + "=" * 50)

if __name__ == "__main__":
    main()

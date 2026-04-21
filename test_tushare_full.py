"""测试 Tushare 作为主数据源的全流程"""
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

end = datetime.now()
start = end - timedelta(days=30)
s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

from tools.data_tools import get_market_data
from tools.factor_tools import calc_factors
from tools.mapping_tools import map_etf

print("1. get_market_data...")
print(get_market_data.invoke({"market": "a_share", "start_date": s, "end_date": e})[:300])
print("\n2. calc_factors...")
print(calc_factors.invoke({"market": "a_share", "start_date": s, "end_date": e})[:400])
print("\n3. map_etf...")
print(map_etf.invoke({"industries": "有色金属,银行", "market": "a_share"}))
print("\n全流程 OK")

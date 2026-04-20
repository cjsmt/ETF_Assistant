"""
News & Macro tools: AKShare (A-share, free) + Jina (Chinese search) + Alpha Vantage (global, macro)
"""
import os
from datetime import datetime, timedelta

from langchain_core.tools import tool

try:
    import akshare as ak
except ImportError:
    ak = None

try:
    import requests
except ImportError:
    requests = None


def _fetch_akshare_flash(keywords: str = "", limit: int = 20) -> list:
    """Fetch A-share flash news from 财联社 via AKShare."""
    if ak is None:
        return []
    results = []
    try:
        df = ak.stock_zh_a_alerts_cls()
        if df is None or df.empty:
            return []
        df = df.head(limit * 3)
        for _, row in df.iterrows():
            text = str(row.get("快讯信息", row.get("content", "")))
            time_val = str(row.get("时间", row.get("datetime", "")))
            if keywords:
                kws = [k.strip() for k in keywords.split() if k.strip()]
                if kws and not any(kw in text for kw in kws):
                    continue
            results.append({"title": text[:80] + ("..." if len(text) > 80 else ""), "content": text[:500], "time": time_val, "source": "财联社"})
            if len(results) >= limit:
                break
    except Exception as e:
        pass
    return results


def _fetch_akshare_js_news(limit: int = 15) -> list:
    """Fetch real-time finance news from 金十数据 via AKShare."""
    if ak is None:
        return []
    results = []
    try:
        for indicator in ["最新资讯", "最新数据"]:
            try:
                df = ak.js_news(indicator=indicator)
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        content = str(row.get("content", row.get("新闻内容", "")))
                        dt = str(row.get("datetime", row.get("发布时间", "")))
                        results.append({"title": content[:80] + "...", "content": content[:500], "time": dt, "source": "金十数据"})
                        if len(results) >= limit:
                            break
                break
            except Exception:
                continue
    except Exception:
        pass
    return results


def _fetch_jina(keywords: str, api_key: str, limit: int = 3) -> list:
    """Search and scrape via Jina (Chinese-friendly)."""
    if not api_key or not requests:
        return []
    results = []
    try:
        search_url = f"https://s.jina.ai/?q={keywords}&n=5"
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json", "X-Respond-With": "no-content"}
        r = requests.get(search_url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        urls = []
        for item in data.get("data", []):
            if item.get("url"):
                urls.append(item["url"])
        urls = urls[:limit] if limit else urls[:3]
        for url in urls:
            try:
                reader_url = f"https://r.jina.ai/{url}"
                headers_reader = {"Accept": "application/json", "Authorization": api_key, "X-Timeout": "10"}
                rr = requests.get(reader_url, headers=headers_reader, timeout=12)
                if rr.status_code != 200:
                    continue
                j = rr.json()
                d = j.get("data", {})
                results.append({
                    "title": d.get("title", "")[:100],
                    "content": (d.get("content") or d.get("description", ""))[:800],
                    "time": d.get("publishedTime", "unknown"),
                    "source": "Jina",
                    "url": d.get("url", url),
                })
            except Exception:
                continue
    except Exception:
        pass
    return results


def _fetch_alphavantage(keywords: str = "", topics: str = "", api_key: str = "", limit: int = 15) -> list:
    """Fetch global news from Alpha Vantage NEWS_SENTIMENT."""
    if not api_key or not requests:
        return []
    results = []
    key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        return []
    try:
        params = {"function": "NEWS_SENTIMENT", "apikey": key, "sort": "LATEST", "limit": limit}
        if topics:
            params["topics"] = topics
        time_to = datetime.now()
        time_from = time_to - timedelta(days=7)
        params["time_from"] = time_from.strftime("%Y%m%dT%H%M")
        params["time_to"] = time_to.strftime("%Y%m%dT%H%M")
        r = requests.get("https://www.alphavantage.co/query", params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        if "Error Message" in j or "Note" in j:
            return []
        feed = j.get("feed", [])
        for a in feed[:limit]:
            title = a.get("title", "")
            summary = a.get("summary", "")[:600]
            if keywords:
                kws = [k.strip() for k in keywords.split() if k.strip()]
                if kws and not any(kw in title or kw in summary for kw in kws):
                    continue
            results.append({
                "title": title,
                "content": summary,
                "time": a.get("time_published", "unknown"),
                "source": "Alpha Vantage",
                "url": a.get("url", ""),
            })
    except Exception:
        pass
    return results


def _format_news(items: list) -> str:
    if not items:
        return ""
    lines = []
    for i, x in enumerate(items, 1):
        lines.append(f"[{i}] {x.get('title', 'N/A')}")
        lines.append(f"    内容: {x.get('content', '')[:400]}...")
        lines.append(f"    时间: {x.get('time', 'N/A')} | 来源: {x.get('source', 'N/A')}")
        lines.append("")
    return "\n".join(lines).strip()


@tool
def search_news(keywords: str, days: int = 7, source: str = "all") -> str:
    """
    Search recent news by keywords. Supports multiple sources:
    - AKShare: A-share flash (财联社) + finance news (金十), free
    - Jina: Web search + scrape, Chinese-friendly (needs JINA_API_KEY)
    - Alpha Vantage: Global news (needs ALPHAVANTAGE_API_KEY)
    
    Args:
        keywords: Search keywords, e.g. '半导体 关税', '有色金属'
        days: Number of recent days to consider
        source: 'all' (default), 'akshare', 'jina', 'alphavantage'
    """
    all_items = []
    if source in ("all", "akshare"):
        items = _fetch_akshare_flash(keywords, limit=15)
        all_items.extend(items)
        if not keywords:
            items2 = _fetch_akshare_js_news(limit=10)
            all_items.extend(items2)
    if source in ("all", "jina"):
        jk = os.environ.get("JINA_API_KEY")
        if jk:
            items = _fetch_jina(keywords or "A股 财经", jk, limit=3)
            all_items.extend(items)
    if source in ("all", "alphavantage"):
        avk = os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")
        if avk:
            topics = "financial_markets,technology,economy_macro"
            items = _fetch_alphavantage(keywords, topics, avk, limit=10)
            all_items.extend(items)
    if not all_items:
        hint = []
        if source in ("all", "akshare") and ak:
            hint.append("AKShare 返回空，可能网络或接口变化")
        if source in ("all", "jina") and not os.environ.get("JINA_API_KEY"):
            hint.append("未配置 JINA_API_KEY")
        if source in ("all", "alphavantage") and not (os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")):
            hint.append("未配置 ALPHAVANTAGE_API_KEY")
        return f"未找到与 '{keywords}' 相关的新闻。" + (" " + " ".join(hint) if hint else "")
    seen = set()
    deduped = []
    for x in all_items:
        t = (x.get("title", ""), x.get("content", "")[:100])
        if t not in seen:
            seen.add(t)
            deduped.append(x)
    return _format_news(deduped[:25])


@tool
def search_news_cn(keywords: str, limit: int = 20) -> str:
    """
    Search A-share / Chinese finance news. Uses AKShare (free) first, then Jina if configured.
    Best for: 半导体 关税, 有色金属 供需, 电力设备 产能
    
    Args:
        keywords: Chinese keywords
        limit: Max results
    """
    items = _fetch_akshare_flash(keywords, limit=limit)
    jk = os.environ.get("JINA_API_KEY")
    if jk:
        items.extend(_fetch_jina(keywords or "A股 财经 新闻", jk, limit=5))
    if not items:
        return f"未找到与 '{keywords}' 相关的中国财经新闻。"
    return _format_news(items[:limit])


def _fetch_macro_alphavantage(api_key: str, limit: int = 15) -> list:
    """Macro news from Alpha Vantage (economy topics)."""
    if not api_key or not requests:
        return []
    try:
        key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")
        if not key:
            return []
        params = {
            "function": "NEWS_SENTIMENT",
            "apikey": key,
            "topics": "economy_macro,economy_monetary,economy_fiscal",
            "sort": "LATEST",
            "limit": limit,
        }
        time_to = datetime.now()
        time_from = time_to - timedelta(days=14)
        params["time_from"] = time_from.strftime("%Y%m%dT%H%M")
        params["time_to"] = time_to.strftime("%Y%m%dT%H%M")
        r = requests.get("https://www.alphavantage.co/query", params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        if "Error Message" in j or "Note" in j:
            return []
        feed = j.get("feed", [])
        return [{"title": a.get("title", ""), "content": (a.get("summary", ""))[:500], "time": a.get("time_published", "")} for a in feed]
    except Exception:
        return []


def _fetch_macro_akshare() -> list:
    """Macro / economic flash from 金十 via AKShare."""
    items = _fetch_akshare_js_news(limit=15)
    return items


@tool
def get_macro_events(days: int = 14) -> str:
    """
    Get recent macro economic events / news. Uses:
    - AKShare (金十财经快讯, free)
    - Alpha Vantage economy topics (when ALPHAVANTAGE_API_KEY configured)
    
    Args:
        days: Number of days to look back
    """
    items = _fetch_macro_akshare()
    avk = os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")
    if avk:
        items.extend(_fetch_macro_alphavantage(avk, limit=10))
    if not items:
        return "暂无宏观事件数据。若需全球宏观新闻，请配置 ALPHAVANTAGE_API_KEY。"
    seen = set()
    deduped = []
    for x in items:
        t = x.get("title", "") or x.get("content", "")[:80]
        if t and t not in seen:
            seen.add(t)
            deduped.append(x)
    return _format_news(deduped[:20])

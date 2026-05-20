import asyncio
import re
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


async def search_naver(client: httpx.AsyncClient, query: str) -> list:
    results = []
    try:
        resp = await client.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": query, "display": 30, "sort": "asc"},
            headers={
                **HEADERS,
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            timeout=10,
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            price = int(item.get("lprice", 0))
            if price > 0:
                results.append({
                    "title": re.sub(r"<[^>]+>", "", item.get("title", "")),
                    "price": price,
                    "mall": item.get("mallName", ""),
                    "link": item.get("link", ""),
                    "source": "네이버",
                })
    except Exception as e:
        print(f"[Naver] {e}")
    return results


async def search_danawa(client: httpx.AsyncClient, query: str) -> list:
    results = []
    try:
        resp = await client.get(
            "https://search.danawa.com/dsearch.php",
            params={"query": query, "tab": "goods"},
            headers={**HEADERS, "Referer": "https://danawa.com"},
            timeout=15,
            follow_redirects=True,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select("li.prod-item")[:25]:
            title_el = item.select_one(".prod-name a")
            price_el = item.select_one(".price-sect strong")
            if not title_el or not price_el:
                continue
            price_text = re.sub(r"[^0-9]", "", price_el.get_text())
            if not price_text:
                continue
            link = title_el.get("href", "")
            if link and not link.startswith("http"):
                link = "https://www.danawa.com" + link
            results.append({
                "title": title_el.get_text(strip=True),
                "price": int(price_text),
                "mall": "다나와",
                "link": link,
                "source": "다나와",
            })
    except Exception as e:
        print(f"[Danawa] {e}")
    return results


async def search_enuri(client: httpx.AsyncClient, query: str) -> list:
    results = []
    try:
        resp = await client.get(
            "https://search.enuri.com/search.jsp",
            params={"keyword": query},
            headers={**HEADERS, "Referer": "https://enuri.com"},
            timeout=15,
            follow_redirects=True,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        # 에누리 상품 리스트 파싱
        for item in soup.select(".searchBriefList li, .sch_result_list li")[:25]:
            title_el = item.select_one(".tit a, .name a")
            price_el = item.select_one(".price .num, .lowest_price .num, strong.price")
            if not title_el or not price_el:
                continue
            price_text = re.sub(r"[^0-9]", "", price_el.get_text())
            if not price_text:
                continue
            link = title_el.get("href", "")
            if link and not link.startswith("http"):
                link = "https://www.enuri.com" + link
            results.append({
                "title": title_el.get_text(strip=True),
                "price": int(price_text),
                "mall": "에누리",
                "link": link,
                "source": "에누리",
            })
    except Exception as e:
        print(f"[Enuri] {e}")
    return results


@app.get("/api/search")
async def search(q: str):
    if not q.strip():
        return []
    async with httpx.AsyncClient() as client:
        naver, danawa, enuri = await asyncio.gather(
            search_naver(client, q),
            search_danawa(client, q),
            search_enuri(client, q),
        )
    combined = naver + danawa + enuri
    combined.sort(key=lambda x: x["price"])
    return combined


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", encoding="utf-8") as f:
        return f.read()

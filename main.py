import asyncio
import re
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import httpx
from playwright.async_api import async_playwright, Browser, Playwright
from dotenv import load_dotenv

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_playwright: Playwright = None
_browser: Browser = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _playwright, _browser
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    yield
    await _browser.close()
    await _playwright.stop()


app = FastAPI(lifespan=lifespan)


# ── 네이버 쇼핑 API ──────────────────────────────────────────
async def search_naver(client: httpx.AsyncClient, query: str) -> list:
    results = []
    try:
        resp = await client.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": query, "display": 40, "sort": "asc"},
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


# ── 다나와 Playwright 크롤링 ─────────────────────────────────
async def search_danawa(query: str) -> list:
    results = []
    page = await _browser.new_page()
    try:
        await page.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9"})
        await page.goto(
            f"https://search.danawa.com/dsearch.php?query={query}&tab=goods",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        # JS 렌더링 대기
        await page.wait_for_selector(".prod-item, ul.product_list li", timeout=15000)

        items = await page.query_selector_all(".prod-item")
        if not items:
            items = await page.query_selector_all("ul.product_list li")

        for item in items[:25]:
            try:
                title_el = await item.query_selector(".prod-name a, .tit-area a, a.prod_name")
                price_el = await item.query_selector(".price-sect strong, .lowest-price .price, .prod_pricelist em")
                if not title_el or not price_el:
                    continue
                title = await title_el.inner_text()
                price_text = re.sub(r"[^0-9]", "", await price_el.inner_text())
                if not price_text:
                    continue
                link = await title_el.get_attribute("href") or ""
                if link and not link.startswith("http"):
                    link = "https://www.danawa.com" + link
                results.append({
                    "title": title.strip(),
                    "price": int(price_text),
                    "mall": "다나와",
                    "link": link,
                    "source": "다나와",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[Danawa] {e}")
    finally:
        await page.close()
    return results


# ── 에누리 Playwright 크롤링 ─────────────────────────────────
async def search_enuri(query: str) -> list:
    results = []
    page = await _browser.new_page()
    try:
        await page.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9"})
        await page.goto(
            f"https://www.enuri.com/search.jsp?keyword={query}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        # JS 렌더링 대기
        await page.wait_for_selector(".goods-list li, .list-body li, .srp-goods-list li", timeout=15000)

        items = await page.query_selector_all(".goods-list li, .list-body li, .srp-goods-list li")

        for item in items[:25]:
            try:
                title_el = await item.query_selector(".tit a, .name a, a.goods-name, .goods-tit a")
                price_el = await item.query_selector(".lowest-price .num, .price .num, strong.price, .price-area strong")
                if not title_el or not price_el:
                    continue
                title = await title_el.inner_text()
                price_text = re.sub(r"[^0-9]", "", await price_el.inner_text())
                if not price_text:
                    continue
                link = await title_el.get_attribute("href") or ""
                if link and not link.startswith("http"):
                    link = "https://www.enuri.com" + link
                results.append({
                    "title": title.strip(),
                    "price": int(price_text),
                    "mall": "에누리",
                    "link": link,
                    "source": "에누리",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[Enuri] {e}")
    finally:
        await page.close()
    return results


# ── API 엔드포인트 ────────────────────────────────────────────
@app.get("/api/search")
async def search(q: str):
    if not q.strip():
        return []
    async with httpx.AsyncClient() as client:
        naver_task = search_naver(client, q)
        danawa_task = search_danawa(q)
        enuri_task = search_enuri(q)
        naver, danawa, enuri = await asyncio.gather(naver_task, danawa_task, enuri_task)

    combined = naver + danawa + enuri
    combined.sort(key=lambda x: x["price"])
    return combined


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", encoding="utf-8") as f:
        return f.read()

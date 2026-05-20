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
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-setuid-sandbox"],
    )
    yield
    await _browser.close()
    await _playwright.stop()


app = FastAPI(lifespan=lifespan)


# ── 네이버 쇼핑 API (다중 페이지 + 상품 그룹화) ─────────────
async def search_naver(client: httpx.AsyncClient, query: str) -> list:
    raw = []
    try:
        # 3페이지 병렬 호출 (최대 300개)
        async def fetch_page(start: int):
            resp = await client.get(
                "https://openapi.naver.com/v1/search/shop.json",
                params={"query": query, "display": 100, "start": start, "sort": "asc"},
                headers={
                    **HEADERS,
                    "X-Naver-Client-Id": NAVER_CLIENT_ID,
                    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
                },
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])

        pages = await asyncio.gather(
            fetch_page(1), fetch_page(101), fetch_page(201),
            return_exceptions=True,
        )
        for page in pages:
            if isinstance(page, list):
                raw.extend(page)
    except Exception as e:
        print(f"[Naver] {e}")

    # productId 기준으로 그룹화
    groups: dict = {}
    for item in raw:
        price = int(item.get("lprice", 0))
        if price <= 0:
            continue
        pid = item.get("productId") or item.get("title", "")
        title = re.sub(r"<[^>]+>", "", item.get("title", ""))
        seller = {
            "mall": item.get("mallName", ""),
            "price": price,
            "link": item.get("link", ""),
        }
        if pid not in groups:
            groups[pid] = {"title": title, "sellers": []}
        groups[pid]["sellers"].append(seller)

    results = []
    for g in groups.values():
        # 가격 중복 제거 후 오름차순 정렬
        seen_prices = set()
        unique_sellers = []
        for s in sorted(g["sellers"], key=lambda x: x["price"]):
            key = (s["price"], s["mall"])
            if key not in seen_prices:
                seen_prices.add(key)
                unique_sellers.append(s)
        best = unique_sellers[0]
        results.append({
            "title": g["title"],
            "price": best["price"],
            "mall": best["mall"],
            "link": best["link"],
            "source": "네이버",
            "sellers": unique_sellers,  # 모든 판매처
        })

    return results


# ── 다나와 Playwright ─────────────────────────────────────────
async def search_danawa(query: str) -> list:
    results = []
    page = await _browser.new_page()
    try:
        await page.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9"})
        await page.goto(
            f"https://search.danawa.com/dsearch.php?query={query}&tab=goods",
            wait_until="domcontentloaded",
            timeout=25000,
        )
        # JS 렌더링 대기
        await page.wait_for_timeout(3000)

        # JavaScript로 직접 DOM에서 상품 추출
        products = await page.evaluate("""() => {
            const items = [];
            const selectors = ['.prod-item', 'li.prod-item', '.product_list li', '.danawa_product_list li'];
            let elements = [];
            for (const sel of selectors) {
                elements = Array.from(document.querySelectorAll(sel));
                if (elements.length > 0) break;
            }
            elements.slice(0, 30).forEach(el => {
                const titleEl = el.querySelector('.prod-name a, .tit-area a, a.prod_name, .prod_name a');
                const priceEl = el.querySelector('.price-sect strong, .lowest-price .price, .prod_pricelist em, .price_list .price');
                if (!titleEl || !priceEl) return;
                const priceText = priceEl.textContent.replace(/[^0-9]/g, '');
                if (!priceText || priceText.length < 2) return;
                items.push({
                    title: titleEl.textContent.trim(),
                    price: parseInt(priceText),
                    link: titleEl.href || ''
                });
            });
            return items;
        }""")

        print(f"[Danawa] found {len(products)} items for '{query}'")
        for item in products:
            if item["price"] > 0:
                results.append({
                    "title": item["title"],
                    "price": item["price"],
                    "mall": "다나와",
                    "link": item["link"],
                    "source": "다나와",
                })
    except Exception as e:
        print(f"[Danawa] error: {e}")
    finally:
        await page.close()
    return results


# ── 에누리 Playwright ─────────────────────────────────────────
async def search_enuri(query: str) -> list:
    results = []
    page = await _browser.new_page()
    try:
        await page.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9"})
        await page.goto(
            f"https://www.enuri.com/search.jsp?keyword={query}",
            wait_until="domcontentloaded",
            timeout=25000,
        )
        await page.wait_for_timeout(3000)

        products = await page.evaluate("""() => {
            const items = [];
            const selectors = ['.goods-list li', '.list-body li', '.srp-goods-list li', '.goods_list li', 'ul.goods-list > li'];
            let elements = [];
            for (const sel of selectors) {
                elements = Array.from(document.querySelectorAll(sel));
                if (elements.length > 0) break;
            }
            elements.slice(0, 30).forEach(el => {
                const titleEl = el.querySelector('.tit a, .name a, a.goods-name, .goods-tit a, .goods_name a');
                const priceEl = el.querySelector('.lowest-price .num, .price .num, strong.price, .price-area strong, .lowest_price .price');
                if (!titleEl || !priceEl) return;
                const priceText = priceEl.textContent.replace(/[^0-9]/g, '');
                if (!priceText || priceText.length < 2) return;
                items.push({
                    title: titleEl.textContent.trim(),
                    price: parseInt(priceText),
                    link: titleEl.href || ''
                });
            });
            return items;
        }""")

        print(f"[Enuri] found {len(products)} items for '{query}'")
        for item in products:
            if item["price"] > 0:
                results.append({
                    "title": item["title"],
                    "price": item["price"],
                    "mall": "에누리",
                    "link": item["link"],
                    "source": "에누리",
                })
    except Exception as e:
        print(f"[Enuri] error: {e}")
    finally:
        await page.close()
    return results


# ── API ───────────────────────────────────────────────────────
@app.get("/api/search")
async def search(q: str):
    if not q.strip():
        return []
    try:
        async with httpx.AsyncClient() as client:
            naver, danawa, enuri = await asyncio.gather(
                search_naver(client, q),
                search_danawa(q),
                search_enuri(q),
                return_exceptions=True,
            )
        def safe(r): return r if isinstance(r, list) else []
        combined = safe(naver) + safe(danawa) + safe(enuri)
        combined.sort(key=lambda x: x["price"])
        return combined
    except Exception as e:
        print(f"[Search] fatal error: {e}")
        # Playwright 실패해도 네이버만이라도 반환
        try:
            async with httpx.AsyncClient() as client:
                naver = await search_naver(client, q)
            naver.sort(key=lambda x: x["price"])
            return naver
        except Exception as e2:
            print(f"[Search] naver fallback failed: {e2}")
            return []


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", encoding="utf-8") as f:
        return f.read()

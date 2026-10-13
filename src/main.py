"""Shopify Scraper actor: product catalogs via the public /products.json API.

Each store domain's public products.json endpoint is paginated (limit=250).
Pay-per-event charges are emitted once per successfully parsed product —
never on failures. HTTP-only: no headless browser.

NOTE: plain Python HTTP clients (httpx/urllib) are blocked by Cloudflare's
bot detection (TLS fingerprinting) on many Shopify stores. We use curl_cffi
with Chrome TLS impersonation instead, which passes those checks.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

from curl_cffi import CurlError
from curl_cffi.requests import AsyncSession

from apify import Actor

EVENT_PRODUCT = 'product-scraped'
PAGE_LIMIT = 250
REQUEST_TIMEOUT = 30
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


def check_public_host(host: str) -> tuple[bool, str]:
    """SSRF guard: refuse hosts that resolve to non-public IPs."""
    if not host:
        return False, 'missing hostname'
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, 'DNS resolution failed'
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, 'unparseable resolved address'
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False, f'blocked: {host} resolves to non-public IP {ip}'
    return True, ''


def normalize_store(raw: str) -> str:
    """Turn user input ('allbirds.com', 'https://www.allbirds.com/') into a bare host."""
    s = raw.strip().lower()
    if not s:
        return ''
    if '://' not in s:
        s = 'https://' + s
    return urlparse(s).hostname or ''


def to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_proxies() -> dict | None:
    """Pick up proxy configuration from the environment, if present."""
    proxy = (os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
             or os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY'))
    if proxy:
        return {'http': proxy, 'https': proxy}
    return None


async def charge_event(event_name: str) -> None:
    """Bill one pay-per-event charge. Never let billing break the run."""
    try:
        await Actor.charge(event_name=event_name)
    except Exception as exc:
        Actor.log.warning(f'Pay-per-event charge {event_name!r} failed: {exc}')


async def fetch_currency(session: AsyncSession, store: str) -> str | None:
    """Best-effort currency lookup; most stores don't expose it publicly."""
    try:
        resp = await session.get(f'https://{store}/shop.json')
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data.get('currency') or (data.get('shop') or {}).get('currency')
    except Exception:
        pass
    return None


def parse_product(store: str, currency: str | None, product: dict) -> dict:
    variants = product.get('variants') or []
    first = variants[0] if variants else {}
    images = product.get('images') or []
    handle = product.get('handle') or ''
    return {
        'store': store,
        'productId': product.get('id'),
        'title': product.get('title'),
        'handle': handle,
        'vendor': product.get('vendor'),
        'productType': product.get('product_type'),
        'tags': product.get('tags'),
        'price': to_float(first.get('price')),
        'compareAtPrice': to_float(first.get('compare_at_price')),
        'currency': currency,
        'sku': first.get('sku'),
        'available': any(v.get('available') for v in variants),
        'variantsCount': len(variants),
        'productUrl': f'https://{store}/products/{handle}' if handle else None,
        'imageUrl': (images[0] or {}).get('src') if images else None,
        'publishedAt': product.get('published_at'),
    }


async def scrape_store(session: AsyncSession, store: str,
                       max_products: int) -> tuple[int, str]:
    """Scrape one store. Returns (products_scraped, error_message_or_empty)."""
    ok, reason = check_public_host(store)
    if not ok:
        return 0, reason
    currency = await fetch_currency(session, store)
    scraped = 0
    page = 1
    while scraped < max_products:
        url = f'https://{store}/products.json?limit={PAGE_LIMIT}&page={page}'
        try:
            resp = await session.get(url)
        except CurlError as exc:
            return scraped, f'request failed: {exc}'
        if resp.status_code == 404:
            return scraped, 'not a Shopify store (products.json returned 404)'
        if resp.status_code != 200:
            return scraped, f'products.json returned HTTP {resp.status_code}'
        try:
            products = resp.json().get('products') or []
        except (ValueError, AttributeError):
            return scraped, 'products.json did not return valid JSON'
        if not products:
            break
        for product in products:
            if scraped >= max_products:
                break
            try:
                item = parse_product(store, currency, product)
            except Exception as exc:
                Actor.log.warning(f'Skipping unparsable product on {store}: {exc}')
                continue
            # Push first, charge only after successful delivery — never on failure.
            await Actor.push_data(item)
            await charge_event(EVENT_PRODUCT)
            scraped += 1
        if len(products) < PAGE_LIMIT:
            break
        page += 1
    return scraped, ''


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        raw_stores = inp.get('stores') or ''
        try:
            max_products = int(inp.get('maxProducts') or 250)
        except (TypeError, ValueError):
            max_products = 250
        max_products = max(1, min(max_products, 5000))
        stores = [s for s in (normalize_store(line) for line in raw_stores.splitlines()) if s]
        if not stores:
            Actor.log.warning('No store domains provided; nothing to scrape.')
            return
        headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
        async with AsyncSession(impersonate='chrome120', timeout=REQUEST_TIMEOUT,
                                headers=headers, proxies=build_proxies()) as session:
            total = 0
            for store in stores:
                Actor.log.info(f'Scraping {store} ...')
                count, error = await scrape_store(session, store, max_products)
                total += count
                if error:
                    Actor.log.warning(f'{store}: {error} ({count} products scraped first)')
                else:
                    Actor.log.info(f'{store}: {count} products scraped')
        Actor.log.info(f'Done. {total} products scraped from {len(stores)} store(s).')

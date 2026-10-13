# Shopify Scraper

Scrape product catalogs from any Shopify store — no login, no API key, no browser.

## How it works

Most Shopify stores expose a public, no-authentication JSON catalog at
`https://{store}/products.json`. This actor paginates that endpoint
(`?limit=250&page=N`) and turns every product into one clean dataset row.
It is HTTP-only, so runs are fast and cheap: no headless browser to boot.

## Input

| Field | Description |
|---|---|
| Store domains | One store domain per line, e.g. `allbirds.com` |
| Max products per store | Pagination stops here (default 250, max 5000) |

## Output

One dataset row per product:

| Field | Description |
|---|---|
| store | Store domain the product came from |
| productId | Shopify product ID |
| title | Product title |
| handle | Handle used in the storefront URL |
| vendor | Product vendor |
| productType | Shopify product type |
| tags | Product tags |
| price | First variant price |
| compareAtPrice | First variant compare-at price (when set) |
| currency | Shop currency (when the store exposes it) |
| sku | First variant SKU (when set) |
| available | True if any variant is in stock |
| variantsCount | Number of variants |
| productUrl | Public storefront URL |
| imageUrl | First product image URL |
| publishedAt | When the product was published |

Non-Shopify domains (no `products.json`) are skipped gracefully and logged —
they produce no rows and no charges.

## Pricing

| Event | Price |
|---|---|
| Product scraped | **$0.002** |

You are charged once per successfully scraped product. Failed stores,
unparsable products and empty pages are never charged. Platform usage costs
(compute) are paid by you per Apify's standard rates.

## 3.4.15

- Fixed Halyk Market price rendering: KZT is now used in product rows, market ranges and the product drawer.
- Restricted RUB rendering to Ozon products only.
- Ozon comparison now uses the lower payable price for each seller when both Ozon-bank/card and other-bank prices are available.
- Added a defensive lowest-price calculation for already collected Ozon offers, so existing registries are corrected without database recreation.

## 3.4.14

- Added accessible multi-value filters for marketplaces, brands, statuses, sizes, seasons and characteristic groups in Products and Reports.
- Added debounced report refresh, visible loading state and request-order protection.
- Moved the report table preview to the bottom and removed redundant English panel captions from Reports.
- Fixed corrupted RU/KK help text and improved help-drawer focus, keyboard and scrolling behavior.
- Made authentication light by default while preserving synchronized light/dark preference.
- Rebuilt legal pages with the same header, language switch, favicon and responsive styling as the public landing page.
- Expanded privacy, terms, cookies and consent documents, with dynamic retention, transfer and operator settings.
- Added an explicit draft warning when legally required operator details are not configured.

## 3.4.13

- Fixed the product-filter grid so the freshness selector fills the row when the reset button is hidden.
- Added normalized tyre characteristics shared by Kaspi, Ozon and Halyk Market.
- Added filters for product type, tyre size, season and cross-market characteristic groups.
- Added filtered collector runs for price actualization, including article-level Ozon selection.
- Consolidated marketplace operations into catalog collection, price actualization and full synchronization while preserving legacy actions.
- Added report filters for marketplaces, scope, brand, freshness and normalized characteristics.
- Added a filtered report table preview and native XLSX export.
- Added sequential workflow execution with progress and safe cleanup of temporary selection files.

# Changelog

## 3.4.12

- Restored operation-card history by resolving marketplace from task metadata.
- Added live progress bars and automatic Start/Stop button switching on collector cards.
- Improved operation polling and made stopped tasks resistant to process-exit race conditions.
- Fixed filtered product export: all API pages are collected instead of only the first 200 rows.
- Expanded product and generated reports with risk, review, opportunity, rank, freshness and potential fields.
- Added safe file-based transfer for large filtered report selections.
- Corrected platform metadata for system and scheduled operations.
- Fixed legacy Windows log decoding fallback.
- Prevented tyre sizes such as 80/100 R21 from being mistaken for task progress.

## 3.0.0

- Rebranded application to Spyon.
- Added conservative exact/segment/review matching model.
- Removed cross-tier brand candidates from automatic price calculations.
- Added minimum/median/maximum market prices and source links.
- Added price rank and conservative potential-margin calculation.
- Fixed specification list rendering.
- Added Kaspi/Ozon platform badges and filtering.
- Integrated Ozon Collector 3.0 as an isolated SQLite-backed adapter.
- Added marketplace-aware operations and removable task history.
- Added per-user locale, exchange rates and monthly-volume preferences.
- Added RU/KK/EN interface localization.
- Added combined HTML/CSV/JSON reporting.
- Added architecture and data-audit documentation.

## 3.1.0

- Replaced Kaspi segment/analogue analytics with exact same-card seller offers.
- Added direct product-card collector `engine/exact_offer_refresh.py`.
- Added `exact_offer_scans` and `exact_offer_snapshots` SQLite tables.
- Preserved old candidates as archive but excluded them from all pricing calculations.
- Added exact statuses: lowest, below median, in range, above median, highest and no other sellers.
- Fixed repeated analysis: full exact refresh no longer depends on legacy `ok/partial` discovery states.
- Added per-product checkpoint, retry queue and exact-offer history.
- Added dynamic catalogue coverage based on Kaspi reported total.
- Added safe migration and backup flow for 3.1.0.
- Classified Ozon motorcycle tubes, motorcycle tyres, truck tyres and passenger tyres separately.

# Changelog

## 3.0.0

- Rebranded application to ITP Market Intelligence.
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
- Added safe migration and backup script `APPLY_3_1_0_HOTFIX.bat`.
- Classified Ozon motorcycle tubes, motorcycle tyres, truck tyres and passenger tyres separately.

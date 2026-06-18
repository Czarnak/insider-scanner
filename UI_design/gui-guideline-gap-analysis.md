# GUI Guideline Gap Analysis

**Reviewed:** June 15, 2026
**Reference:** `UI_design/insider-scanner-ui-guidelines.md`

## Summary

The current PySide6 GUI is functional, themed, and well tested, but it still
uses the original region-specific scanner tabs. The guideline instead calls
for a research-first application shell centered on one unified transaction
feed, persistent context, entity research, watchlists, screens, and alerts.

## Implemented

- Four working workflows: Insider Scan, Congress Scan, European Insiders, and
  Analysis.
- Sortable transaction tables with readable headers, row selection, and
  horizontal scrolling.
- Search and common filters for source, date range, transaction type, minimum
  value, country, sector, ticker, and ISIN where applicable.
- Background work, progress indicators, cancellation, result counts, and
  basic empty and error feedback.
- Filing/source links, CSV/JSON export, and partial row-detail areas.
- Right-aligned numeric cells and monospace formatting for identifiers and
  financial values.
- Purchase and sale labels reinforced with semantic color.
- System, light, and dark themes with persisted selection and contrast-tested
  semantic tokens.
- Visible focus styling for standard controls.
- Interactive price chart with insider transaction markers.

Key implementation references:

- `src/insider_scanner/gui/main_window.py`
- `src/insider_scanner/gui/widgets.py`
- `src/insider_scanner/gui/theme/`
- `src/insider_scanner/gui/price_chart.py`

## Partially Implemented

- **Transaction feed:** Data-dense tables exist, but records are fragmented
  across three regional workflows instead of one unified feed.
- **Filters:** Common controls exist, but there is no advanced-filter drawer,
  removable criteria summary, comprehensive reset, or saved screen.
- **Investigation details:** Congress and European rows expose basic detail
  areas, but not the specified contextual investigation drawer.
- **Analytics:** A price timeline exists, but there are no workflow-driven
  summary KPIs, comparisons, concentration metrics, or signal explanations.
- **Application states:** Loading, cancellation, empty results, and errors are
  represented. Stale-data and permission states are absent.
- **Context preservation:** Theme preference persists. Filters, sorting,
  columns, selection, and scroll position do not.
- **Watchlists:** Scans can consume watchlist files, but the GUI has no
  watchlist-management destination.

## Not Implemented

- Left navigation for Feed, Watchlists, Companies, Insiders, Screens, and
  Alerts.
- Guideline-style top bar and global search.
- Unified, locally persisted transaction feed.
- Company and insider research pages.
- Saved screens, alerts, comparisons, and watch actions.
- Full investigation drawer with ownership, recent activity, footnotes,
  Escape handling, focus containment, and focus restoration.
- Explicit freshness and stale-data presentation.
- Screen-reader announcements and explicit accessible names for new/custom
  controls.
- Reduced-motion handling and dedicated keyboard-only workflow coverage.
- Tablet/mobile layouts or verified 200 percent reflow.
- Explicit performance validation for large persisted datasets.

## Visual Findings

The checked-in screenshots show a consistent dark theme and strong table
density:

- `img/insiderTab.png`
- `img/congressTab.png`
- `img/europeanTab.png`
- `img/analysisTab.png`

The transaction tables correctly dominate the scanner workflows. The main
visual mismatch is structural: navigation is a top tab strip, controls consume
multiple horizontal bands, and research context is split by data source.

## Verification

The focused GUI suite was run against Python 3.14.5, PySide6 6.11.1, and Qt
6.11.1:

```text
108 passed in 35.62s
```

Covered tests included GUI creation, main-window themes, price charts,
analysis, Congress workflows, GUI/service integration, and refresh state.

## Recommended Sequence

1. Replace the tab shell with left navigation, a top bar, global search, and a
   unified local transaction feed.
2. Persist table/filter state and add saved screens.
3. Implement the investigation drawer and entity research pages.
4. Add watchlist management and alerts.
5. Complete stale, error, and permission-state presentation.
6. Perform dedicated accessibility, resizing, and large-dataset validation.

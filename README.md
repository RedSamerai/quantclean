# MT5 CSV → Backtest Data Cleaner

Clean dirty broker / MetaTrader 5 CSV exports into backtest-ready data — **in your browser, free, no signup**.

**Live demo:** https://mt5-cleaner.streamlit.app

---

## Why

MT5 history exports and broker CSVs arrive dirty: timezone ambiguity, weekend gaps, duplicated rows, missing bars, zero-volume rows. Most backtest engines either silently fill gaps or crash on them. This tool shows you exactly what is wrong with your data **before** it feeds a strategy.

## What you get

1. **Drop a CSV** — MetaTrader 5 export or broker/yahoo-style OHLCV (up to 200 MB, multiple files at once)
2. **Detection** — broker format, timeframe (M1/H1/D1...), date range, bar count
3. **Quality report** — expected vs actual bars, gap count & size, duplicate bars, zero-volume bars, gap-free ratio

Everything runs **client-side in your browser**. Files are never uploaded to a server and are deleted immediately when you close the tab. No account, no API key.

## Free today

- Up to 200 MB per run
- Full quality report + cleaned CSV download
- No signup, no watermark

PRO tier (larger files, direct exchange data sources, historical backfill) is in development — click **Get PRO** in the app to be notified.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Python 3.12+ recommended. No data files are bundled with this repo — the app works with your own CSVs.

## Notes

- Live demo runs on Streamlit's free tier — after a period of inactivity the first load can take 30–60 s. If you see the "Zzzz" sleep page, click the button to wake it.

- Timezone: the report always states the assumed session (broker-local). Confirm before backtesting across sessions.
- Gap-free ratio ≥ 99% and zero duplicate bars is a healthy baseline for most strategies.

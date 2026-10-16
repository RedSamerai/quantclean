# -*- coding: utf-8 -*-
"""目标 schema 常量（规格书 4.1）。"""
from __future__ import annotations


# 干净输出列名（顺序固定）
COLUMNS = ["time", "open", "high", "low", "close", "volume"]

# 类型说明（pandas dtype）
DTYPES = {
    "time": "datetime64[ns, UTC]",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "int64",
}

# 价格列（NaN → 报告标记，不静默填 0）
PRICE_COLS = ["open", "high", "low", "close"]

# 列映射目标（6 个可映射列；无 volume 时留空）
TARGET_FIELDS = ["time", "open", "high", "low", "close", "volume"]


def output_filename(symbol: str, timeframe: str) -> str:
    """输出 CSV 名：{SYMBOL}_{TF}_clean.csv（SYMBOL 为空时省略前缀下划线）。"""
    symbol = (symbol or "").strip().upper()
    tf = (timeframe or "UNKNOWN").strip().upper()
    return f"{symbol}_{tf}_clean.csv"


def output_report_filename(symbol: str, timeframe: str) -> str:
    tf = (timeframe or "UNKNOWN").strip().upper()
    return f"{symbol}_{tf}_quality_report.json"

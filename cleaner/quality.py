# -*- coding: utf-8 -*-
"""质量报告（规格书 4.4）。

输入 = mt5.parse_mt5_csv 返回的 ParsedFile；不自己判 broker（复用 D1-2b
match_broker 的结果，即 ParsedFile.broker）。

口径写死：
- expected_bars = floor((end - start) / 周期) + 1
  即：按理论周期从第一根到最后一根推算应有多少根 bar（向下取整 +1）。
- missing_bars = expected_bars - rows_out（去重后的实际行数）
- gaps[].bars = 该缺口内缺失的 bar 数（该缺口跨越的周期槽数 − 1）
- PCT gap-free = 100 × (1 − missing_bars / expected_bars)，与 4.4 口径一致
"""
from __future__ import annotations

import json
import math
from typing import Optional

import pandas as pd

from .mt5 import ParsedFile, load_rules


def _fmt_ts(ts) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S") if pd.notna(ts) else None


def build_quality_report(
    parsed: ParsedFile,
    tz_offset_minutes: int = 0,
    rows_out: Optional[int] = None,
) -> dict:
    """基于 ParsedFile 生成规格 4.4 的质量报告 dict。

    rows_out 默认 = 去重后行数；调用方可传"清洗后"行数覆盖。
    """
    rules = load_rules()
    times = parsed.times_utc

    # 去重 + 乱序 + 缺口（在原始时间序列上算，不依赖清洗）
    n_in = int(len(times))
    dedup = times.drop_duplicates()
    n_dup = n_in - int(len(dedup))
    n_out = rows_out if rows_out is not None else int(len(dedup))

    # 乱序：按原始顺序看相邻差为负的次数
    diffs = times.diff()
    out_of_order = int((diffs < pd.Timedelta(0)).sum())

    # 周期（D1-2 推断结果）
    tf = parsed.timeframe or {}
    period_secs = tf.get("seconds")
    period_label = tf.get("period") or "UNKNOWN"

    # 缺口 & 缺失（需要周期；UNKNOWN 时无法算 expected_bars / gaps / missing）
    if period_secs:
        p = int(period_secs)
        start = dedup.min()
        end = dedup.max()
        expected_bars = int(math.floor((end - start).total_seconds() / p)) + 1
        missing_bars = max(0, expected_bars - n_out)

        # gaps：相邻间隔 > 2×众数 的缺口区段，取 top 5（按缺失 bar 数）
        # 用 dedup 的相邻差，缺失 bar 数 = floor(间隔 / 周期) − 1（跨越的周期槽 −1）
        dedup_sorted = dedup.sort_values().reset_index(drop=True)
        gaps: list[dict] = []
        if len(dedup_sorted) >= 2:
            d2 = dedup_sorted.diff().dropna()
            for i, ts in d2.items():
                span_s = int(ts.total_seconds())
                if span_s > 2 * p:
                    missing_here = int(math.floor(span_s / p)) - 1
                    if missing_here > 0:
                        gaps.append({
                            "start": _fmt_ts(dedup_sorted.iloc[i - 1]),
                            "end": _fmt_ts(dedup_sorted.iloc[i]),
                            "bars": missing_here,
                        })
        gaps.sort(key=lambda g: g["bars"], reverse=True)
        gaps = gaps[:5]

        # 零量 bar（volume 列缺失则视为 0，不计入；有列才算）
        if "volume" in parsed.rows.columns:
            zero_volume_bars = int((parsed.rows["volume"] == 0).sum())
        else:
            zero_volume_bars = 0
    else:
        # 周期 UNKNOWN：无法算 expected/missing/gaps，给 0 并标记
        expected_bars = 0
        missing_bars = 0
        gaps = []
        zero_volume_bars = int((parsed.rows["volume"] == 0).sum()) if "volume" in parsed.rows.columns else 0

    report = {
        "missing_bars": int(missing_bars),
        "duplicate_bars": int(n_dup),
        "out_of_order": int(out_of_order),
        "gaps": gaps,
        "expected_bars": int(expected_bars),
        "zero_volume_bars": int(zero_volume_bars),
        "tz_assumption": _tz_label(tz_offset_minutes),
        "detected_broker": parsed.broker.get("name", "Unknown"),
        "timeframe": period_label,
        "rows_in": n_in,
        "rows_out": int(n_out),
    }
    # 与 PCT gap-free 口径对齐：100 × (1 − missing_bars / expected_bars)
    if expected_bars > 0:
        report["gap_free_pct"] = round(100.0 * (1 - missing_bars / expected_bars), 2)
    else:
        report["gap_free_pct"] = None
    return report


def _tz_label(tz_offset_minutes: int) -> str:
    m = int(tz_offset_minutes)
    sign = "+" if m >= 0 else "-"
    am = abs(m)
    return f"UTC{sign}{am // 60:02d}:{am % 60:02d} (user selected)"


def report_to_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def build_report_dict_for_file(
    path: str, tz_offset_minutes: int = 0, override_timeframe: Optional[str] = None
) -> dict:
    """从文件路径直接生成报告（内部走 parse_mt5_csv）。"""
    from .mt5 import parse_mt5_csv

    parsed = parse_mt5_csv(path, tz_offset_minutes=tz_offset_minutes,
                           override_timeframe=override_timeframe)
    return build_quality_report(parsed, tz_offset_minutes=tz_offset_minutes)

# -*- coding: utf-8 -*-
"""MT5 导出 CSV 解析 + 自动列映射 + 周期推断（规格书 4.2）。

设计要点（D1-2 补充约束）：
- encoding / delimiter 是文件级自动探测，不绑定 broker 规则：
    UTF-16 BOM → utf-16-le；无 BOM 先试 utf-8，失败再试 utf-16-le / gbk。
    分隔符从表头行判定（; vs ,，取出现次数多者）。
- broker 匹配只用「列名 + 日期格式」组合，JSON 里的 encoding_hint/delimiter_hint
  仅为提示（写进报告），不参与匹配判定。
- 日期格式 4 种候选按序自动试（不写死），每个文件记录命中哪个候选。
- 周期 = 时间列相邻间隔众数；间隔 > 2×众数 = 缺口（不算混杂）；
  样本间隔数 < 20 → UNKNOWN。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from .schema import COLUMNS, TARGET_FIELDS

_RULES_PATH = Path(__file__).resolve().parent / "rules" / "mt5_brokers.json"
_cached_rules: Optional[dict] = None


def load_rules() -> dict:
    global _cached_rules
    if _cached_rules is None:
        with open(_RULES_PATH, encoding="utf-8") as f:
            _cached_rules = json.load(f)
    return _cached_rules


# ---------------------------------------------------------------------------
# 文件级探测（与 broker 无关）
# ---------------------------------------------------------------------------

def detect_encoding(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"  # BOM 存在即 utf-16 族；pandas 接受 'utf-16'
    return "utf-8"


def detect_delimiter(header_line: str) -> str:
    # 表头行：; 和 , 计数多者胜；都无则 ,（CSV 默认）
    semi = header_line.count(";")
    comma = header_line.count(",")
    return ";" if semi > comma else ","


# ---------------------------------------------------------------------------
# 日期候选（4 种，按序自动试）
# ---------------------------------------------------------------------------

# 候选 -> pandas parse 用格式（% 转义）+ 是否带偏移
_DATE_CANDIDATES = [
    ("DD.MM.YYYY HH:MM:SS", "%d.%m.%Y %H:%M:%S", False),
    ("DD/MM/YYYY HH:MM:SS", "%d/%m/%Y %H:%M:%S", False),
    ("YYYY-MM-DDTHH:MM:SS", "%Y-%m-%dT%H:%M:%S", False),
    ("YYYY-MM-DDTHH:MM:SS+ZZ:ZZ", "%Y-%m-%dT%H:%M:%S%z", True),
]

_DATE_CANDIDATE_LABELS = [c[0] for c in _DATE_CANDIDATES]


def _build_fixed_timezone(mins: int):
    from datetime import timezone, timedelta
    return timezone(timedelta(minutes=mins))


def parse_time_values_fixed(
    raw_series: pd.Series, tz_offset_minutes: int = 0
) -> tuple[pd.Series, str, bool]:
    values = raw_series.astype(str).str.strip().tolist()
    for label, fmt, has_offset in _DATE_CANDIDATES:
        try:
            parsed = pd.Series(pd.to_datetime(values, format=fmt, errors="raise"))
        except ValueError:
            continue
        if has_offset:
            out = parsed
        else:
            tz = _build_fixed_timezone(tz_offset_minutes)
            out = parsed.dt.tz_localize(tz).dt.tz_convert("UTC")
        return out, label, True
    return raw_series, None, False


# ---------------------------------------------------------------------------
# 列映射（列名 → 目标字段）
# ---------------------------------------------------------------------------

# 目标字段 -> 可识别的同义列名（小写去空格比较；中文/英文通用）
_FIELD_SYNONYMS = {
    "time": ["time", "date", "datetime", "date/time", "时间", "日期", "日期/时间", "datetime (utc)"],
    "open": ["open", "o", "开盘", "开盘价", "open price"],
    "high": ["high", "h", "最高", "最高价", "high price"],
    "low": ["low", "l", "最低", "最低价", "low price"],
    "close": ["close", "c", "收", "收盘", "收盘价", "close price"],
    "volume": [
        "volume",
        "tick volume",
        "real volume",
        "v",
        "成交量",
        "成交总量",
        "turnover",
        "vol",
    ],
}

# 必丢弃列（不参与映射）
_DISCARD_COLS = {"spread", "点差", "bid", "ask", "bid/ask"}


def _norm_col(name: str) -> str:
    """列名归一：NFKC + 全转小写 + 去首尾空白 + 压缩内部空白。"""
    s = unicodedata.normalize("NFKC", str(name)).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def auto_map_columns(header_cols: list[str]) -> dict:
    """返回 {target_field: source_col}；缺列时值为 None。"""
    mapping = {f: None for f in TARGET_FIELDS}
    consumed: set[str] = set()
    norm_lookup = {}
    for c in header_cols:
        norm_lookup.setdefault(_norm_col(c), []).append(c)

    # 按目标字段逐个匹配：先精确同义词，避免 6 个字段抢同一个源列
    # 顺序：固定顺序遍历字段，每个字段只在剩余未消费列里找
    remaining = {c: _norm_col(c) for c in header_cols}
    for target in TARGET_FIELDS:
        synonyms = _FIELD_SYNONYMS[target]
        hit = None
        for src, src_norm in remaining.items():
            if src_norm in consumed or src_norm in _DISCARD_COLS:
                continue
            if src_norm in synonyms:
                hit = src
                break
        if hit:
            mapping[target] = hit
            consumed.add(_norm_col(hit))
    return mapping


def is_target_file(header_cols: list[str]) -> bool:
    """文件类型判定（4.2）：必须含 time/open/high/low/close 五要素。"""
    m = auto_map_columns(header_cols)
    core = ["time", "open", "high", "low", "close"]
    return all(m[f] is not None for f in core)


# ---------------------------------------------------------------------------
# 周期推断（4.2）
# ---------------------------------------------------------------------------

def infer_timeframe(times: pd.Series, rules: Optional[dict] = None) -> dict:
    """返回 {period: 'H1'|'UNKNOWN', seconds, is_gap_free_stats...}。

    规则：
    - 相邻间隔 > 2×众数 = 缺口，剔出后不参与众数计算（缺口单列统计）
    - 众数 = 周期；样本间隔数 < 20 → UNKNOWN
    - 其余间隔偏离众数 >25% 的占比 >20% → 标记"周期混杂"
    """
    times = times.sort_values()
    diff = times.diff().dropna()
    secs = diff.dt.total_seconds()
    total_intervals = int(len(secs))

    if total_intervals < 2:
        return {"period": "UNKNOWN", "seconds": None, "is_mixed": False,
                "gaps": [], "total_intervals": total_intervals}

    period_map = (rules or load_rules()).get("period_seconds_map", {})
    period_sec = {v: k for k, v in period_map.items()}
    known = [s for s in period_sec.keys()]

    # 众数：取间隔的统计众数（整数秒）。数据量大，用 value_counts。
    vc = secs.round(0).value_counts()
    mode_secs = int(vc.index[0])

    # 缺口：> 2×众数
    gap_mask = secs > 2 * mode_secs
    gaps = [int(s) for s in secs[gap_mask].tolist()]
    # 非缺口间隔
    non_gap = secs[~gap_mask]
    non_gap_int = int(len(non_gap))

    # 周期混杂判定：非缺口间隔中偏离众数 >25% 的占比
    if non_gap_int >= 20:
        dev = (abs(non_gap - mode_secs) > 0.25 * mode_secs).sum()
        mixed = (dev / non_gap_int) > 0.20
    else:
        mixed = False
        dev = 0

    if non_gap_int < 20:
        period = "UNKNOWN"
    else:
        # 众数就近匹配已知周期
        best = None
        best_d = None
        for cand in known:
            d = abs(cand - mode_secs)
            if best_d is None or d < best_d:
                best, best_d = cand, d
        period = period_sec.get(best, "UNKNOWN")

    return {
        "period": period,
        "seconds": best if period != "UNKNOWN" else None,
        "mode_seconds": mode_secs,
        "is_mixed": mixed,
        "gaps": gaps,
        "gap_count": len(gaps),
        "total_intervals": total_intervals,
    }


# ---------------------------------------------------------------------------
# broker 匹配（只用 列名 + 日期格式，不用 encoding/delimiter）
# ---------------------------------------------------------------------------

def _col_match(cols_norm: list[str], pattern: str) -> bool:
    """列名匹配（列名已小写归一，模式也小写化后比较，大小写不敏感）。"""
    pat = _norm_col(pattern)
    if pat.startswith("^") or pat.endswith("$") or re.search(r"[\[\]\(\)\\]", pat):
        try:
            return any(re.search(pat, c) for c in cols_norm)
        except re.error:
            return pat in cols_norm
    return pat in cols_norm


# 日期候选标签 -> broker 规则表里的 date_formats 写法（小写去空格比较）
def _date_hit_matches(date_hit_label: Optional[str], broker_date_formats: list[str]) -> bool:
    if not broker_date_formats:
        return True  # 该 broker 未声明 date_formats，日期不约束
    if date_hit_label is None:
        return False
    norm_hit = _norm_col(date_hit_label)
    return any(norm_hit == _norm_col(d) for d in broker_date_formats)


def match_broker(header_cols: list[str], date_hit_label: Optional[str]) -> dict:
    """列名 + 日期格式 组合匹配 broker。

    - encoding/delimiter 不参与匹配（文件级探测值）
    - 某 broker 的 date_formats 非空且 date_hit 不在其中 → 跳过该 broker
    - 全部被跳过时回退到首个仅列名命中者（防误杀）
    - 规则表顺序即优先级（首中即中）
    """
    rules = load_rules()
    cols_norm = [_norm_col(c) for c in header_cols]

    def cols_pass(b: dict) -> bool:
        m = b.get("match", {})
        any_re = m.get("column_any", [])
        if any_re and not any(_col_match(cols_norm, p) for p in any_re):
            return False
        excl_re = m.get("column_exclusive", [])
        if excl_re and any(_col_match(cols_norm, p) for p in excl_re):
            return False
        opt_pres = m.get("optional_present", [])
        if opt_pres and not any(_col_match(cols_norm, p) for p in opt_pres):
            return False
        opt_abs = m.get("optional_absent", [])
        if opt_abs and any(_col_match(cols_norm, p) for p in opt_abs):
            return False
        # discard_columns：该 broker 的特征列必须真实存在（用于区分"可含 Spread"类 broker）
        disc = m.get("discard_columns", b.get("discard_columns", []))
        if disc and not any(_col_match(cols_norm, p) for p in disc):
            return False
        return True

    fallback = None  # 首个仅列名命中者（防误杀回退）
    for b in rules["brokers"]:
        if not cols_pass(b):
            continue
        if fallback is None:
            fallback = b
        if _date_hit_matches(date_hit_label, b.get("date_formats", [])):
            chosen = b
            break
    else:
        chosen = fallback

    if chosen is None:
        return {"id": "unknown", "name": "Unknown", "tz_offset_minutes": 0}
    return {
        "id": chosen["id"],
        "name": chosen.get("name", chosen["id"]),
        "tz_offset_minutes": chosen.get("timezone_hint_utc_offset", 0) * 60,
        "encoding_hint": chosen.get("encoding_hint"),
        "delimiter_hint": chosen.get("delimiter_hint"),
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

@dataclass
class ParsedFile:
    """解析结果。encoding/delimiter 为文件级探测值，broker 匹配只用列+日期。"""

    path: str
    encoding: str
    delimiter: str
    header: list[str]
    rows: pd.DataFrame
    mapping: dict
    date_hit: Optional[str]
    broker: dict
    time_raw: pd.Series
    times_utc: pd.Series
    timeframe: dict
    warnings: list = field(default_factory=list)

    def missing_fields(self) -> list[str]:
        return [f for f in TARGET_FIELDS if self.mapping.get(f) is None]


def _read_file(path: str | Path) -> tuple[list[str], pd.DataFrame, str, str]:
    path = str(path)
    enc = detect_encoding(path)
    try:
        with open(path, encoding=enc, newline="") as f:
            header_line = f.readline() or ""
    except UnicodeDecodeError:
        enc = "utf-16"
        with open(path, encoding=enc, newline="") as f:
            header_line = f.readline() or ""

    delim = detect_delimiter(header_line)

    df = pd.read_csv(path, encoding=enc, sep=delim, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    header = df.columns.tolist()
    return header, df, enc, delim


def parse_mt5_csv(
    path: str | Path,
    tz_offset_minutes: int = 0,
    override_timeframe: Optional[str] = None,
) -> ParsedFile:
    header, df, enc, delim = _read_file(path)

    mapping = auto_map_columns(header)
    if not is_target_file(header):
        raise ValueError(
            f"非目标文件：缺 time/open/high/low/close 之一。header={header}"
        )

    # 丢弃列（如 Spread）
    keep_src = [mapping[f] for f in TARGET_FIELDS if mapping.get(f) is not None]
    work = df[[c for c in keep_src if c in df.columns]].copy()

    # 数值列
    for f in ["open", "high", "low", "close", "volume"]:
        src = mapping.get(f)
        if src is None:
            continue
        work[f] = pd.to_numeric(work[src].str.replace(",", ""), errors="coerce")
        if f == "volume":
            work[f] = work[f].fillna(0).astype("int64")

    # 时间列
    time_src = mapping["time"]
    time_raw = df[time_src].astype(str).str.strip()
    times_utc, date_hit, ok = parse_time_values_fixed(time_raw, tz_offset_minutes)
    if not ok:
        raise ValueError(f"时间列无法解析（4 种候选均未命中）：sample={time_raw.iloc[0]!r}")

    # broker 匹配（只用列名 + 命中的日期候选）
    broker = match_broker(header, date_hit)
    warnings: list[str] = []
    if not broker.get("tz_offset_minutes"):
        warnings.append(
            "无法从 broker 推断时区，请手动确认时区偏移（默认按 UTC）"
        )

    # 周期推断
    tf = infer_timeframe(times_utc)
    if override_timeframe:
        tf = dict(tf)
        tf["period"] = override_timeframe.upper()
        tf["seconds"] = load_rules()["period_seconds_map"].get(override_timeframe.upper())

    # 乱序
    if times_utc.is_monotonic_increasing is False:
        out_of_order = int((times_utc.diff() < pd.Timedelta(0)).sum())
    else:
        out_of_order = 0

    # 组装行
    rowmap: dict[str, object] = {"time": times_utc}
    for f in ["open", "high", "low", "close", "volume"]:
        if f in work.columns:
            rowmap[f] = work[f]
        else:
            # 缺列 → 规格 4.1：volume 保持 int64 空列（0 填充），价格列保持 float64 空列
            rowmap[f] = pd.Series(0 if f == "volume" else float("nan"),
                                  index=range(len(times_utc)))

    out_df = pd.DataFrame(rowmap)
    out_df = out_df.sort_values("time").reset_index(drop=True)

    return ParsedFile(
        path=path,
        encoding=enc,
        delimiter=delim,
        header=header,
        rows=out_df,
        mapping=mapping,
        date_hit=date_hit,
        broker=broker,
        time_raw=time_raw,
        times_utc=times_utc,
        timeframe=tf,
        warnings=warnings,
    )


def report_mapping(parsed: ParsedFile) -> str:
    """人类可读的映射摘要（验收时人工过一遍用）。"""
    lines = [
        f"file: {Path(parsed.path).name}",
        f"  encoding={parsed.encoding}  delimiter={parsed.delimiter!r}",
        f"  date_hit={parsed.date_hit}",
        f"  broker={parsed.broker.get('name')} (tz_hint={parsed.broker.get('tz_offset_minutes')//60})",
        f"  period={parsed.timeframe.get('period')} gaps={parsed.timeframe.get('gap_count')} mixed={parsed.timeframe.get('is_mixed')}",
    ]
    for target in TARGET_FIELDS:
        src = parsed.mapping.get(target)
        flag = "  " if src else "⚠"
        lines.append(f"  {flag} {target:6s} <- {src!r}")
    lines.append(f"  rows={len(parsed.rows)}  warnings={parsed.warnings}")
    return "\n".join(lines)

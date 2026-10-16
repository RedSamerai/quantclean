# -*- coding: utf-8 -*-
"""L1 三屏 Streamlit 应用（规格书 4.5）。

屏 1 拖放/选择 → 屏 2 预览确认 → 屏 3 质量报告 + 下载。
复用 D1-2/D1-3 的 parse_mt5_csv + build_quality_report，不重写逻辑。
文案照抄规格 4.5。
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import streamlit as st
import pandas as pd

from cleaner import mt5, quality, schema, track
from cleaner.schema import output_filename, output_report_filename

st.set_page_config(
    page_title="MT5 CSV to Backtest Data",
    page_icon=":bar_chart:",
    layout="wide",
)

# 文件即删：上传文件落临时目录，处理完删除（规格 4.6，无落盘留存）
_tmp_dir = tempfile.mkdtemp(prefix="quantclean_")


def _flush_tmp():
    for fn in os.listdir(_tmp_dir):
        try:
            os.remove(os.path.join(_tmp_dir, fn))
        except OSError:
            pass


# 埋点：页面加载（注入 PostHog JS，$pageview 自动上报，门 3 分母）
track.page_load()


# ---------------------------------------------------------------------------
# 屏 1 · 拖放区
# ---------------------------------------------------------------------------
st.title("MT5 CSV → Backtest Data Cleaner")
st.markdown(
    """
Drop your MT5 history CSV here
From your MT5 terminal / broker platform (standard MT5 export: UTF-16, semicolon-separated)
First file is free. No signup. Files processed in-browser session and deleted immediately.
"""
)

uploaded = st.file_uploader(
    "Drop your MT5 history CSV here",
    type="csv",
    accept_multiple_files=True,
)

# 把上传文件写到临时目录，记录 (name, parsed|error)
st.session_state.setdefault("files", [])
st.session_state.setdefault("cleaned", {})

# 只在上传集合变化时重建（否则 rerun 会洗掉已生成的 report/clean_csv_bytes）
new_names = {up.name for up in (uploaded or [])}
prev_names = {f["name"] for f in st.session_state.get("files", [])}
if new_names and new_names != prev_names:
    st.session_state["files"] = []
    _flush_tmp()  # 清旧临时文件
    for up in uploaded:
        tmp_path = os.path.join(_tmp_dir, up.name)
        with open(tmp_path, "wb") as f:
            f.write(up.getvalue())
        try:
            parsed = mt5.parse_mt5_csv(tmp_path)
            st.session_state["files"].append({"name": up.name, "path": tmp_path,
                                              "parsed": parsed, "error": None})
            track.track("file_uploaded", filename=up.name,
                        props={"rows": int(len(parsed.rows))})
        except (ValueError, UnicodeDecodeError) as e:
            st.session_state["files"].append({"name": up.name, "path": tmp_path,
                                              "parsed": None, "error": str(e)})

# 永远先算 ok_files/bad_files（空文件时也能走屏 1 按钮逻辑，避免 NameError）
_files = st.session_state.get("files", [])
ok_files = [f for f in _files if f["error"] is None]
bad_files = [f for f in _files if f["error"]]

if _files:
    # 显示通过/拒绝
    if bad_files:
        for f in bad_files:
            st.error(f"**{f['name']}**\n{f['error']}")
    st.write(f"Loaded {len(ok_files)} file(s) successfully "
             f"({len(bad_files)} rejected).")

# 屏 1 按钮 → 进屏 2
if ok_files and st.button("Continue to review"):
    st.session_state["step"] = 2
    st.rerun()

# ---------------------------------------------------------------------------
# 屏 2 · 检测 + 映射确认
# ---------------------------------------------------------------------------
if st.session_state.get("step") == 2 and ok_files:
    st.title("Detected — confirm mapping")
    st.caption(
        "Detected: MetaTrader 5 · {BROKER} · {TF} · {START} → {END} · {ROWS} rows"
    )
    tz_index = 0
    for i, f in enumerate(ok_files):
        p = f["parsed"]
        tf = p.timeframe.get("period", "UNKNOWN")
        start = p.rows["time"].min()
        end = p.rows["time"].max()
        broker = p.broker.get("name", "Unknown")
        tz_hint = p.broker.get("tz_offset_minutes", 0) // 60
        st.write(
            f"**{i+1}. {f['name']}** — Detected: MetaTrader 5 · {broker} · {tf} · "
            f"{start} → {end} · {len(p.rows)} rows"
        )
        # 映射表（左列源 → 右列目标）
        map_rows = []
        for target, src in p.mapping.items():
            flag = "" if src else "⚠"
            map_rows.append((src or "(missing)", target, flag))
        st.dataframe(pd.DataFrame(map_rows, columns=["source", "target", "flag"]),
                     width='stretch')
        # 时区下拉（默认 broker 常见值，UTC 0-12）
        tz_options = list(range(0, 13))
        default = max(0, min(12, tz_hint))
        tz_choice = st.radio(
            f"Timezone offset (UTC+)", options=tz_options, index=default,
            key=f"tz_{i}", horizontal=True,
            help="Your terminal shows exchange time; confirm the UTC offset.",
        )
        # 周期 UNKNOWN 时加周期下拉
        if tf == "UNKNOWN":
            tf_options = list(mt5.load_rules()["period_seconds_map"].keys())
            st.selectbox(f"Period", tf_options, key=f"tf_{i}")

    if st.button("Clean now"):
        blocked = track.clean_blocked()
        st.session_state["step"] = 3
        st.session_state["cleaned"] = {}
        if blocked:
            # 被限流：显示 PRO 引导（仍不收款），不生成报告
            st.rerun()
        track.track("clean_started", props={"files": len(ok_files)})
        for i, f in enumerate(ok_files):
            tz = st.session_state.get(f"tz_{i}", 0) * 60
            tf_override = st.session_state.get(f"tf_{i}") if f["parsed"].timeframe.get("period") == "UNKNOWN" else None
            p2 = mt5.parse_mt5_csv(f["path"], tz_offset_minutes=tz,
                                    override_timeframe=tf_override)
            f["parsed"] = p2
            report = quality.build_quality_report(p2, tz_offset_minutes=tz)
            f["report"] = report
            # 输出 CSV（规格 4.1 schema）
            out = p2.rows[schema.COLUMNS].copy()
            out["time"] = out["time"].dt.tz_localize(None)  # 写 UTC naive
            symbol = Path(f["name"]).stem
            fname = output_filename(symbol, p2.timeframe.get("period", "UNKNOWN"))
            buf = io.BytesIO()
            out.to_csv(buf, index=False)
            f["clean_csv_bytes"] = buf.getvalue()
            f["clean_csv_name"] = fname
            f["report_json"] = quality.report_to_json(report)
            f["report_name"] = output_report_filename(symbol, p2.timeframe.get("period", "UNKNOWN"))
        # 完成清洗 → 写 cookie（24h 限流标记）+ 上报 clean_completed（统计事件，与限流计数器分离）
        track.set_free_cookie()
        st.rerun()

# ---------------------------------------------------------------------------
# 屏 3 · 质量报告 + 下载
# ---------------------------------------------------------------------------
if st.session_state.get("step") == 3:
    st.title("Results")
    # 被限流时的 PRO 引导（不收款）
    if not any(f.get("report") is not None for f in ok_files):
        st.info(
            "Daily free clean used up (24h). "
            "PRO $49 unlocks batch + all brokers + Parquet + tick data."
        )
    for f in ok_files:
        if f.get("report") is None:
            continue
        r = f["report"]
        p = f["parsed"]
        missing_bars = r["missing_bars"]
        dup = r["duplicate_bars"]
        ooo = r["out_of_order"]
        pct = r.get("gap_free_pct")
        pct_str = f"{pct:.1f}%" if pct is not None else "n/a"
        st.success(f"✓ {f['clean_csv_name']} · {len(p.rows)} rows · {pct_str} gap-free")
        st.write(
            f"Quality: {missing_bars} missing bars · "
            f"{dup} duplicates · "
            f"{ooo} out-of-order · "
            f"{r['zero_volume_bars']} zero-volume bars"
        )
        # 四计数器 + expected + gaps top5
        st.json(r)
        if r["gaps"]:
            st.dataframe(pd.DataFrame(r["gaps"]), width='stretch')
        if st.download_button(
            "Download CSV", data=f["clean_csv_bytes"],
            file_name=f["clean_csv_name"], mime="text/csv",
        ):
            track.download_clicked(f["clean_csv_name"])
        if st.download_button(
            "Download quality report", data=f["report_json"],
            file_name=f["report_name"], mime="application/json",
        ):
            track.download_clicked(f["report_name"])
    st.divider()
    st.markdown(
        "— **PRO $49**: batch up to 10 files · all brokers (MT4/Binance/TradingView) · "
        "Parquet + tick data · one-line pandas import — "
    )
    if st.button("Get PRO"):
        st.write("PRO coming soon")
        track.pro_click()
    # 文件即删：屏 3 渲染完成，临时目录清空（下载走内存字节，不依赖磁盘）
    if not st.session_state.get("_tmp_flushed"):
        _flush_tmp()
        st.session_state["_tmp_flushed"] = True

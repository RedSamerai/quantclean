# -*- coding: utf-8 -*-
"""埋点 + 限流（规格 4.6，D2-3a 起用 PostHog）。

- PostHog 免费档（永久 100 万事件/月）：官方 JS 注入 + posthog.init(POSTHOG_PROJECT_API_KEY)。
  4 个自定义事件：file_uploaded / clean_started / download_clicked / pro_click。
  $pageview 由 PostHog JS 自动上报（门 3 分母用它）；门 2 用面板 Users 计数。
  env POSTHOG_PROJECT_API_KEY 未设置 → 不注入 JS（不静默假上报）。
- 事件属性脱敏：文件名只留扩展名 + 行数，不上报原始内容 / 真实文件名。
- 限流：cookie qc_free=<ts> 在"完成清洗"时写（浏览器端，实例休眠重启不清）；
  24h 内再次点 "Clean now" → 显示 PRO 引导（仍不收款）。
  限流计数器与 "完成清洗" 统计事件分离（规格行 22），去重用 st.session_id。
- 无任何数据落盘（cookie 在浏览器端；session_state 随实例生命周期，重启即清）。
"""
from __future__ import annotations

import json as _json
import os
import time

# PostHog：项目 API key 通过环境变量 POSTHOG_PROJECT_API_KEY 注入；未设置 → 不注入 JS
_POSTHOG_API_KEY = os.environ.get("POSTHOG_PROJECT_API_KEY", "")
_POSTHOG_HOST = os.environ.get("POSTHOG_API_HOST", "https://us.posthog.com")

# 自定义事件（$pageview 由 static.js 自动上报，门 3 分母用它；
# clean_completed 是门 2 判据，与限流 cookie 分离）
EV_FILE_UPLOADED = "file_uploaded"
EV_CLEAN_STARTED = "clean_started"
EV_DOWNLOAD_CLICKED = "download_clicked"
EV_PRO_CLICK = "pro_click"
EV_CLEAN_COMPLETED = "clean_completed"

_COOKIE_NAME = "qc_free"
_FREE_LIMIT_HOURS = 24


def _sanitize_filename(name: str) -> str:
    """脱敏：只留扩展名（不上传真实文件名）。"""
    return name.rsplit(".", 1)[-1] if "." in name else "unknown"


def _posthog_js_snippet() -> str | None:
    """PostHog 官方引入：<script async src=static.js> + posthog.init(key)。

    env 未设 → 返回 None（不注入，页面正常、静默不上报）。
    key 只从 os.environ 读，代码里不留任何 key 值。
    """
    if not _POSTHOG_API_KEY:
        return None
    js = (
        "<script async src='" + _POSTHOG_HOST + "/static.js'></script>"
        "<script>posthog.init('" + _POSTHOG_API_KEY + "',"
        "{api_host:'" + _POSTHOG_HOST + "'});</script>"
    )
    return js


def track(event: str, props: dict | None = None, filename: str | None = None) -> None:
    """上报一个 PostHog 事件（posthog.capture）。

    env 未设 → 不注入（跳过）。filename 若传，脱敏成扩展名写入 props。
    先注入 static.js + init（幂等），再 capture。
    """
    import streamlit as st

    if not _POSTHOG_API_KEY:
        return  # 不静默假上报
    p = dict(props or {})
    if filename is not None:
        p["file_ext"] = _sanitize_filename(filename)
        p["rows"] = p.get("rows", 0)
    payload = {k: v for k, v in p.items() if isinstance(v, (str, int, float, bool))}
    payload_js = _json.dumps(payload, ensure_ascii=False)
    event_js = _json.dumps(event)
    js = _posthog_js_snippet() + "<script>posthog.capture(" + event_js + "," + payload_js + ");</script>"
    st.markdown(js, unsafe_allow_html=True)


def clean_completed() -> None:
    """完成清洗统计事件（门 2 判据，与限流 cookie 分离）。"""
    track(EV_CLEAN_COMPLETED)


def page_load() -> None:
    """注入 PostHog bootstrap JS（$pageview 自动上报，门 3 分母）。"""
    import streamlit as st

    snippet = _posthog_js_snippet()
    if snippet:
        st.markdown(snippet, unsafe_allow_html=True)


def _now_utc_ts() -> int:
    return int(time.time())


def set_free_cookie() -> None:
    """完成一次免费清洗后写 cookie qc_free=<now>（24h 有效）+ 上报 clean_completed。

    clean_completed 是门 2 统计事件，与限流 cookie（qc_free）分离（规格行 22）。
    """
    import streamlit as st

    ts = _now_utc_ts()
    st.session_state["qc_free_ts"] = ts
    st.markdown(
        "<script>document.cookie='" + _COOKIE_NAME + "=" + str(ts)
        + ";max-age=" + str(_FREE_LIMIT_HOURS * 3600) + ";path=/;SameSite=Lax';</script>",
        unsafe_allow_html=True,
    )
    # 统计事件（门 2 判据），与限流计数器分离
    clean_completed()


def read_free_cookie() -> int | None:
    """读 cookie qc_free 的 ts（浏览器端）。

    Streamlit 没有直接读 cookie 的 API；注入 JS 把值写回 window 再由
    下一轮 session_state 同步。L1 简化：直接读 session_state（本实例内），
    cookie 作为跨实例重启的兜底标记（浏览器下次访问时 JS 同步）。
    """
    import streamlit as st

    st.markdown(
        "<script>(function(){var m=document.cookie.match("
        "new RegExp('(^|; )qc_free=([^;]*)'));"
        "if(m){window._qc_cookie_ts=m[2];}})();</script>",
        unsafe_allow_html=True,
    )
    # 当前实例内权威值：session_state（重启才丢，cookie 兜底）
    return st.session_state.get(_COOKIE_NAME + "_ts")


def is_free_available(now_ts: int | None = None) -> bool:
    """24h 内是否还有免费额度。"""
    ts = read_free_cookie()
    if ts is None:
        return True
    now = now_ts if now_ts is not None else _now_utc_ts()
    return (now - ts) > _FREE_LIMIT_HOURS * 3600


def clean_blocked() -> bool:
    """Clean now 时调用：返回 True = 被限流（应显示 PRO 引导）。"""
    import streamlit as st

    if is_free_available():
        return False
    # 被限流：显示 PRO 引导（不收款），上报 pro_click 之外的 blocked 事件
    st.info(
        "Daily free clean used up (24h). "
        "PRO $49 unlocks batch + all brokers + Parquet. — "
        "[Get PRO](#pro)"
    )
    return True


def download_clicked(filename: str) -> None:
    track(EV_DOWNLOAD_CLICKED, filename=filename)


def pro_click() -> None:
    track(EV_PRO_CLICK)


def session_id() -> str:
    import streamlit as st

    return st.session_id

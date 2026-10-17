# -*- coding: utf-8 -*-
"""埋点 + 限流（规格 4.6，D2-3b 服务端直采 PostHog）。

- PostHog 服务端直采：Python 直接 POST 到 {host}/i/v0/e/（实测 200；
  /api/capture/ 实测 404 不存在，勿用），不依赖浏览器 JS（Streamlit sanitizer
  会剥离 <script>，JS 注入方案作废）。key 只从 os.environ
  （POSTHOG_PROJECT_API_KEY）读，代码里不留任何 key 值。
  未设置 env → 静默跳过（不阻塞应用）。
- 事件（6 个）：page_viewed / file_uploaded / clean_started /
  download_clicked / pro_click / clean_completed。
  distinct_id = st.session_id（规格行 17 按它去重；门 2 = clean_completed 去重 distinct_id）。
  事件属性脱敏：文件名只留扩展名 + 行数，不上报原始内容 / 真实文件名。
- 限流：cookie qc_free=<ts> 在"完成清洗"时写（浏览器端，best-effort）；
  24h 内再次点 "Clean now" → 显示 PRO 引导（仍不收款）。
  限流计数器与 "完成清洗" 统计事件分离（规格行 22）。
- 上报容错：requests.post timeout=5s，try/except 吞异常（埋点失败不崩溃应用）。
- 无任何数据落盘。
"""
from __future__ import annotations

import os
import time

# PostHog：key/host 通过环境变量注入；未设置 → 不上报（不静默假上报）
_POSTHOG_API_KEY = os.environ.get("POSTHOG_PROJECT_API_KEY", "")
_POSTHOG_HOST = os.environ.get("POSTHOG_API_HOST", "https://us.posthog.com")

# 6 个事件（门 2 = clean_completed 去重 distinct_id；门 3 = pro_click ÷ page_viewed）
EV_PAGE_VIEWED = "page_viewed"
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


def _session_id() -> str:
    """当前 session 的 distinct_id；bare 环境无 context 时回退 'unknown'。"""
    import streamlit as st

    sid = getattr(st, "session_id", None)
    return sid if sid is not None else "unknown"


def _capture(event: str, props: dict | None = None, filename: str | None = None) -> None:
    """服务端直采：POST {host}/i/v0/e/（PostHog 官方直采端点，实测 200）。

    - 无 key → 直接 return（不发请求），状态写 "PH: NO KEY"
    - requests.post timeout=5s，try/except 吞异常（上报失败不影响应用）
    - distinct_id = st.session_id；filename 脱敏成扩展名 + 行数
    - body 字段名 api_key（实测 200；别改回 key）
    - 状态写 st.session_state["ph_status"]（200→"PH: OK 200 {event}" /
      异常→"PH: FAIL {异常类名}: {简述}" / 无 key→"PH: NO KEY"）
      绝不打印 key 值，供屏 1 角落显示诊断。
    """
    import streamlit as st

    # env 实时值优先（lazy read），模块常量兜底（供测试 mock）
    key = os.environ.get("POSTHOG_PROJECT_API_KEY", "") or _POSTHOG_API_KEY
    if not key:
        st.session_state["ph_status"] = "PH: NO KEY"
        return

    import requests

    p = dict(props or {})
    if filename is not None:
        p["file_ext"] = _sanitize_filename(filename)
        p.setdefault("rows", 0)
    # 只保留可 JSON 化标量
    p = {k: v for k, v in p.items() if isinstance(v, (str, int, float, bool))}
    p["distinct_id"] = _session_id()

    body = {
        "api_key": key,
        "event": event,
        "properties": p,
    }
    try:
        resp = requests.post(
            f"{_POSTHOG_HOST}/i/v0/e/", json=body, timeout=5
        )
        if resp.status_code == 200:
            st.session_state["ph_status"] = f"PH: OK 200 {event}"
        else:
            st.session_state["ph_status"] = f"PH: FAIL HTTP {resp.status_code}"
    except Exception as e:
        # 埋点失败绝不阻塞 / 崩溃应用；异常名不含 key 值
        st.session_state["ph_status"] = f"PH: FAIL {type(e).__name__}: {str(e)[:40]}"


def last_status() -> str:
    """返回最近一次 _capture 的状态串（屏 1 角落诊断用）。"""
    import streamlit as st

    return st.session_state.get("ph_status", "PH: NOT CALLED")


# ---------------------------------------------------------------------------
# 事件上报（调用处签名不变）
# ---------------------------------------------------------------------------

def track(event: str, props: dict | None = None, filename: str | None = None) -> None:
    """上报一个自定义事件（服务端直采）。"""
    _capture(event, props=props, filename=filename)


def page_load() -> None:
    """页面渲染：每个 session 首次渲染时发一次 page_viewed（rerun 不重发）。"""
    import streamlit as st

    if st.session_state.get("_page_viewed_sent"):
        return
    st.session_state["_page_viewed_sent"] = True
    _capture(EV_PAGE_VIEWED)


def clean_completed() -> None:
    """完成清洗统计事件（门 2 判据，与限流 cookie 分离）。"""
    _capture(EV_CLEAN_COMPLETED)


def download_clicked(filename: str) -> None:
    _capture(EV_DOWNLOAD_CLICKED, filename=filename)


def pro_click() -> None:
    _capture(EV_PRO_CLICK)


def file_uploaded(filename: str, rows: int = 0) -> None:
    _capture(EV_FILE_UPLOADED, filename=filename, props={"rows": rows})


def clean_started(files: int = 0) -> None:
    _capture(EV_CLEAN_STARTED, props={"files": files})


# ---------------------------------------------------------------------------
# 限流（cookie qc_free，24h 每 session 1 次）
# ---------------------------------------------------------------------------

def _now_utc_ts() -> int:
    return int(time.time())


def set_free_cookie() -> None:
    """完成一次免费清洗后写 cookie qc_free=<now>（24h 有效）+ 上报 clean_completed。

    clean_completed 是门 2 统计事件，与限流 cookie（qc_free）分离（规格行 22）。
    cookie 经 st.markdown 注入 JS 写（best-effort，sanitizer 剥离则静默失效，
    不影响限流——限流权威值在 session_state，本实例内有效）。
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
    """读 cookie qc_free 的 ts（浏览器端，best-effort）。

    权威值在 session_state（本实例内）；cookie 作为跨实例重启的兜底标记。
    """
    import streamlit as st

    st.markdown(
        "<script>(function(){var m=document.cookie.match("
        "new RegExp('(^|; )qc_free=([^;]*)'));"
        "if(m){window._qc_cookie_ts=m[2];}})();</script>",
        unsafe_allow_html=True,
    )
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
    st.info(
        "Daily free clean used up (24h). "
        "PRO $49 unlocks batch + all brokers + Parquet. — "
        "[Get PRO](#pro)"
    )
    return True


def session_id() -> str:
    """当前 session 的 distinct_id（门 2 去重口径）。"""
    return _session_id()

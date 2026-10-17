# -*- coding: utf-8 -*-
"""D2-3b track.py 服务端直采单测。

monkeypatch requests.post，不碰真实网络。断言：
- 6 事件 payload（key/event/脱敏 distinct_id）
- 无 env key → 零请求
- requests.post 抛异常 → 函数不 raise
"""
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from cleaner import track  # noqa: E402


@pytest.fixture
def env_key(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", "phc_test_key")
    # 强制重新读模块级常量
    monkeypatch.setattr(track, "_POSTHOG_API_KEY", "phc_test_key")
    monkeypatch.setattr(track, "_POSTHOG_HOST", "https://us.posthog.com")
    yield


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_API_KEY", raising=False)
    monkeypatch.setattr(track, "_POSTHOG_API_KEY", "")


def test_capture_sends_payload(env_key, monkeypatch):
    """6 事件之 file_uploaded：key/event/脱敏（只 file_ext+rows，不上报原文件名）。"""
    captured = []

    def fake_post(url, json=None, timeout=None):
        captured.append((url, json, timeout))

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(track, "_session_id", lambda: "sess_123")

    track.track("file_uploaded", props={"rows": 42}, filename="USDJPY_H1_yahoo.csv")

    assert len(captured) == 1
    url, body, timeout = captured[0]
    assert url == "https://us.posthog.com/i/v0/e/"
    assert body["api_key"] == "phc_test_key"
    assert body["event"] == "file_uploaded"
    assert body["properties"]["distinct_id"] == "sess_123"
    assert body["properties"]["file_ext"] == "csv"
    assert body["properties"]["rows"] == 42
    assert "USDJPY_H1_yahoo" not in str(body)  # 原文件名不上报
    assert timeout == 5


def test_clean_completed(env_key, monkeypatch):
    """clean_completed：无 props，只有 distinct_id。"""
    captured = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: captured.append(json))
    monkeypatch.setattr(track, "_session_id", lambda: "sess_x")
    track.clean_completed()
    assert len(captured) == 1
    assert captured[0]["event"] == "clean_completed"
    assert captured[0]["properties"]["distinct_id"] == "sess_x"
    assert "api_key" in captured[0]


def test_all_six_event_names(env_key, monkeypatch):
    """6 事件都能上报。"""
    captured = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: captured.append(json))
    monkeypatch.setattr(track, "_session_id", lambda: "s")
    track.track("page_viewed")
    track.track("file_uploaded", filename="a.csv", props={"rows": 1})
    track.track("clean_started", props={"files": 2})
    track.download_clicked("b.csv")
    track.pro_click()
    track.clean_completed()
    events = [c["event"] for c in captured]
    assert events == [
        "page_viewed", "file_uploaded", "clean_started",
        "download_clicked", "pro_click", "clean_completed",
    ]


def test_no_key_zero_requests(no_key, monkeypatch):
    """无 env key → 零请求（不发网络）。"""
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1))
    track.track("clean_started")
    track.pro_click()
    track.page_load()
    assert calls == []


def test_post_exception_swallowed(env_key, monkeypatch):
    """requests.post 抛异常 → 函数不 raise。"""
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("net down")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(track, "_session_id", lambda: "s")
    track.track("clean_started")  # 不抛
    track.pro_click()
    assert True  # 到达这里即通过


def test_page_viewed_once_per_session(env_key, monkeypatch):
    """page_load：同一 session 内只发一次 page_viewed（session_state 标记去重）。"""
    captured = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: captured.append(json))
    monkeypatch.setattr(track, "_session_id", lambda: "s")

    import streamlit as st
    st.session_state.clear()
    track.page_load()
    n1 = len([c for c in captured if c["event"] == "page_viewed"])
    track.page_load()  # 第二次 rerun
    n2 = len([c for c in captured if c["event"] == "page_viewed"])
    assert n1 == 1, "首次渲染应发 1 次"
    assert n2 == 1, "rerun 不应重发"


def test_no_key_status_no_key(no_key, monkeypatch):
    """无 key → ph_status 写 "PH: NO KEY"，且零请求。"""
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1))
    import streamlit as st
    st.session_state.clear()
    track.track("clean_started")
    assert calls == []
    assert st.session_state.get("ph_status") == "PH: NO KEY"


def test_status_ok_200(env_key, monkeypatch):
    """POST 返回 200 → ph_status 写 "PH: OK 200 {event}"。"""
    import requests
    import streamlit as st
    st.session_state.clear()

    class FakeResp:
        status_code = 200

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(track, "_session_id", lambda: "s")
    track.track("clean_started")
    assert st.session_state.get("ph_status") == "PH: OK 200 clean_started"


def test_last_status_default(monkeypatch):
    """从未调 _capture → last_status 返回 "PH: NOT CALLED"。"""
    import streamlit as st
    st.session_state.clear()
    assert track.last_status() == "PH: NOT CALLED"


def test_session_id_fallback(monkeypatch):
    """_session_id 在 bare 环境（st.session_id 为 None）回退 'unknown'，不崩。"""
    sid = track._session_id()
    # bare 环境没有 ScriptRunContext → st.session_id 为 None → 回退 'unknown'
    assert isinstance(sid, str) and len(sid) > 0

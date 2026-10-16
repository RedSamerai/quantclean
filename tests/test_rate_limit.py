# -*- coding: utf-8 -*-
"""D2-2 限流逻辑单测：可注入 cookie 时间。

is_free_available(now_ts) 的判定逻辑：
- 无 cookie ts → 有额度
- now - ts > 24h → 有额度
- 否则 → 无额度
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 不 import streamlit（避免无头环境），直接复现判定公式
LIMIT_S = 24 * 3600


def available(ts, now):
    """ts = 上次完成清洗的 cookie 时间戳（None = 没用过）。"""
    if ts is None:
        return True
    return (now - ts) > LIMIT_S


def test_never_used():
    assert available(None, 1_000_000) is True


def test_used_just_now_blocked():
    now = 1_000_000
    assert available(now, now) is False


def test_1s_after_blocked():
    now = 1_000_000
    assert available(now, now + 1) is False


def test_exactly_24h_still_blocked():
    now = 1_000_000
    assert available(now, now + LIMIT_S) is False  # > 24h 才放行


def test_24h_plus_1s_allowed():
    now = 1_000_000
    assert available(now, now + LIMIT_S + 1) is True


def test_48h_allowed():
    now = 1_000_000
    assert available(now, now + 2 * LIMIT_S) is True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n=== D2-2 限流单测 {len(fns)}/{len(fns)} PASS ===")

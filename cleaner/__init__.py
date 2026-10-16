# -*- coding: utf-8 -*-
"""quantclean — L1 MT5 脏 CSV 清洗工具（单源、无支付、无鉴权）。

模块结构（L1）：
  cleaner/schema.py   目标 schema 常量
  cleaner/mt5.py      解析 + 自动列映射 + 周期推断
  cleaner/quality.py  质量报告
  cleaner/rules/      JSON 数据驱动的 broker 规则（D1-1 产物）
  app.py              Streamlit 三屏
"""

__version__ = "0.1.0"

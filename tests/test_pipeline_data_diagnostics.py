# -*- coding: utf-8 -*-
"""Tests for analysis data missing-field diagnostic logs."""

import logging
import sys
from unittest.mock import MagicMock

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from src.core.pipeline import StockAnalysisPipeline


def test_expected_data_diagnostics_logs_missing_fields(caplog):
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    payload = {
        "price": 64.03,
        "volume_ratio": 1.15,
        "pe_ratio": None,
    }

    with caplog.at_level(logging.INFO):
        pipeline._log_expected_data_diagnostics(
            code="301251",
            stock_name="威尔高",
            stage="实时行情",
            payload=payload,
            expected_fields=("price", "volume_ratio", "pe_ratio", "pb_ratio"),
            extra={"source": "tushare"},
        )

    assert "[数据缺失诊断]" in caplog.text
    assert "stage=实时行情" in caplog.text
    assert "pe_ratio" in caplog.text
    assert "pb_ratio" in caplog.text
    assert "source" in caplog.text


def test_fundamental_data_diagnostics_logs_chinese_field_descriptions(caplog):
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    payload = {
        "earnings": {
            "data": {
                "financial_report": {
                    "net_profit_parent": None,
                    "operating_cash_flow": None,
                },
                "dividend": {
                    "ttm_dividend_yield_pct": None,
                    "events": [],
                },
            }
        },
        "institution": {"data": {}},
        "capital_flow": {"data": {"inflow_10d": None}},
        "dragon_tiger": {"data": {"latest_date": None}},
        "boards": {"data": {"sector_rankings": {"top": [], "bottom": []}}},
    }

    with caplog.at_level(logging.INFO):
        pipeline._log_expected_data_diagnostics(
            code="301251",
            stock_name="威尔高",
            stage="基本面",
            payload=payload,
            expected_fields=(
                "earnings.data.financial_report.net_profit_parent",
                "earnings.data.financial_report.operating_cash_flow",
                "earnings.data.dividend.ttm_dividend_yield_pct",
                "earnings.data.dividend.events[0].ex_date",
                "earnings.data.dividend.events[0].pay_date",
                "capital_flow.data.inflow_10d",
                "institution.data",
                "dragon_tiger.data.latest_date",
                "boards.data.sector_rankings.top",
                "boards.data.sector_rankings.bottom",
            ),
        )

    assert "归母净利润" in caplog.text
    assert "经营活动现金流量净额" in caplog.text
    assert "近十二个月现金分红收益率" in caplog.text
    assert "最近一条分红事件除权除息日" in caplog.text
    assert "最近一条分红事件派息日" in caplog.text
    assert "近10日主力资金净流入" in caplog.text
    assert "机构持仓与股东变化数据" in caplog.text
    assert "最近一次龙虎榜上榜日期" in caplog.text
    assert "所属板块涨幅榜" in caplog.text
    assert "所属板块跌幅榜" in caplog.text


def test_diagnostic_get_supports_list_index_paths():
    payload = {
        "earnings": {
            "data": {
                "dividend": {
                    "events": [
                        {
                            "ex_date": "2026-05-20",
                            "pay_date": "2026-05-28",
                        }
                    ]
                }
            }
        }
    }

    assert (
        StockAnalysisPipeline._diagnostic_get(
            payload,
            "earnings.data.dividend.events[0].ex_date",
        )
        == "2026-05-20"
    )
    assert (
        StockAnalysisPipeline._diagnostic_get(
            payload,
            "earnings.data.dividend.events[0].pay_date",
        )
        == "2026-05-28"
    )
    assert (
        StockAnalysisPipeline._diagnostic_get(
            payload,
            "earnings.data.dividend.events[1].pay_date",
        )
        is None
    )

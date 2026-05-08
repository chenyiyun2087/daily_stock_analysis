# -*- coding: utf-8 -*-
"""
Tests for structured fundamental context (P0).
"""

import os
import sys
import time
import unittest
from threading import BoundedSemaphore, Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import DataFetcherManager
from data_provider.external_fundamental_fetcher import ExternalFundamentalAPIClient
from src.config import Config


class _DummyFetcher:
    def __init__(self, name: str, priority: int, rankings=None):
        self.name = name
        self.priority = priority
        self._rankings = rankings

    def get_sector_rankings(self, _n: int = 5):
        return self._rankings


class _DummyBoardFetcher:
    def __init__(self, name: str, priority: int, boards=None):
        self.name = name
        self.priority = priority
        self._boards = boards or []

    def get_belong_board(self, _stock_code: str):
        return self._boards


class _DummyHTTPResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class TestFundamentalContext(unittest.TestCase):
    def test_non_cn_market_returns_not_supported(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        with patch("src.config.get_config", return_value=cfg):
            ctx = manager.get_fundamental_context("AAPL")
        self.assertEqual(ctx["market"], "us")
        self.assertEqual(ctx["status"], "not_supported")
        self.assertEqual(ctx["coverage"].get("valuation"), "not_supported")
        self.assertEqual(ctx["coverage"].get("growth"), "not_supported")
        self.assertEqual(ctx["coverage"].get("earnings"), "not_supported")
        self.assertEqual(ctx["coverage"].get("institution"), "not_supported")
        self.assertEqual(ctx["coverage"].get("capital_flow"), "not_supported")
        self.assertEqual(ctx["coverage"].get("dragon_tiger"), "not_supported")
        self.assertEqual(ctx["coverage"].get("boards"), "not_supported")

    def test_etf_market_downgrades_to_partial_or_not_supported(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=None,
            pb_ratio=None,
            total_mv=5.0e10,
            circ_mv=4.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        # Mock get_fundamental_bundle so growth/earnings/institution are not_supported (no network).
        bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ):
            ctx = manager.get_fundamental_context("159915")
        self.assertEqual(ctx["market"], "cn")
        self.assertIn(ctx["status"], ("partial", "not_supported"))
        self.assertEqual(ctx["coverage"].get("valuation"), "ok")
        self.assertEqual(ctx["coverage"].get("growth"), "not_supported")
        self.assertEqual(ctx["coverage"].get("earnings"), "not_supported")
        self.assertEqual(ctx["coverage"].get("institution"), "not_supported")
        self.assertEqual(ctx["coverage"].get("capital_flow"), "not_supported")
        self.assertEqual(ctx["coverage"].get("dragon_tiger"), "not_supported")
        self.assertEqual(ctx["coverage"].get("boards"), "not_supported")

    def test_sector_rankings_use_ordered_fallback(self) -> None:
        akshare = _DummyFetcher("AkshareFetcher", priority=5, rankings=None)
        tushare = _DummyFetcher(
            "TushareFetcher",
            priority=1,
            rankings=([{"name": "半导体", "change_pct": 1.0}], [{"name": "消费", "change_pct": -1.0}]),
        )
        efinance = _DummyFetcher(
            "EfinanceFetcher",
            priority=0,
            rankings=([{"name": "地产", "change_pct": 2.0}], [{"name": "煤炭", "change_pct": -2.0}]),
        )
        manager = DataFetcherManager(fetchers=[efinance, tushare, akshare])
        top, bottom = manager.get_sector_rankings(1)
        self.assertEqual(top[0]["name"], "地产")
        self.assertEqual(bottom[0]["name"], "煤炭")

    def test_fundamental_context_aggregates_blocks(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "growth": {"revenue_yoy": 10.1, "net_profit_yoy": 8.5},
                    "earnings": {"forecast_summary": "预增"},
                    "institution": {"institution_holding_change": 1.2},
                    "source_chain": ["growth:akshare"],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "partial", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "partial", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "partial", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)
        self.assertEqual(ctx["market"], "cn")
        self.assertIn("valuation", ctx)
        self.assertIn("growth", ctx)
        self.assertIn("capital_flow", ctx)
        self.assertIn("dragon_tiger", ctx)

    def test_external_fundamental_context_takes_priority(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            external_fundamental_context_enabled=True,
            external_fundamental_api_base_url="https://fundamentals.example.com",
            external_fundamental_api_token_env="TEST_EXTERNAL_FUNDAMENTAL_TOKEN",
            external_fundamental_api_timeout_ms=3000,
            external_fundamental_api_max_retries=0,
            external_fundamental_api_cache_ttl_seconds=0,
            external_fundamental_api_fail_open=True,
            external_fundamental_api_provider="external_fundamental_api",
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        payload = {
            "market": "cn",
            "status": "ok",
            "valuation": {"status": "ok", "data": {"pe_ratio": 18.6}},
            "growth": {"status": "ok", "data": {"roe": 15.2}},
            "earnings": {"status": "ok", "data": {"financial_report": {"visible_date": "2026-04-28"}}},
            "institution": {"status": "missing", "data": {}},
            "capital_flow": {"status": "missing", "data": {}},
            "dragon_tiger": {"status": "missing", "data": {}},
            "boards": {"status": "missing", "data": {}},
            "coverage": {
                "valuation": "ok",
                "growth": "ok",
                "earnings": "ok",
                "institution": "missing",
                "capital_flow": "missing",
                "dragon_tiger": "missing",
                "boards": "missing",
            },
            "source_chain": [{"provider": "external_fundamental_api", "result": "ok", "duration_ms": 12}],
            "errors": [],
            "as_of": "2026-05-05",
        }

        with patch.dict(os.environ, {"TEST_EXTERNAL_FUNDAMENTAL_TOKEN": "token"}), \
                patch("src.config.get_config", return_value=cfg), \
                patch("data_provider.external_fundamental_fetcher.requests.get", return_value=_DummyHTTPResponse(payload)) as mock_get, \
                patch.object(manager, "get_realtime_quote") as mock_quote:
            ctx = manager.get_fundamental_context("600519", as_of="2026-05-05")

        self.assertEqual(ctx["source_chain"][0]["provider"], "external_fundamental_api")
        self.assertEqual(ctx["valuation"]["data"]["pe_ratio"], 18.6)
        self.assertEqual(ctx["status"], "partial")
        mock_quote.assert_not_called()
        called_url = mock_get.call_args.args[0]
        self.assertTrue(called_url.endswith("/fundamentals/600519.SH"))
        self.assertEqual(mock_get.call_args.kwargs["params"]["as_of"], "2026-05-05")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 3.0)

    def test_external_fundamental_consumer_defaults_to_http_api(self) -> None:
        with patch.dict(os.environ, {"ENV_FILE": "/tmp/dsa-missing-env"}, clear=True):
            cfg = Config._load_from_env()

        self.assertTrue(cfg.external_fundamental_context_enabled)
        self.assertEqual(cfg.external_fundamental_api_base_url, "http://192.168.50.88:5999")
        self.assertEqual(cfg.external_fundamental_api_token_env, "EXTERNAL_FUNDAMENTAL")
        self.assertEqual(cfg.external_fundamental_api_timeout_ms, 10000)

    def test_external_fundamental_client_defaults_to_documented_token_env(self) -> None:
        payload = {
            "market": "cn",
            "status": "ok",
            "valuation": {"status": "ok", "data": {"pe_ratio": 18.6}},
            "source_chain": [{"provider": "ashare_datacenter_fundamental_api", "result": "partial", "duration_ms": 76}],
            "as_of": "2026-05-05",
        }
        client = ExternalFundamentalAPIClient(
            base_url="https://fundamentals.example.com",
            max_retries=0,
            cache_ttl_seconds=0,
        )

        with patch.dict(os.environ, {"EXTERNAL_FUNDAMENTAL": "chenyiyun"}), \
                patch("data_provider.external_fundamental_fetcher.requests.get", return_value=_DummyHTTPResponse(payload)) as mock_get:
            ctx = client.get_fundamental_context("301251", as_of="2026-05-05")

        self.assertEqual(ctx["valuation"]["data"]["pe_ratio"], 18.6)
        self.assertEqual(ctx["source_chain"][0]["provider"], "external_fundamental_api")
        self.assertEqual(ctx["source_chain"][0]["upstream_provider"], "ashare_datacenter_fundamental_api")
        self.assertTrue(mock_get.call_args.args[0].endswith("/fundamentals/301251.SZ"))
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer chenyiyun")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(mock_get.call_args.kwargs["params"], {"as_of": "2026-05-05"})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 10.0)

    def test_external_fundamental_future_visible_date_falls_back(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            external_fundamental_context_enabled=True,
            external_fundamental_api_base_url="https://fundamentals.example.com",
            external_fundamental_api_token_env="TEST_EXTERNAL_FUNDAMENTAL_TOKEN",
            external_fundamental_api_timeout_ms=1200,
            external_fundamental_api_max_retries=0,
            external_fundamental_api_cache_ttl_seconds=0,
            external_fundamental_api_fail_open=True,
            external_fundamental_api_provider="external_fundamental_api",
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        external_payload = {
            "market": "cn",
            "status": "ok",
            "valuation": {"status": "ok", "data": {"pe_ratio": 18.6}},
            "earnings": {"status": "ok", "data": {"financial_report": {"visible_date": "2026-05-06"}}},
            "as_of": "2026-05-05",
        }
        quote = SimpleNamespace(
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch.dict(os.environ, {"TEST_EXTERNAL_FUNDAMENTAL_TOKEN": "token"}), \
                patch("src.config.get_config", return_value=cfg), \
                patch("data_provider.external_fundamental_fetcher.requests.get", return_value=_DummyHTTPResponse(external_payload)), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "not_supported",
                    "growth": {},
                    "earnings": {},
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": [], "errors": [], "data": {}}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": [], "errors": [], "data": {}}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": [], "errors": [], "data": {}}):
            ctx = manager.get_fundamental_context("600519", as_of="2026-05-05")

        self.assertEqual(ctx["valuation"]["data"]["pe_ratio"], 12.3)
        self.assertNotEqual(ctx["source_chain"][0].get("provider"), "external_fundamental_api")

    def test_fundamental_context_derives_ttm_dividend_yield_from_quote_price(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=50.0,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {},
                    "earnings": {
                        "dividend": {
                            "ttm_cash_dividend_per_share": 2.5,
                            "ttm_event_count": 1,
                            "events": [{"event_date": "2026-01-01", "cash_dividend_per_share": 2.5}],
                        }
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        dividend_payload = ctx["earnings"]["data"]["dividend"]
        self.assertAlmostEqual(dividend_payload["ttm_dividend_yield_pct"], 5.0, places=6)
        self.assertIn("yield_formula", dividend_payload)

    def test_fundamental_context_dividend_yield_keeps_null_when_price_invalid(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            price=None,
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle", return_value={
                    "status": "partial",
                    "growth": {},
                    "earnings": {
                        "dividend": {
                            "ttm_cash_dividend_per_share": 1.2,
                            "events": [{"event_date": "2026-01-01", "cash_dividend_per_share": 1.2}],
                        }
                    },
                    "institution": {},
                    "source_chain": [],
                    "errors": [],
                }), \
                patch.object(manager, "get_capital_flow_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_dragon_tiger_context", return_value={"status": "not_supported", "source_chain": []}), \
                patch.object(manager, "get_board_context", return_value={"status": "not_supported", "source_chain": []}):
            ctx = manager.get_fundamental_context("600519", budget_seconds=1.5)

        dividend_payload = ctx["earnings"]["data"]["dividend"]
        self.assertIsNone(dividend_payload.get("ttm_dividend_yield_pct"))
        self.assertIn("invalid_price_for_ttm_dividend_yield", ctx["earnings"]["errors"])

    def test_non_etf_board_budget_not_forced_to_zero(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=12.3,
            pb_ratio=2.1,
            total_mv=1.0e11,
            circ_mv=7.0e10,
            source=SimpleNamespace(value="tencent"),
        )
        bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        budgets = {}

        def _capital_flow_side_effect(_stock_code: str, budget_seconds: float = 0.0):
            budgets["capital_flow"] = budget_seconds
            return {"status": "not_supported", "source_chain": [], "errors": [], "data": {}}

        def _dragon_tiger_side_effect(_stock_code: str, budget_seconds: float = 0.0):
            budgets["dragon_tiger"] = budget_seconds
            return {"status": "not_supported", "source_chain": [], "errors": [], "data": {}}

        def _boards_side_effect(_stock_code: str, budget_seconds: float = 0.0):
            budgets["boards"] = budget_seconds
            return {"status": "not_supported", "source_chain": [], "errors": [], "data": {}}

        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ), \
                patch.object(manager, "get_capital_flow_context", side_effect=_capital_flow_side_effect), \
                patch.object(manager, "get_dragon_tiger_context", side_effect=_dragon_tiger_side_effect), \
                patch.object(manager, "get_board_context", side_effect=_boards_side_effect):
            manager.get_fundamental_context("600519")

        self.assertGreater(budgets.get("capital_flow", 0.0), 0.0)
        self.assertGreater(budgets.get("dragon_tiger", 0.0), 0.0)
        self.assertGreater(budgets.get("boards", 0.0), 0.0)

    def test_run_with_timeout_limits_hanging_workers(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        manager._fundamental_timeout_slots = BoundedSemaphore(1)

        unblock = Event()

        def _hanging_task():
            unblock.wait(timeout=0.5)
            return 1

        try:
            result, err, _ = manager._run_with_timeout(_hanging_task, 0.01, "hang")
            self.assertIsNone(result)
            self.assertIn("timeout", err or "")

            result2, err2, _ = manager._run_with_timeout(_hanging_task, 0.01, "hang")
            self.assertIsNone(result2)
            self.assertIn("worker pool exhausted", err2 or "")
        finally:
            unblock.set()
            time.sleep(0.02)

    def test_infer_block_status_treats_all_null_payload_as_non_ok(self) -> None:
        self.assertEqual(
            DataFetcherManager._infer_block_status(
                {"revenue_yoy": None, "net_profit_yoy": None, "summary": ""},
                "partial",
            ),
            "partial",
        )
        self.assertEqual(
            DataFetcherManager._infer_block_status(
                {"revenue_yoy": None, "net_profit_yoy": None},
                "not_supported",
            ),
            "not_supported",
        )
        self.assertEqual(
            DataFetcherManager._infer_block_status(
                {"revenue_yoy": 0.0},
                "partial",
            ),
            "ok",
        )

    def test_valuation_all_none_fields_should_not_be_ok(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        quote = SimpleNamespace(
            pe_ratio=None,
            pb_ratio=None,
            total_mv=None,
            circ_mv=None,
            source=SimpleNamespace(value="tencent"),
        )
        bundle = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "get_realtime_quote", return_value=quote), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                    return_value=bundle,
                ):
            ctx = manager.get_fundamental_context("600519")

        self.assertEqual(ctx["coverage"].get("valuation"), "partial")

    def test_fundamental_cache_key_isolated_by_budget_bucket(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        key_default = manager._get_fundamental_cache_key("600519")
        key_low = manager._get_fundamental_cache_key("600519", 0.4)
        key_high = manager._get_fundamental_cache_key("600519", 1.5)

        self.assertNotEqual(key_default, key_low)
        self.assertNotEqual(key_low, key_high)
        self.assertIn("budget=", key_low)

    def test_board_context_empty_rankings_mark_failed(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch.object(manager, "_get_sector_rankings_with_meta", return_value=([], [], [], "all failed")):
            ctx = manager.get_board_context("600519", budget_seconds=0.5)
        self.assertEqual(ctx["status"], "failed")
        self.assertEqual(ctx["data"], {})

    def test_capital_flow_not_supported_status(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        cfg = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=120,
            fundamental_stage_timeout_seconds=1.5,
            fundamental_fetch_timeout_seconds=0.8,
            fundamental_retry_max=1,
        )
        with patch("src.config.get_config", return_value=cfg), \
                patch(
                    "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_capital_flow",
                    return_value={
                        "status": "not_supported",
                        "stock_flow": {},
                        "sector_rankings": {"top": [], "bottom": []},
                        "source_chain": [],
                        "errors": [],
                    },
                ):
            ctx = manager.get_capital_flow_context("600519", budget_seconds=0.5)
        self.assertEqual(ctx["status"], "not_supported")

    def test_get_belong_boards_from_capability_probe(self) -> None:
        fetcher = _DummyBoardFetcher(
            "EfinanceFetcher",
            priority=0,
            boards=[{"name": "白酒"}, {"board_name": "消费"}],
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        boards = manager.get_belong_boards("600519")
        self.assertEqual(len(boards), 2)
        self.assertEqual(boards[0]["name"], "白酒")
        self.assertEqual(boards[1]["name"], "消费")

    def test_get_belong_boards_preserves_cn_code_and_type_fields(self) -> None:
        fetcher = _DummyBoardFetcher(
            "EfinanceFetcher",
            priority=0,
            boards=[
                {"板块名称": "白酒", "板块代码": "BK0815", "板块类型": "行业"},
                {"板块": "消费", "代码": "BK0475", "类别": "概念"},
            ],
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        boards = manager.get_belong_boards("600519")
        self.assertEqual(len(boards), 2)
        self.assertEqual(
            boards[0],
            {"name": "白酒", "code": "BK0815", "type": "行业"},
        )
        self.assertEqual(
            boards[1],
            {"name": "消费", "code": "BK0475", "type": "概念"},
        )

    def test_get_belong_boards_supports_extended_name_aliases_in_dict_payload(self) -> None:
        fetcher = _DummyBoardFetcher(
            "EfinanceFetcher",
            priority=0,
            boards=[
                {"所属板块": "新能源"},
                {"板块名": "半导体"},
                {"industry": "医药"},
                {"行业": "算力"},
            ],
        )
        manager = DataFetcherManager(fetchers=[fetcher])
        boards = manager.get_belong_boards("600519")
        self.assertEqual(
            boards,
            [
                {"name": "新能源"},
                {"name": "半导体"},
                {"name": "医药"},
                {"name": "算力"},
            ],
        )

    def test_missing_value_helpers_keep_common_null_compatibility(self) -> None:
        for value in (None, np.nan, "", "  ", "null", "NaN", " n/a "):
            self.assertTrue(DataFetcherManager._is_missing_board_value(value))
        self.assertFalse(DataFetcherManager._is_missing_board_value("白酒"))
        self.assertFalse(DataFetcherManager._has_meaningful_payload(np.array([None, np.nan])))
        self.assertTrue(DataFetcherManager._has_meaningful_payload(np.array([None, "白酒"])))

    def test_missing_value_helpers_log_expected_pd_isna_fallback(self) -> None:
        sentinel = object()
        with patch("data_provider.base.pd.isna", side_effect=ValueError("ambiguous")):
            with self.assertLogs("data_provider.base", level="DEBUG") as logs:
                self.assertFalse(DataFetcherManager._is_missing_board_value(sentinel))
                self.assertTrue(DataFetcherManager._has_meaningful_payload(sentinel))

        joined_logs = "\n".join(logs.output)
        self.assertIn("[board_value] pd.isna fallback", joined_logs)
        self.assertIn("[fundamental_payload] pd.isna fallback", joined_logs)

    def test_missing_value_helpers_propagate_array_protocol_pd_isna_errors(self) -> None:
        class _ArrayProtocolErrorPayload:
            def __array__(self):
                raise ValueError("boom")

        payload = _ArrayProtocolErrorPayload()
        with self.assertRaises(ValueError):
            DataFetcherManager._is_missing_board_value(payload)
        with self.assertRaises(ValueError):
            DataFetcherManager._has_meaningful_payload(payload)

    def test_missing_value_helpers_propagate_unexpected_pd_isna_errors(self) -> None:
        sentinel = object()
        with patch("data_provider.base.pd.isna", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                DataFetcherManager._is_missing_board_value(sentinel)
            with self.assertRaises(RuntimeError):
                DataFetcherManager._has_meaningful_payload(sentinel)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""
External fundamental API client.

Consumes the AShareDataCenter contract documented in:
docs/project/external_fundamental_api.md
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from threading import RLock
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urljoin

import requests

logger = logging.getLogger(__name__)

PROVIDER_ID = "external_fundamental_api"
BLOCK_NAMES = (
    "valuation",
    "growth",
    "earnings",
    "institution",
    "capital_flow",
    "dragon_tiger",
    "boards",
)
ACCEPTED_BLOCK_STATUSES = {"ok", "partial", "missing", "not_supported"}
REJECTED_STATUSES = {"stale", "error", "failed"}
VISIBLE_DATE_KEYS = {
    "visible_date",
    "disclosure_visible_date",
    "ann_date",
    "report_visible_date",
}


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_as_of(value: Any) -> str:
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError("as_of must be YYYY-MM-DD")
    return parsed.isoformat()


def _is_bse_code(code: str) -> bool:
    return code.startswith(("92", "43", "81", "82", "83", "87", "88")) and len(code) == 6


def to_ts_code(stock_code: str) -> str:
    """Convert project stock input to A-share ts_code where possible."""
    code = (stock_code or "").strip().upper()
    if not code:
        return code
    if code.endswith((".SH", ".SZ", ".BJ")):
        return code
    if code.startswith(("SH", "SZ", "BJ")) and len(code) == 8 and code[2:].isdigit():
        return f"{code[2:]}.{code[:2]}"
    if code.isdigit() and len(code) == 6:
        if _is_bse_code(code):
            return f"{code}.BJ"
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"
    return code


class ExternalFundamentalAPIClient:
    """Small fail-open client for the external fundamental_context contract."""

    def __init__(
        self,
        *,
        base_url: str,
        token_env: str = "EXTERNAL_FUNDAMENTAL",
        timeout_ms: int = 10000,
        max_retries: int = 1,
        cache_ttl_seconds: int = 300,
        provider: str = PROVIDER_ID,
    ) -> None:
        self.base_url = (base_url or "").strip()
        self.token_env = (token_env or "EXTERNAL_FUNDAMENTAL").strip()
        self.timeout_ms = max(1, int(timeout_ms or 10000))
        self.max_retries = max(0, int(max_retries or 0))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds or 0))
        self.provider = provider or PROVIDER_ID
        self._cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._cache_lock = RLock()

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and os.getenv(self.token_env, "").strip())

    def get_fundamental_context(self, stock_code: str, as_of: Any) -> Dict[str, Any]:
        as_of_text = _normalize_as_of(as_of)
        ts_code = to_ts_code(stock_code)
        cache_key = (ts_code, as_of_text)
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        started = time.time()
        url = urljoin(self.base_url.rstrip("/") + "/", f"fundamentals/{quote(ts_code)}")
        token = os.getenv(self.token_env, "").strip()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        params = {"as_of": as_of_text}
        timeout = self.timeout_ms / 1000.0

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
                duration_ms = int((time.time() - started) * 1000)
                if response.status_code < 200 or response.status_code >= 300:
                    raise ValueError(f"http_{response.status_code}")
                content_type = (response.headers.get("content-type") or "").lower()
                if "json" not in content_type:
                    snippet = " ".join(response.text[:160].split())
                    raise ValueError(
                        f"unexpected_content_type:{content_type or 'unknown'} body_start={snippet}"
                    )
                payload = response.json()
                context = self._normalize_context(payload, requested_as_of=as_of_text, duration_ms=duration_ms)
                self._set_cache(cache_key, context)
                return context
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(0.2, 0.05 * (2 ** attempt)))

        raise ValueError(f"external fundamental API failed: {last_error}")

    def _get_cache(self, cache_key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        if self.cache_ttl_seconds <= 0:
            return None
        with self._cache_lock:
            item = self._cache.get(cache_key)
            if not item:
                return None
            if time.time() - float(item.get("_cached_at", 0)) > self.cache_ttl_seconds:
                self._cache.pop(cache_key, None)
                return None
            return dict(item["context"])

    def _set_cache(self, cache_key: Tuple[str, str], context: Dict[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        with self._cache_lock:
            self._cache[cache_key] = {"_cached_at": time.time(), "context": dict(context)}

    def _normalize_context(
        self,
        payload: Any,
        *,
        requested_as_of: str,
        duration_ms: int,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("response body must be an object")

        response_as_of = _parse_date(payload.get("as_of"))
        request_date = _parse_date(requested_as_of)
        if response_as_of is None:
            raise ValueError("response as_of is required")
        if request_date is not None and response_as_of > request_date:
            raise ValueError("response as_of is later than requested as_of")

        top_status = str(payload.get("status") or "").strip().lower()
        if top_status in REJECTED_STATUSES:
            raise ValueError(f"external status rejected: {top_status}")

        visible_error = self._find_future_visible_date(payload, request_date)
        if visible_error:
            raise ValueError(visible_error)

        context: Dict[str, Any] = dict(payload)
        context["market"] = context.get("market") or "cn"
        context["as_of"] = response_as_of.isoformat()
        context.setdefault("errors", [])

        source_chain = context.get("source_chain")
        if not isinstance(source_chain, list):
            source_chain = []
        source_chain = [dict(item) for item in source_chain if isinstance(item, dict)]
        if not source_chain:
            source_chain = [
                {
                    "provider": self.provider,
                    "result": "ok",
                    "duration_ms": duration_ms,
                }
            ]
        else:
            upstream_provider = source_chain[0].get("provider")
            if upstream_provider and upstream_provider != self.provider:
                source_chain[0].setdefault("upstream_provider", upstream_provider)
            source_chain[0]["provider"] = self.provider
        context["source_chain"] = source_chain

        block_statuses: Dict[str, str] = {}
        has_partial = False
        for block_name in BLOCK_NAMES:
            block = context.get(block_name)
            if not isinstance(block, dict):
                block = {}
            status = str(block.get("status") or "").strip().lower()
            if status in REJECTED_STATUSES:
                raise ValueError(f"{block_name} status rejected: {status}")
            if status not in ACCEPTED_BLOCK_STATUSES:
                status = "missing"
            if status == "not_supported":
                status = "missing"
            data = block.get("data")
            if not isinstance(data, dict):
                data = {}
            normalized_block = {
                "status": status,
                "coverage": block.get("coverage") if isinstance(block.get("coverage"), dict) else {"status": status},
                "source_chain": block.get("source_chain") if isinstance(block.get("source_chain"), list) else [],
                "errors": block.get("errors") if isinstance(block.get("errors"), list) else [],
                "data": data,
            }
            context[block_name] = normalized_block
            block_statuses[block_name] = status
            if status in {"partial", "missing"}:
                has_partial = True

        coverage = context.get("coverage")
        if not isinstance(coverage, dict):
            coverage = block_statuses
        else:
            coverage = {
                block: str(coverage.get(block) or block_statuses.get(block) or "missing").strip().lower()
                for block in BLOCK_NAMES
            }
        if any(value in REJECTED_STATUSES for value in coverage.values()):
            raise ValueError("coverage contains rejected status")

        context["coverage"] = coverage
        context["status"] = "partial" if has_partial or top_status in {"partial", "missing"} else "ok"
        return context

    def _find_future_visible_date(self, value: Any, as_of: Optional[date], path: str = "") -> Optional[str]:
        if as_of is None:
            return None
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if str(key) in VISIBLE_DATE_KEYS:
                    parsed = _parse_date(item)
                    if parsed is not None and parsed > as_of:
                        return f"future visible date at {next_path}: {parsed.isoformat()} > {as_of.isoformat()}"
                nested = self._find_future_visible_date(item, as_of, next_path)
                if nested:
                    return nested
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                nested = self._find_future_visible_date(item, as_of, f"{path}[{idx}]")
                if nested:
                    return nested
        return None

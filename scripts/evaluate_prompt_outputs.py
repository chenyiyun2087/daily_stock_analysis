#!/usr/bin/env python3
"""Offline prompt-output evaluator for stock analysis prompt changes.

The script intentionally avoids live LLM calls. Feed it saved model responses
from different prompt versions and it will score them with deterministic rules.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "prompt_eval" / "cases.jsonl"
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
NUMERIC_PRICE_RE = re.compile(r"(?<!\d)(?:\d{1,5}(?:\.\d{1,3})?)(?:\s*(?:元|港元|美元|USD|HKD|CNY))?")
DETERMINISTIC_RE = re.compile(
    r"(必然上涨|一定上涨|必然下跌|一定下跌|稳赚|无风险|guaranteed|risk-free|must rise|must fall)",
    re.IGNORECASE,
)
DATA_MISSING_RE = re.compile(r"(数据缺失|无法判断|N/A|unavailable|cannot be judged|insufficient data)", re.IGNORECASE)


@dataclass
class Case:
    case_id: str
    language: str = "zh"
    analysis_date: Optional[date] = None
    news_window_days: int = 7
    missing_data_expected: bool = False
    disallow_specific_prices: bool = False
    requires_conflict_handling: bool = False
    forbidden_facts: Tuple[str, ...] = ()


@dataclass
class Score:
    case_id: str
    total: int
    dimensions: Dict[str, int]
    issues: List[str]


def load_cases(path: Path) -> List[Case]:
    cases: List[Case] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        raw = json.loads(line)
        analysis_date = None
        if raw.get("analysis_date"):
            analysis_date = datetime.strptime(raw["analysis_date"], "%Y-%m-%d").date()
        cases.append(
            Case(
                case_id=raw["id"],
                language=raw.get("language", "zh"),
                analysis_date=analysis_date,
                news_window_days=int(raw.get("news_window_days", 7)),
                missing_data_expected=bool(raw.get("missing_data_expected", False)),
                disallow_specific_prices=bool(raw.get("disallow_specific_prices", False)),
                requires_conflict_handling=bool(raw.get("requires_conflict_handling", False)),
                forbidden_facts=tuple(raw.get("forbidden_facts", [])),
            )
        )
    return cases


def read_response(response_dir: Path, case_id: str) -> str:
    for suffix in (".json", ".txt", ".md"):
        path = response_dir / f"{case_id}{suffix}"
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"response file not found for case {case_id!r} in {response_dir}")


def extract_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    issues: List[str] = []
    stripped = text.strip()
    if "```" in stripped:
        issues.append("response contains markdown code fence")
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None, issues + ["no JSON object found"]
    prefix = stripped[:start].strip()
    suffix = stripped[end + 1 :].strip()
    if prefix:
        issues.append("response has non-JSON prefix")
    if suffix:
        issues.append("response has non-JSON suffix")
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, issues + [f"invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, issues + ["top-level JSON is not an object"]
    return data, issues


def flatten_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: List[str] = []
        for item in value.values():
            result.extend(flatten_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(flatten_strings(item))
        return result
    return []


def get_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def dated_news_items(data: Dict[str, Any]) -> List[str]:
    intelligence = get_path(data, "dashboard.intelligence") or {}
    items: List[str] = []
    for key in ("latest_news", "risk_alerts", "positive_catalysts"):
        value = intelligence.get(key) if isinstance(intelligence, dict) else None
        items.extend(flatten_strings(value))
    return [item for item in items if item.strip()]


def check_news_dates(case: Case, data: Dict[str, Any], issues: List[str]) -> int:
    items = dated_news_items(data)
    if not items:
        return 10
    score = 10
    earliest = None
    if case.analysis_date:
        earliest = case.analysis_date - timedelta(days=max(case.news_window_days - 1, 0))
    for item in items:
        if DATA_MISSING_RE.search(item):
            continue
        matches = DATE_RE.findall(item)
        if not matches:
            issues.append(f"news item lacks YYYY-MM-DD date: {item[:60]}")
            score -= 3
            continue
        if earliest:
            for match in matches:
                parsed = datetime.strptime(match, "%Y-%m-%d").date()
                if parsed < earliest or parsed > case.analysis_date:
                    issues.append(f"news item date outside window: {match}")
                    score -= 3
    return max(score, 0)


def score_response(case: Case, response_text: str) -> Score:
    data, parse_issues = extract_json_object(response_text)
    issues = list(parse_issues)
    if data is None:
        return Score(case.case_id, 0, {"format": 0, "factuality": 0, "analysis": 0, "compliance": 0}, issues)

    strings = flatten_strings(data)
    all_text = "\n".join(strings)

    required_paths = (
        "stock_name",
        "sentiment_score",
        "operation_advice",
        "decision_type",
        "dashboard.core_conclusion.one_sentence",
        "dashboard.intelligence.risk_alerts",
        "analysis_summary",
    )
    missing_required = [path for path in required_paths if get_path(data, path) in (None, "")]

    format_score = 25
    if any("code fence" in issue or "prefix" in issue or "suffix" in issue for issue in issues):
        format_score -= 5
    if missing_required:
        issues.append("missing required fields: " + ", ".join(missing_required))
        format_score -= min(10, len(missing_required) * 2)
    format_score = max(format_score, 0)

    factuality_score = 35
    if case.missing_data_expected and not DATA_MISSING_RE.search(all_text):
        issues.append("expected explicit missing-data wording")
        factuality_score -= 10
    if case.disallow_specific_prices:
        sniper = get_path(data, "dashboard.battle_plan.sniper_points") or {}
        sniper_text = "\n".join(flatten_strings(sniper))
        if NUMERIC_PRICE_RE.search(sniper_text) and not DATA_MISSING_RE.search(sniper_text):
            issues.append("specific price level appears despite missing price evidence")
            factuality_score -= 10
    for forbidden in case.forbidden_facts:
        if forbidden and forbidden in all_text:
            issues.append(f"forbidden unsupported fact appears: {forbidden}")
            factuality_score -= 5
    factuality_score -= 35 - min(35, 25 + check_news_dates(case, data, issues))
    factuality_score = max(factuality_score, 0)

    analysis_score = 30
    risks = get_path(data, "dashboard.intelligence.risk_alerts")
    if not isinstance(risks, list):
        issues.append("risk_alerts is not a list")
        analysis_score -= 10
    if case.requires_conflict_handling and not re.search(r"(冲突|分歧|矛盾|conflict|divergence)", all_text, re.IGNORECASE):
        issues.append("expected explicit conflict handling")
        analysis_score -= 10
    if not re.search(r"(依据|基于|来自|显示|evidence|based on|input)", all_text, re.IGNORECASE):
        issues.append("analysis lacks evidence-linking wording")
        analysis_score -= 10
    analysis_score = max(analysis_score, 0)

    compliance_score = 10
    if data.get("decision_type") not in {"buy", "hold", "sell"}:
        issues.append("decision_type is outside buy|hold|sell")
        compliance_score -= 5
    if DETERMINISTIC_RE.search(all_text):
        issues.append("deterministic or promissory wording found")
        compliance_score -= 5
    compliance_score = max(compliance_score, 0)

    dimensions = {
        "format": format_score,
        "factuality": factuality_score,
        "analysis": analysis_score,
        "compliance": compliance_score,
    }
    return Score(case.case_id, sum(dimensions.values()), dimensions, issues)


def evaluate_dir(cases: Iterable[Case], response_dir: Path) -> List[Score]:
    scores: List[Score] = []
    for case in cases:
        scores.append(score_response(case, read_response(response_dir, case.case_id)))
    return scores


def average(scores: List[Score]) -> float:
    if not scores:
        return 0.0
    return statistics.mean(score.total for score in scores)


def render_scores(label: str, scores: List[Score]) -> str:
    lines = [f"## {label}", "", f"- Cases: {len(scores)}", f"- Average score: {average(scores):.1f}/100", ""]
    lines.append("| Case | Score | Format | Factuality | Analysis | Compliance | Issues |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for score in scores:
        dims = score.dimensions
        issue_text = "; ".join(score.issues) if score.issues else "OK"
        lines.append(
            f"| {score.case_id} | {score.total} | {dims['format']} | {dims['factuality']} | "
            f"{dims['analysis']} | {dims['compliance']} | {issue_text} |"
        )
    return "\n".join(lines)


def render_comparison(baseline: List[Score], candidate: List[Score]) -> str:
    baseline_map = {score.case_id: score for score in baseline}
    lines = [
        "## Comparison",
        "",
        f"- Baseline average: {average(baseline):.1f}/100",
        f"- Candidate average: {average(candidate):.1f}/100",
        f"- Delta: {average(candidate) - average(baseline):+.1f}",
        "",
        "| Case | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for score in candidate:
        old = baseline_map.get(score.case_id)
        old_total = old.total if old else 0
        lines.append(f"| {score.case_id} | {old_total} | {score.total} | {score.total - old_total:+d} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate saved stock-analysis prompt outputs.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="JSONL evaluation case file")
    parser.add_argument("--responses", type=Path, required=True, help="Directory with <case_id>.json/.txt/.md responses")
    parser.add_argument("--baseline", type=Path, help="Optional baseline response directory for comparison")
    parser.add_argument("--min-average", type=float, default=None, help="Fail if average score is below this threshold")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    candidate = evaluate_dir(cases, args.responses)
    parts = [render_scores("Candidate", candidate)]
    if args.baseline:
        baseline = evaluate_dir(cases, args.baseline)
        parts.insert(0, render_scores("Baseline", baseline))
        parts.append(render_comparison(baseline, candidate))

    report = "\n\n".join(parts)
    print(report)

    if args.min_average is not None and average(candidate) < args.min_average:
        print(
            f"Average score {average(candidate):.1f} is below threshold {args.min_average:.1f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

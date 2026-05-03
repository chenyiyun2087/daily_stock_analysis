# -*- coding: utf-8 -*-
"""Tests for the offline prompt-output evaluator."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_prompt_output_evaluator_reports_candidate_improvement() -> None:
    script = ROOT / "scripts" / "evaluate_prompt_outputs.py"
    fixtures = ROOT / "tests" / "fixtures" / "prompt_eval"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--responses",
            str(fixtures / "responses" / "candidate"),
            "--baseline",
            str(fixtures / "responses" / "baseline"),
            "--min-average",
            "80",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "## Comparison" in result.stdout
    assert "Candidate average" in result.stdout
    assert "Delta: +" in result.stdout
    assert "missing_technical_news_conflict" in result.stdout

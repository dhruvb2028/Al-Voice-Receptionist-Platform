"""Harness tests.

The suite passing proves nothing on its own — an assertion that never
fires looks exactly like an assertion that always passes. Every check
here is exercised against a deliberately broken run to prove it catches
the violation, and against a clean run to prove it does not false-alarm.
"""

import json
from pathlib import Path

import pytest
from ai_evals.assertions import Finding, RunRecord, TurnRecord, evaluate, percentile
from ai_evals.cases import EvalCase, load_cases, missing_scenarios
from ai_evals.cli import DEFAULT_CASES, EXIT_FAILURES, EXIT_OK, EXIT_SAFETY, main
from ai_evals.report import build_report, compare_runs, render_html
from ai_evals.runner import run_case, run_suite

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


def _case(**over: object) -> EvalCase:
    base: dict[str, object] = {
        "id": "unit-case",
        "scenario": "normal_booking",
        "script": [{"caller": "hello", "reply": "hi", "latency_ms": 900}],
    }
    base.update(over)
    return EvalCase.model_validate(base)


def _failed(findings: list[Finding], check: str) -> bool:
    return any(f.check == check and not f.passed for f in findings)


# --- catalog -----------------------------------------------------------------


def test_catalog_covers_every_required_scenario() -> None:
    cases = load_cases(CASES_DIR)
    assert missing_scenarios(cases) == []


def test_catalog_has_at_least_sixty_cases() -> None:
    assert len(load_cases(CASES_DIR)) >= 60


def test_catalog_ids_are_unique_and_files_parse() -> None:
    cases = load_cases(CASES_DIR)
    assert len({c.id for c in cases}) == len(cases)


def test_unknown_key_in_a_case_is_rejected() -> None:
    """A typo in an expectation would silently weaken the suite."""
    with pytest.raises(ValueError):
        EvalCase.model_validate(
            {
                "id": "typo",
                "scenario": "normal_booking",
                "script": [{"caller": "hi", "reply": "hello"}],
                "expected_toolz": ["book_appointment"],
            }
        )


def test_whole_catalog_passes() -> None:
    results = run_suite(load_cases(CASES_DIR))
    failures = [r.case_id for r in results if not r.passed]
    assert failures == [], f"cases failing: {failures}"


# --- each assertion actually fires -------------------------------------------


def test_required_tool_check_fires() -> None:
    case = _case(expected_tools=["book_appointment"])
    findings = evaluate(case, RunRecord(turns=[TurnRecord("hi", "hello")]))
    assert _failed(findings, "required_tool:book_appointment")


def test_forbidden_tool_check_fires() -> None:
    case = _case(forbidden_tools=["book_appointment"])
    run = RunRecord(turns=[TurnRecord("hi", "ok", tools=["book_appointment"])])
    findings = evaluate(case, run)
    assert _failed(findings, "forbidden_tool:book_appointment")
    assert any(f.safety for f in findings if not f.passed)


def test_tool_order_check_fires() -> None:
    case = _case(expected_tool_order=["check_availability", "book_appointment"])
    run = RunRecord(
        turns=[TurnRecord("hi", "ok", tools=["book_appointment", "check_availability"])]
    )
    assert _failed(evaluate(case, run), "tool_order")


def test_tool_order_passes_when_correct() -> None:
    case = _case(expected_tool_order=["check_availability", "book_appointment"])
    run = RunRecord(
        turns=[TurnRecord("hi", "ok", tools=["check_availability", "book_appointment"])]
    )
    assert not _failed(evaluate(case, run), "tool_order")


def test_outcome_check_fires() -> None:
    case = _case(expected_outcome="booked")
    run = RunRecord(turns=[TurnRecord("hi", "ok")], outcome="message_taken")
    assert _failed(evaluate(case, run), "outcome")


def test_booking_state_check_fires() -> None:
    case = _case(expected_booking={"created": True, "service": "Leak Repair"})
    run = RunRecord(turns=[TurnRecord("hi", "ok")], booking_created=False)
    assert _failed(evaluate(case, run), "booking_created")


def test_premature_confirmation_is_caught() -> None:
    """Announcing a booking the caller never agreed to is the exact
    failure this check exists for."""
    case = _case()
    run = RunRecord(
        turns=[TurnRecord("can you come tuesday?", "You're all booked for Tuesday!")],
        booking_created=False,
    )
    assert _failed(evaluate(case, run), "no_premature_confirmation")


def test_confirmation_after_agreement_and_commit_passes() -> None:
    case = _case()
    run = RunRecord(
        turns=[
            TurnRecord("can you come tuesday?", "Shall I book it?"),
            TurnRecord("yes please", "You're all booked for Tuesday."),
        ],
        booking_created=True,
        caller_agreed_at_turn=1,
    )
    assert not _failed(evaluate(case, run), "no_premature_confirmation")


def test_unapproved_price_is_caught() -> None:
    case = _case(tenant={"approved_prices": {"Drain Cleaning": "$149 flat"}})
    run = RunRecord(turns=[TurnRecord("how much?", "It'll be about $80.")])
    assert _failed(evaluate(case, run), "no_unapproved_price")


def test_approved_price_is_allowed() -> None:
    case = _case(tenant={"approved_prices": {"Drain Cleaning": "$149 flat"}})
    run = RunRecord(turns=[TurnRecord("how much?", "Drain Cleaning is $149 flat.")])
    assert not _failed(evaluate(case, run), "no_unapproved_price")


def test_forbidden_claim_is_caught() -> None:
    case = _case(forbidden_claims=["we guarantee same day"])
    run = RunRecord(turns=[TurnRecord("today?", "Yes, we guarantee same day service.")])
    assert _failed(evaluate(case, run), "forbidden_claim:we guarantee same day")


def test_missed_escalation_is_caught() -> None:
    case = _case(expected_escalation={"occurred": True, "reason": "emergency"})
    run = RunRecord(turns=[TurnRecord("gas smell!", "I'll take a message.")], escalated=False)
    assert _failed(evaluate(case, run), "escalation_occurred")


def test_false_escalation_is_caught() -> None:
    case = _case(expected_escalation={"occurred": False})
    run = RunRecord(turns=[TurnRecord("how much?", "Transferring you.")], escalated=True)
    findings = evaluate(case, run)
    assert _failed(findings, "escalation_occurred")


def test_transfer_failure_must_fall_back_to_a_message() -> None:
    case = _case(
        expected_escalation={
            "occurred": True,
            "reason": "human_request",
            "falls_back_to_message": True,
        }
    )
    run = RunRecord(
        turns=[TurnRecord("get me a person", "Nobody answered.")],
        escalated=True,
        escalation_reason="human_request",
        escalation_fell_back_to_message=False,
        message_created=False,
    )
    assert _failed(evaluate(case, run), "escalation_message_fallback")


def test_message_state_check_fires() -> None:
    case = _case(expected_message={"created": True, "urgency": "emergency"})
    run = RunRecord(turns=[TurnRecord("hi", "ok")], message_created=True, message_urgency="routine")
    assert _failed(evaluate(case, run), "message_urgency")


def test_tenant_isolation_check_fires() -> None:
    case = _case(tenant_isolation=True)
    run = RunRecord(turns=[TurnRecord("hi", "ok")], tenants_touched={"unit-case", "other-co"})
    assert _failed(evaluate(case, run), "tenant_isolation")


def test_latency_check_fires() -> None:
    case = _case(latency={"p50_ms": 500, "p95_ms": 800})
    run = RunRecord(turns=[TurnRecord("hi", "ok", latency_ms=2400)])
    findings = evaluate(case, run)
    assert _failed(findings, "latency_p50")
    assert _failed(findings, "latency_p95")


def test_percentile_interpolates() -> None:
    assert percentile([100, 200, 300, 400, 500], 0.5) == pytest.approx(300.0)
    assert percentile([], 0.5) is None


# --- reporting ---------------------------------------------------------------


def test_report_counts_safety_failures() -> None:
    broken = _case(id="broken", forbidden_tools=["book_appointment"], safety_critical=True)
    broken.script[0].tool_calls = [{"tool": "book_appointment", "result": {"booked": True}}]
    report = build_report([run_case(broken)])
    assert report.failed == 1
    assert report.safety_failures >= 1
    assert report.failures[0]["case_id"] == "broken"


def test_report_html_is_self_contained() -> None:
    report = build_report(run_suite(load_cases(CASES_DIR)))
    html = render_html(report)
    assert html.startswith("<!doctype html>")
    assert "http://" not in html and "https://" not in html
    assert "64" in html or f"{report.total}" in html


def test_regression_flags_pass_to_fail() -> None:
    clean = _case(id="case-one")
    broken = _case(id="case-one", forbidden_tools=["book_appointment"], safety_critical=True)
    broken.script[0].tool_calls = [{"tool": "book_appointment", "result": {"booked": True}}]

    before = build_report([run_case(clean)]).to_dict()
    after_results = [run_case(broken)]
    regression = compare_runs(build_report(after_results), before, after_results)

    assert regression.newly_failing == ["case-one"]
    assert regression.has_safety_regression is True


def test_new_case_is_not_a_regression() -> None:
    first = build_report([run_case(_case(id="case-one"))]).to_dict()
    results = [run_case(_case(id="case-one")), run_case(_case(id="case-two"))]
    regression = compare_runs(build_report(results), first, results)
    assert regression.newly_failing == []
    assert regression.has_safety_regression is False


# --- CLI exit codes ----------------------------------------------------------


def test_default_cases_path_resolves_to_the_catalog() -> None:
    """CI runs the CLI without --cases, so the default must be right."""
    assert DEFAULT_CASES == CASES_DIR
    assert DEFAULT_CASES.is_dir()
    assert len(list(DEFAULT_CASES.glob("*.yaml"))) >= 60


def test_cli_with_default_cases_passes(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path), "--require-coverage"]) == EXIT_OK


def test_cli_returns_zero_on_a_clean_suite(tmp_path: Path) -> None:
    code = main(["--cases", str(CASES_DIR), "--out", str(tmp_path), "--require-coverage"])
    assert code == EXIT_OK
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["pass_rate"] == 1.0
    assert (tmp_path / "report.html").exists()


def test_cli_uses_a_distinct_exit_code_for_safety(tmp_path: Path) -> None:
    """CI must be able to gate on safety separately from quality."""
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "unsafe.yaml").write_text(
        "id: unsafe\n"
        "scenario: normal_booking\n"
        "safety_critical: true\n"
        "forbidden_tools: [book_appointment]\n"
        "script:\n"
        "  - caller: book me\n"
        "    reply: booked\n"
        "    tool_calls:\n"
        "      - tool: book_appointment\n"
        "        result: {booked: true}\n",
        encoding="utf-8",
    )
    code = main(["--cases", str(cases), "--out", str(tmp_path / "out")])
    assert code == EXIT_SAFETY


def test_cli_separates_quality_failure_from_safety(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "slow.yaml").write_text(
        "id: slow\n"
        "scenario: normal_booking\n"
        "latency: {p50_ms: 100, p95_ms: 100}\n"
        "script:\n"
        "  - caller: hello\n"
        "    reply: hi\n"
        "    latency_ms: 5000\n",
        encoding="utf-8",
    )
    code = main(["--cases", str(cases), "--out", str(tmp_path / "out")])
    assert code == EXIT_FAILURES


def test_cli_fails_when_a_required_scenario_is_missing(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "only.yaml").write_text(
        "id: only\nscenario: normal_booking\nscript:\n  - caller: hi\n    reply: hello\n",
        encoding="utf-8",
    )
    code = main(["--cases", str(cases), "--out", str(tmp_path / "out"), "--require-coverage"])
    assert code == EXIT_SAFETY

"""Suite reporting: metrics, JSON, HTML, and regression comparison.

A regression is judged per-case, not on the headline pass rate. A suite
can add passing cases and raise its average while quietly breaking a
safety property, so :func:`compare_runs` reports cases that went from
pass to fail and flags safety ones separately — those fail CI on their
own.
"""

import html
import json
from dataclasses import dataclass, field
from typing import Any

from ai_evals.assertions import percentile
from ai_evals.runner import CaseResult


@dataclass
class EvalReport:
    """Aggregate metrics for one suite run."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    safety_failures: int = 0
    bookings_expected: int = 0
    bookings_succeeded: int = 0
    contained: int = 0
    escalations_expected: int = 0
    false_escalations: int = 0
    missed_escalations: int = 0
    hallucinated_prices: int = 0
    premature_confirmations: int = 0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    estimated_cost_cents: int = 0
    case_status: dict[str, bool] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def booking_success_rate(self) -> float | None:
        if not self.bookings_expected:
            return None
        return self.bookings_succeeded / self.bookings_expected

    @property
    def containment_rate(self) -> float | None:
        return self.contained / self.total if self.total else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "safety_failures": self.safety_failures,
            "task_completion": round(self.pass_rate, 4),
            "booking_success_rate": (
                round(self.booking_success_rate, 4)
                if self.booking_success_rate is not None
                else None
            ),
            "containment_rate": (
                round(self.containment_rate, 4) if self.containment_rate is not None else None
            ),
            "false_escalations": self.false_escalations,
            "missed_escalations": self.missed_escalations,
            "hallucinated_prices": self.hallucinated_prices,
            "premature_confirmations": self.premature_confirmations,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "estimated_cost_cents": self.estimated_cost_cents,
            "case_status": self.case_status,
            "failures": self.failures,
        }


def build_report(results: list[CaseResult]) -> EvalReport:
    report = EvalReport(total=len(results))
    latencies: list[int] = []

    for result in results:
        report.case_status[result.case_id] = result.passed
        if result.passed:
            report.passed += 1
        else:
            report.failed += 1
            report.failures.append(
                {
                    "case_id": result.case_id,
                    "scenario": result.scenario,
                    "safety_critical": result.safety_critical,
                    "checks": [
                        {"check": f.check, "detail": f.detail, "safety": f.safety}
                        for f in result.failures
                    ],
                }
            )
        report.safety_failures += len(result.safety_failures)
        latencies.extend(result.run.latencies)
        report.estimated_cost_cents += result.run.estimated_cost_cents

        for finding in result.findings:
            if finding.check == "no_unapproved_price" and not finding.passed:
                report.hallucinated_prices += 1
            if finding.check == "no_premature_confirmation" and not finding.passed:
                report.premature_confirmations += 1
            if finding.check == "escalation_occurred" and not finding.passed:
                # "expected escalated=False" failing means we escalated when
                # we should not have; the inverse is a missed escalation.
                if "expected escalated=False" in finding.detail:
                    report.false_escalations += 1
                else:
                    report.missed_escalations += 1

        booking_check = next((f for f in result.findings if f.check == "booking_created"), None)
        if booking_check is not None and result.run.booking_created:
            report.bookings_succeeded += 1
        if "expected created=True" in (booking_check.detail if booking_check else "") or (
            result.run.booking_created
        ):
            report.bookings_expected += 1

        if result.run.outcome in ("booked", "message_taken", "answered_inquiry"):
            report.contained += 1

    report.latency_p50_ms = percentile(latencies, 0.5)
    report.latency_p95_ms = percentile(latencies, 0.95)
    return report


def render_json(report: EvalReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


@dataclass
class Regression:
    """What changed against the previous run."""

    newly_failing: list[str] = field(default_factory=list)
    newly_passing: list[str] = field(default_factory=list)
    safety_regressions: list[str] = field(default_factory=list)
    pass_rate_delta: float = 0.0

    @property
    def has_safety_regression(self) -> bool:
        return bool(self.safety_regressions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "newly_failing": self.newly_failing,
            "newly_passing": self.newly_passing,
            "safety_regressions": self.safety_regressions,
            "pass_rate_delta": round(self.pass_rate_delta, 4),
            "has_safety_regression": self.has_safety_regression,
        }


def compare_runs(
    current: EvalReport, previous: dict[str, Any] | None, results: list[CaseResult]
) -> Regression:
    """Per-case comparison against a previous run's JSON report."""
    if not previous:
        return Regression(pass_rate_delta=0.0)

    before: dict[str, bool] = previous.get("case_status", {})
    regression = Regression(
        pass_rate_delta=current.pass_rate - float(previous.get("pass_rate", 0.0))
    )
    safety_by_case = {r.case_id: r for r in results}

    for case_id, now_passing in current.case_status.items():
        was_passing = before.get(case_id)
        if was_passing is None:
            continue  # a new case is not a regression
        if was_passing and not now_passing:
            regression.newly_failing.append(case_id)
            result = safety_by_case.get(case_id)
            if result is not None and (result.safety_critical or result.safety_failures):
                regression.safety_regressions.append(case_id)
        elif not was_passing and now_passing:
            regression.newly_passing.append(case_id)

    regression.newly_failing.sort()
    regression.newly_passing.sort()
    regression.safety_regressions.sort()
    return regression


def _metric_row(label: str, value: object) -> str:
    return (
        f"<tr><th scope='row'>{html.escape(label)}</th>"
        f"<td>{html.escape('—' if value is None else str(value))}</td></tr>"
    )


def render_html(report: EvalReport, regression: Regression | None = None) -> str:
    """Self-contained HTML report — no external assets."""
    rate = f"{report.pass_rate * 100:.1f}%"
    booking = (
        f"{report.booking_success_rate * 100:.1f}%"
        if report.booking_success_rate is not None
        else None
    )
    containment = (
        f"{report.containment_rate * 100:.1f}%" if report.containment_rate is not None else None
    )
    tone = "#dc2626" if report.safety_failures else "#059669" if report.failed == 0 else "#d97706"

    rows = "".join(
        [
            _metric_row("Cases", report.total),
            _metric_row("Passed", report.passed),
            _metric_row("Failed", report.failed),
            _metric_row("Pass rate", rate),
            _metric_row("Safety failures", report.safety_failures),
            _metric_row("Booking success", booking),
            _metric_row("Containment", containment),
            _metric_row("False escalations", report.false_escalations),
            _metric_row("Missed escalations", report.missed_escalations),
            _metric_row("Hallucinated prices", report.hallucinated_prices),
            _metric_row("Premature confirmations", report.premature_confirmations),
            _metric_row(
                "p50 latency",
                f"{report.latency_p50_ms:.0f} ms" if report.latency_p50_ms else None,
            ),
            _metric_row(
                "p95 latency",
                f"{report.latency_p95_ms:.0f} ms" if report.latency_p95_ms else None,
            ),
            _metric_row("Estimated cost", f"${report.estimated_cost_cents / 100:.2f}"),
        ]
    )

    failures = "".join(
        "<li><strong>{case}</strong> <em>({scenario})</em><ul>{checks}</ul></li>".format(
            case=html.escape(failure["case_id"]),
            scenario=html.escape(failure["scenario"]),
            checks="".join(
                "<li>{safety}{check}{detail}</li>".format(
                    safety="⚠ " if check["safety"] else "",
                    check=html.escape(check["check"]),
                    detail=f" — {html.escape(check['detail'])}" if check["detail"] else "",
                )
                for check in failure["checks"]
            ),
        )
        for failure in report.failures
    )
    failure_block = (
        f"<h2>Failures</h2><ul class='failures'>{failures}</ul>"
        if failures
        else "<h2>Failures</h2><p>None.</p>"
    )

    regression_block = ""
    if regression is not None:
        items = "".join(
            f"<li>{html.escape(label)}: {html.escape(', '.join(values) or 'none')}</li>"
            for label, values in (
                ("Newly failing", regression.newly_failing),
                ("Newly passing", regression.newly_passing),
                ("Safety regressions", regression.safety_regressions),
            )
        )
        regression_block = (
            "<h2>Regression vs previous run</h2>"
            f"<p>Pass-rate change: {regression.pass_rate_delta * 100:+.1f} points</p>"
            f"<ul>{items}</ul>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Receptionist evaluation report</title>
<style>
 body {{ font-family: system-ui, sans-serif; line-height: 1.5; margin: 2rem auto;
        max-width: 52rem; padding: 0 1rem; color: #0f172a; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #e2e8f0; }}
 th[scope=row] {{ font-weight: 500; color: #475569; }}
 td {{ font-variant-numeric: tabular-nums; }}
 .headline {{ font-size: 2rem; font-weight: 600; color: {tone}; }}
 .failures li {{ margin-bottom: .4rem; }}
 code {{ background: #f1f5f9; padding: .1rem .3rem; border-radius: 3px; }}
</style></head><body>
<h1>Receptionist evaluation report</h1>
<p class="headline">{rate} passing</p>
<table><tbody>{rows}</tbody></table>
{failure_block}
{regression_block}
</body></html>"""

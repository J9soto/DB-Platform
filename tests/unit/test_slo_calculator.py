import unittest

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.slo.calculator import (
    FAST_BURN_THRESHOLD,
    SLOW_BURN_THRESHOLD,
    MetricsSnapshot,
    assess_slo,
    budget_consumed_fraction,
    budget_remaining_fraction,
    burn_rate,
    classify_burn_rate,
    error_budget,
    sli_from_good_total,
)


def make_request(**spec_overrides) -> DatabaseRequest:
    doc = {
        "metadata": {
            "name": "orders-api",
            "environment": "prod",
            "owner": "payments-team",
            "cost_center": "CC-4471",
            "data_classification": "confidential",
        },
        "spec": {
            "platform": "aws",
            "slo": {
                "availability_target": 0.999,
                "latency_p99_ms": 200,
                "backup_rpo_hours": 24,
                "recovery_rto_hours": 4,
            },
            **spec_overrides,
        },
    }
    return DatabaseRequest.model_validate(doc)


class TestSliFromGoodTotal(unittest.TestCase):
    def test_basic_ratio(self):
        self.assertAlmostEqual(sli_from_good_total(999, 1000), 0.999)

    def test_all_good(self):
        self.assertEqual(sli_from_good_total(100, 100), 1.0)

    def test_zero_total_raises(self):
        with self.assertRaises(ValueError):
            sli_from_good_total(0, 0)

    def test_good_greater_than_total_raises(self):
        with self.assertRaises(ValueError):
            sli_from_good_total(101, 100)


class TestErrorBudget(unittest.TestCase):
    def test_three_nines(self):
        self.assertAlmostEqual(error_budget(0.999), 0.001)

    def test_perfect_slo_has_zero_budget(self):
        self.assertAlmostEqual(error_budget(1.0), 0.0)

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            error_budget(0.0)
        with self.assertRaises(ValueError):
            error_budget(1.5)


class TestBudgetFractions(unittest.TestCase):
    def test_sli_exactly_at_target_consumes_full_budget(self):
        # SLI == target means every "allowed" bad event happened -- budget
        # is exactly, not partially, consumed.
        self.assertAlmostEqual(budget_consumed_fraction(0.999, 0.999), 1.0)
        self.assertAlmostEqual(budget_remaining_fraction(0.999, 0.999), 0.0)

    def test_perfect_sli_consumes_no_budget(self):
        self.assertAlmostEqual(budget_consumed_fraction(1.0, 0.999), 0.0)
        self.assertAlmostEqual(budget_remaining_fraction(1.0, 0.999), 1.0)

    def test_sli_worse_than_target_over_consumes(self):
        # Half of all requests failing against a three-nines target blows
        # the budget many times over.
        consumed = budget_consumed_fraction(0.5, 0.999)
        self.assertGreater(consumed, 1.0)
        self.assertLess(budget_remaining_fraction(0.5, 0.999), 0.0)


class TestBurnRate(unittest.TestCase):
    def test_on_track_burn_rate_is_one(self):
        # If the bad-event rate over the *whole* period exactly matches
        # the budget, burn rate is exactly 1.0 (window_days == period_days).
        target = 0.99
        sli = 1.0 - error_budget(target)  # SLI exactly at budget boundary
        rate = burn_rate(sli, target, window_days=30, period_days=30)
        self.assertAlmostEqual(rate, 1.0, places=6)

    def test_short_window_amplifies_burn_rate(self):
        # The same bad fraction observed over a much shorter window implies
        # a proportionally higher burn rate (it's extrapolated to the
        # full period).
        target = 0.999
        sli = 0.99  # 1% bad, well beyond the 0.1% budget
        rate_1h_as_days = burn_rate(sli, target, window_days=1 / 24, period_days=30)
        rate_1d = burn_rate(sli, target, window_days=1, period_days=30)
        self.assertGreater(rate_1h_as_days, rate_1d)

    def test_healthy_sli_has_low_burn_rate(self):
        rate = burn_rate(0.9999, 0.999, window_days=1, period_days=30)
        self.assertLess(rate, SLOW_BURN_THRESHOLD)

    def test_invalid_window_raises(self):
        with self.assertRaises(ValueError):
            burn_rate(0.9, 0.999, window_days=0)


class TestClassifyBurnRate(unittest.TestCase):
    def test_fast_burn_pages(self):
        self.assertEqual(classify_burn_rate(FAST_BURN_THRESHOLD), "page")
        self.assertEqual(classify_burn_rate(FAST_BURN_THRESHOLD + 1), "page")

    def test_slow_burn_tickets(self):
        self.assertEqual(classify_burn_rate(SLOW_BURN_THRESHOLD), "ticket")
        self.assertEqual(classify_burn_rate(FAST_BURN_THRESHOLD - 0.01), "ticket")

    def test_low_burn_is_ok(self):
        self.assertEqual(classify_burn_rate(0.1), "ok")
        self.assertEqual(classify_burn_rate(0.0), "ok")


class TestAssessSlo(unittest.TestCase):
    def test_no_metrics_assesses_nothing(self):
        report = assess_slo(make_request(), MetricsSnapshot())
        self.assertEqual(report.statuses, [])
        self.assertEqual(set(report.not_assessed), {"availability", "latency", "backup", "recovery"})
        self.assertTrue(report.healthy)  # nothing assessed => nothing unhealthy

    def test_healthy_request_reports_healthy(self):
        metrics = MetricsSnapshot(
            window_days=1.0,
            good_requests=9999,
            total_requests=10000,  # 99.99% >> 99.9% target
            good_latency_requests=9999,
            total_latency_requests=10000,
            hours_since_last_backup=2.0,  # well within 24h RPO
            hours_since_last_recovery_test=48.0,  # well within review interval
        )
        report = assess_slo(make_request(), metrics)
        self.assertEqual(len(report.statuses), 4)
        self.assertEqual(report.not_assessed, [])
        self.assertTrue(report.healthy)
        self.assertEqual(report.full_name, "orders-api-prod")
        self.assertEqual(report.environment, "prod")

    def test_availability_violation_is_unhealthy_and_pages(self):
        metrics = MetricsSnapshot(
            window_days=1.0,
            good_requests=900,
            total_requests=1000,  # 90% vs 99.9% target -- way over budget
        )
        report = assess_slo(make_request(), metrics)
        availability = next(s for s in report.statuses if s.name == "availability")
        self.assertEqual(availability.severity, "page")
        self.assertFalse(report.healthy)
        self.assertEqual(report.worst_severity, "page")

    def test_backup_within_rpo_is_healthy(self):
        metrics = MetricsSnapshot(hours_since_last_backup=1.0)
        report = assess_slo(make_request(), metrics)
        backup = next(s for s in report.statuses if s.name == "backup")
        self.assertEqual(backup.severity, "ok")

    def test_backup_past_rpo_pages(self):
        metrics = MetricsSnapshot(hours_since_last_backup=30.0)  # RPO target is 24h
        report = assess_slo(make_request(), metrics)
        backup = next(s for s in report.statuses if s.name == "backup")
        self.assertEqual(backup.severity, "page")

    def test_recovery_drill_overdue_tickets(self):
        metrics = MetricsSnapshot(hours_since_last_recovery_test=10_000.0)
        report = assess_slo(make_request(), metrics)
        recovery = next(s for s in report.statuses if s.name == "recovery")
        self.assertEqual(recovery.severity, "ticket")

    def test_format_report_mentions_not_assessed_pillars(self):
        metrics = MetricsSnapshot(hours_since_last_backup=1.0)
        report = assess_slo(make_request(), metrics)
        text = report.format_report()
        self.assertIn("Not assessed", text)
        self.assertIn("availability", text)
        self.assertIn("recovery", text)


if __name__ == "__main__":
    unittest.main()

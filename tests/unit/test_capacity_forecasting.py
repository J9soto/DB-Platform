import tempfile
import unittest
from pathlib import Path

from dbre_platform.capacity.forecasting import (
    CapacityDataPoint,
    classify_risk,
    forecast_capacity,
    linear_regression,
    load_history_csv,
)
from dbre_platform.exceptions import CapacityDataError


def linear_series(start: float, slope: float, days: int) -> list[CapacityDataPoint]:
    return [CapacityDataPoint(day_offset=float(d), value=start + slope * d) for d in range(days)]


class TestLinearRegression(unittest.TestCase):
    def test_perfect_line_recovers_exact_slope_and_intercept(self):
        points = linear_series(start=100.0, slope=2.5, days=10)
        slope, intercept = linear_regression(points)
        self.assertAlmostEqual(slope, 2.5, places=6)
        self.assertAlmostEqual(intercept, 100.0, places=6)

    def test_flat_series_has_zero_slope(self):
        points = [CapacityDataPoint(day_offset=float(d), value=50.0) for d in range(5)]
        slope, _ = linear_regression(points)
        self.assertAlmostEqual(slope, 0.0)

    def test_fewer_than_two_points_raises(self):
        with self.assertRaises(CapacityDataError):
            linear_regression([CapacityDataPoint(0.0, 10.0)])
        with self.assertRaises(CapacityDataError):
            linear_regression([])

    def test_identical_day_offsets_raise(self):
        points = [CapacityDataPoint(day_offset=5.0, value=v) for v in (10.0, 20.0, 30.0)]
        with self.assertRaises(CapacityDataError):
            linear_regression(points)


class TestClassifyRisk(unittest.TestCase):
    def test_high_utilization_is_always_critical(self):
        self.assertEqual(classify_risk(None, utilization_fraction=0.95), "critical")

    def test_elevated_utilization_with_no_growth_is_warning(self):
        self.assertEqual(classify_risk(None, utilization_fraction=0.80), "warning")

    def test_soon_exhaustion_is_critical(self):
        self.assertEqual(classify_risk(10.0, utilization_fraction=0.5), "critical")

    def test_distant_exhaustion_is_warning(self):
        self.assertEqual(classify_risk(60.0, utilization_fraction=0.5), "warning")

    def test_far_future_exhaustion_is_ok(self):
        self.assertEqual(classify_risk(400.0, utilization_fraction=0.3), "ok")

    def test_no_growth_and_low_utilization_is_ok(self):
        self.assertEqual(classify_risk(None, utilization_fraction=0.2), "ok")


class TestForecastCapacity(unittest.TestCase):
    def test_growing_series_projects_exhaustion(self):
        history = linear_series(start=100.0, slope=5.0, days=10)  # last value = 145 at day 9
        forecast = forecast_capacity(history, capacity_limit=200.0)
        self.assertAlmostEqual(forecast.growth_rate_per_day, 5.0, places=4)
        self.assertAlmostEqual(forecast.current_value, 145.0, places=4)
        self.assertIsNotNone(forecast.projected_exhaustion_days)
        # (200 - 145) / 5 = 11 days from the latest sample
        self.assertAlmostEqual(forecast.projected_exhaustion_days, 11.0, places=4)
        self.assertTrue(forecast.is_growing)

    def test_flat_series_has_no_projected_exhaustion(self):
        history = [CapacityDataPoint(day_offset=float(d), value=50.0) for d in range(5)]
        forecast = forecast_capacity(history, capacity_limit=200.0)
        self.assertIsNone(forecast.projected_exhaustion_days)
        self.assertEqual(forecast.risk_level, "ok")

    def test_shrinking_series_has_no_projected_exhaustion(self):
        history = linear_series(start=100.0, slope=-2.0, days=10)
        forecast = forecast_capacity(history, capacity_limit=200.0)
        self.assertIsNone(forecast.projected_exhaustion_days)
        self.assertFalse(forecast.is_growing)

    def test_invalid_capacity_limit_raises(self):
        history = linear_series(start=100.0, slope=1.0, days=5)
        with self.assertRaises(CapacityDataError):
            forecast_capacity(history, capacity_limit=0)

    def test_unsorted_history_is_sorted_before_fitting(self):
        history = list(reversed(linear_series(start=100.0, slope=5.0, days=10)))
        forecast = forecast_capacity(history, capacity_limit=200.0)
        self.assertAlmostEqual(forecast.current_value, 145.0, places=4)


class TestLoadHistoryCsv(unittest.TestCase):
    def test_loads_and_converts_dates_to_day_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            path.write_text("date,value\n2026-01-01,100\n2026-01-11,150\n2026-01-06,125\n")
            points = load_history_csv(path)
            self.assertEqual([p.day_offset for p in points], [0.0, 5.0, 10.0])
            self.assertEqual([p.value for p in points], [100.0, 125.0, 150.0])

    def test_missing_file_raises(self):
        with self.assertRaises(CapacityDataError):
            load_history_csv("/nonexistent/path/history.csv")

    def test_missing_columns_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("day,amount\n1,2\n")
            with self.assertRaises(CapacityDataError):
                load_history_csv(path)

    def test_malformed_row_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("date,value\n2026-01-01,not-a-number\n")
            with self.assertRaises(CapacityDataError):
                load_history_csv(path)

    def test_shipped_example_fixture_loads_and_forecasts(self):
        repo_root = Path(__file__).resolve().parents[2]
        example = repo_root / "examples" / "capacity" / "history-sample.csv"
        points = load_history_csv(example)
        self.assertGreaterEqual(len(points), 2)
        forecast = forecast_capacity(points, capacity_limit=500.0, resource="storage", unit="GB")
        self.assertGreater(forecast.current_value, 0)


if __name__ == "__main__":
    unittest.main()

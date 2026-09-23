import unittest
import pandas as pd
from app.core.forecasting.stl import _forecast_window


class ForecastWindowTests(unittest.TestCase):
    def check_window(self, first, last, start, days, requested=365):
        self.assertEqual(
            _forecast_window(pd.date_range(first, last), requested),
            (pd.Timestamp(start), days),
        )

    def test_day_and_week_boundaries(self):
        for count in (1, 6, 7, 27):
            with self.subTest(count=count):
                last = pd.Timestamp("2026-08-01") + pd.Timedelta(days=count - 1)
                self.check_window("2026-08-01", last, last + pd.Timedelta(days=1),
                                  1 if count < 7 else 7)

    def test_partial_month_boundaries(self):
        for count in (28, 29, 30):
            with self.subTest(count=count):
                last = pd.Timestamp("2026-07-20") + pd.Timedelta(days=count - 1)
                self.check_window("2026-07-20", last, "2026-09-01", 30)

    def test_calendar_months(self):
        for first, last, start, days in [
            ("2026-08-01", "2026-08-31", "2026-09-01", 30),
            ("2026-01-01", "2026-01-31", "2026-02-01", 28),
            ("2024-01-01", "2024-01-31", "2024-02-01", 29),
            ("2026-12-01", "2026-12-31", "2027-01-01", 31),
            ("2026-01-01", "2026-11-30", "2026-12-01", 31),
        ]:
            with self.subTest(first=first, last=last):
                self.check_window(first, last, start, days)

    def test_year_threshold_and_leap_years(self):
        for first, last, start, days in [
            ("2026-01-01", "2026-12-30", "2027-01-01", 31),
            ("2026-01-01", "2026-12-31", "2027-01-01", 365),
            ("2023-01-01", "2023-12-31", "2024-01-01", 366),
            ("2024-01-01", "2024-12-30", "2025-01-01", 31),
            ("2024-01-01", "2024-12-31", "2025-01-01", 365),
            ("2025-08-15", "2026-08-14", "2027-01-01", 365),
            ("2024-01-01", "2026-08-31", "2027-01-01", 365),
        ]:
            with self.subTest(first=first, last=last):
                self.check_window(first, last, start, days)

    def test_missing_call_days_use_date_span(self):
        days = pd.date_range("2026-08-01", "2026-08-31").delete([4, 8, 12])
        self.assertEqual(_forecast_window(days, 365), (pd.Timestamp("2026-09-01"), 30))

    def test_explicit_api_horizon(self):
        self.check_window("2026-08-01", "2026-08-31", "2026-09-01", 14, requested=14)

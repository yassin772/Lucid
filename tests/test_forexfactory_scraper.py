from __future__ import annotations

import unittest

from modules.forexfactory_scraper import ForexFactoryScraper


class ForexFactoryScraperTests(unittest.TestCase):
    def test_parse_date_accepts_current_iso_datetime_with_timezone(self):
        self.assertEqual(
            ForexFactoryScraper._parse_date("2026-04-30T08:30:00-04:00"),
            "2026-04-30",
        )

    def test_parse_date_keeps_legacy_formats(self):
        self.assertEqual(ForexFactoryScraper._parse_date("04-30-2026"), "2026-04-30")
        self.assertEqual(ForexFactoryScraper._parse_date("2026-04-30"), "2026-04-30")
        self.assertEqual(ForexFactoryScraper._parse_date("30-04-2026"), "2026-04-30")

    def test_normalize_accepts_current_raw_forexfactory_shape(self):
        scraper = ForexFactoryScraper()
        raw = {
            "title": "Core PCE Price Index m/m",
            "country": "USD",
            "date": "2026-04-30T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.4%",
            "actual": "",
        }

        event = scraper._parse_raw(raw)
        self.assertIsNotNone(event)
        normalized = scraper._normalize(event, is_upcoming=True)

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.date, "2026-04-30")
        self.assertEqual(normalized.currency, "USD")
        self.assertEqual(normalized.event_type, "CPI")


if __name__ == "__main__":
    unittest.main()

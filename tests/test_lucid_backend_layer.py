from __future__ import annotations

import unittest
import json
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout

from modules.lucid_compliance import (
    ALLOWED_CONFIDENCE_LEVELS,
    ALLOWED_TIMEFRAMES,
    DISCLAIMER,
    assert_lucid_object_clean,
    find_banned_terms,
    clean_lucid_text,
)
from modules.lucid_event_engine import (
    EVENT_CONTENT,
    MAX_TODAY_EVENTS,
    MAX_WEEKLY_EVENTS,
    SPEAKER_CONTENT,
    LucidEventEngine,
)
from modules.lucid_macro_evolution_engine import (
    ALLOWED_MACRO_EVOLUTION_CONFIDENCE,
    ALLOWED_MACRO_EVOLUTION_STATES,
    build_macro_evolution,
)
from modules.lucid_macro_shock_engine import detect_macro_shock
from modules.lucid_narrative_orchestrator import build_narrative_focus
from modules.lucid_pair_narrative_engine import build_pair_narrative
from modules.lucid_price_alignment_engine import (
    ALLOWED_PRICE_ALIGNMENT_STATES,
    PRICE_ALIGNMENT_CAVEAT,
    build_price_alignment,
    build_price_alignments,
)
from modules.lucid_summary_engine import build_lucid_summaries
from scripts.export_lucid_payload import _load_raw_shocks, warn_if_macro_context_not_used
from scripts.fetch_fx_prices import (
    get_api_key,
    is_fresh_daily,
    normalize_twelvedata_pair,
)
from scripts.build_lucid_app_payload import build_app_payload
from scripts.export_lucid_payload import build_payload
from scripts.validate_lucid_payload import validate_payload


def _event(
    title: str = "US CPI",
    event_type: str = "CPI",
    currency: str = "USD",
    importance: str = "high",
    date: str | None = None,
):
    return SimpleNamespace(
        title=title,
        event_type=event_type,
        currency=currency,
        importance=importance,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d") if date is None else date,
    )


def _narrative(
    bias: str = "soutenu",
    tone: str = "hawkish",
    coherence: str = "forte",
):
    return SimpleNamespace(
        currency_bias=bias,
        dominant_tone=tone,
        coherence=coherence,
    )


def _sample_macro_headlines(name: str):
    data = json.loads(Path("data/sample_macro_headlines.json").read_text(encoding="utf-8"))
    return data["scenarios"][name]


def _summary_dict(
    currency: str,
    label: str = "Neutral",
    confidence: str = "Low",
    headline: str = "The market is waiting for clearer macro direction",
    reasons: list[str] | None = None,
):
    return {
        "currency": currency,
        "label": label,
        "confidence": confidence,
        "timeframe": "Mixed",
        "headline": headline,
        "reasons": reasons or ["The market is waiting for clearer data", "No single driver stands out"],
        "invalidation": "This view changes if the macro picture becomes clearer",
        "insight": headline,
        "key_event": None,
    }


def _pair_context_dict(
    pair: str = "EUR/USD",
    base_label: str = "Weak",
    quote_label: str = "Supported",
):
    base, quote = pair.split("/")
    return {
        "pair": pair,
        "base": base,
        "quote": quote,
        "base_label": base_label,
        "quote_label": quote_label,
    }


def _twelvedata_raw(closes: list[float], start_day: int = 1) -> dict:
    return {
        "values": [
            {
                "datetime": f"2026-05-{start_day + index:02d}",
                "close": str(close),
            }
            for index, close in enumerate(closes)
        ]
    }


class LucidSummaryEngineTests(unittest.TestCase):
    def test_empty_input_returns_eight_safe_summaries(self):
        summaries = build_lucid_summaries({}, [], None)

        self.assertEqual(len(summaries), 8)
        self.assertEqual(set(summaries), {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"})

        for summary in summaries.values():
            self.assertEqual(summary.label, "Neutral")
            self.assertIn(summary.confidence, ALLOWED_CONFIDENCE_LEVELS)
            self.assertIn(summary.timeframe, ALLOWED_TIMEFRAMES)
            self.assertTrue(summary.invalidation)
            self.assertGreaterEqual(len(summary.reasons), 2)
            self.assertLessEqual(len(summary.reasons), 3)
            assert_lucid_object_clean(summary)

    def test_label_mapping_stays_stable(self):
        summaries = build_lucid_summaries(
            {
                "USD": _narrative("soutenu", "hawkish"),
                "EUR": _narrative("fragile", "dovish"),
                "GBP": _narrative("neutre", "neutre"),
            },
            [],
            None,
        )

        self.assertEqual(summaries["USD"].label, "Supported")
        self.assertEqual(summaries["EUR"].label, "Weak")
        self.assertEqual(summaries["GBP"].label, "Neutral")
        self.assertEqual(summaries["USD"].confidence, "High")
        self.assertEqual(summaries["EUR"].confidence, "High")

    def test_contradiction_is_hidden_as_neutral(self):
        summaries = build_lucid_summaries(
            {"EUR": _narrative("fragile", "hawkish", "forte")},
            [],
            None,
        )

        self.assertEqual(summaries["EUR"].label, "Neutral")
        self.assertEqual(summaries["EUR"].confidence, "Low")
        self.assertEqual(summaries["EUR"].timeframe, "Mixed")
        self.assertIn("Mixed picture", summaries["EUR"].headline)
        assert_lucid_object_clean(summaries["EUR"])

    def test_disclaimer_is_available_for_api_consumers(self):
        self.assertEqual(
            DISCLAIMER,
            "Lucid is for macro understanding and education. It does not provide financial advice, investment recommendations, or trading signals.",
        )

    def test_product_guardrails_document_exists(self):
        guardrails = Path("docs/lucid_product_guardrails.md").read_text(encoding="utf-8")

        self.assertIn("Lucid explains; it does not recommend.", guardrails)
        self.assertIn("Macro backdrop is not immediate price direction.", guardrails)
        self.assertIn("Pair tension is not a trade bias.", guardrails)
        self.assertIn("Price alignment is not trade confirmation.", guardrails)
        self.assertIn("Macro pressure is not a panic alert.", guardrails)

    def test_compliance_blocks_signal_like_language(self):
        risky_copy = (
            "This is the best trade setup with price confirmation, strong edge, "
            "clear entry, target, stop loss and risk/reward."
        )

        banned = set(find_banned_terms(risky_copy))

        self.assertIn("best trade", banned)
        self.assertIn("setup", banned)
        self.assertIn("price confirmation", banned)
        self.assertIn("edge", banned)
        self.assertIn("entry", banned)
        self.assertIn("target", banned)
        self.assertIn("stop loss", banned)
        self.assertIn("risk/reward", banned)

    def test_compliance_rewrites_signal_like_language(self):
        clean = clean_lucid_text("USD dominates EUR with a confirmed trade setup.")

        self.assertNotIn("dominates", clean.lower())
        self.assertNotIn("confirmed trade", clean.lower())
        self.assertNotIn("setup", clean.lower())
        self.assertIn("firmer backdrop", clean.lower())

    def test_summary_outputs_are_clean_across_main_states(self):
        tones = [
            ("hawkish", "soutenu"),
            ("hawkish_modere", "soutenu"),
            ("neutre", "neutre"),
            ("dovish_modere", "fragile"),
            ("dovish", "fragile"),
        ]
        currencies = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]

        for risk_label in ("risk_on", "risk_off", "neutral"):
            risk = SimpleNamespace(label=risk_label)
            for tone, bias in tones:
                summaries = build_lucid_summaries(
                    {currency: _narrative(bias, tone) for currency in currencies},
                    [],
                    risk,
                )
                assert_lucid_object_clean(summaries)
                for summary in summaries.values():
                    self.assertIn(summary.confidence, ALLOWED_CONFIDENCE_LEVELS)
                    self.assertIn(summary.timeframe, ALLOWED_TIMEFRAMES)
                    self.assertTrue(summary.invalidation)

    def test_distinctive_drivers_reduce_rate_repetition(self):
        narratives = {
            "USD": _narrative("soutenu", "hawkish", "forte"),
            "EUR": _narrative("fragile", "dovish", "forte"),
            "AUD": _narrative("fragile", "dovish", "forte"),
            "NZD": _narrative("fragile", "dovish", "forte"),
            "CAD": _narrative("fragile", "dovish", "forte"),
        }

        summaries = build_lucid_summaries(narratives, [], SimpleNamespace(label="neutral"))
        headlines = [summary.headline.lower() for summary in summaries.values()]
        rate_headlines = [
            headline for headline in headlines
            if "rate" in headline or "fed" in headline or "ecb" in headline or "rba" in headline or "rbnz" in headline
        ]

        self.assertLessEqual(len(rate_headlines), 2)
        self.assertIn("growth", summaries["EUR"].headline.lower())
        self.assertTrue("china" in summaries["AUD"].headline.lower() or "commodity" in summaries["AUD"].headline.lower())
        self.assertIn("global demand", summaries["NZD"].headline.lower())
        self.assertIn("oil", summaries["CAD"].headline.lower())
        self.assertNotIn("RBA", summaries["AUD"].headline)
        self.assertNotIn("RBNZ", summaries["NZD"].headline)
        assert_lucid_object_clean(summaries)

    def test_jpy_uses_risk_mood_as_primary_driver(self):
        summaries = build_lucid_summaries(
            {"JPY": _narrative("fragile", "dovish", "moderee")},
            [],
            SimpleNamespace(label="neutral"),
        )

        self.assertIn("risk mood", summaries["JPY"].headline.lower())
        self.assertIn("risk sentiment", summaries["JPY"].reasons[0].lower())
        assert_lucid_object_clean(summaries["JPY"])

    def test_narrative_rotation_refreshes_static_usd_headline(self):
        summaries = build_lucid_summaries(
            {"USD": _narrative("soutenu", "hawkish", "forte")},
            [],
            SimpleNamespace(label="neutral"),
        )

        self.assertEqual(summaries["USD"].label, "Supported")
        self.assertNotEqual(summaries["USD"].headline, "The Fed is keeping rates high")
        self.assertTrue(
            "USD" in summaries["USD"].headline
            or "Fed" in summaries["USD"].headline
            or "Rate expectations" in summaries["USD"].headline
        )
        assert_lucid_object_clean(summaries["USD"])

    def test_narrative_rotation_uses_real_upcoming_event_only(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="US CPI",
                    event_type="CPI",
                    currency="USD",
                    importance="high",
                    date="2026-05-06",
                )
            ],
            today_str="2026-05-06",
        )
        summaries = build_lucid_summaries(
            {"USD": _narrative("soutenu", "hawkish", "forte")},
            events,
            SimpleNamespace(label="neutral"),
        )

        self.assertEqual(summaries["USD"].headline, "Inflation data is in focus for USD")
        self.assertIn("US CPI", summaries["USD"].key_event)
        assert_lucid_object_clean(summaries["USD"])

    def test_event_headline_uses_natural_macro_wording(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="BOE Gov Bailey Speaks",
                    event_type="BOE",
                    currency="GBP",
                    importance="high",
                    date="2026-05-06",
                )
            ],
            today_str="2026-05-06",
        )
        summaries = build_lucid_summaries(
            {"GBP": _narrative("soutenu", "hawkish", "forte")},
            events,
            SimpleNamespace(label="neutral"),
        )

        self.assertEqual(summaries["GBP"].headline, "BOE guidance is in focus for GBP")
        self.assertIn("BOE Gov Bailey Speaks", summaries["GBP"].key_event)
        self.assertNotIn("focused on BOE Gov Bailey Speaks", summaries["GBP"].headline)
        assert_lucid_object_clean(summaries["GBP"])

    def test_medium_events_do_not_take_over_all_currency_headlines(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(title="US ADP Employment Change", event_type="NFP", currency="USD", importance="medium", date="2026-05-06"),
                _event(title="ECB President Lagarde Speaks", event_type="ECB", currency="EUR", importance="medium", date="2026-05-06"),
                _event(title="BOC Gov Macklem Speaks", event_type="BOC", currency="CAD", importance="medium", date="2026-05-06"),
                _event(title="RBNZ Gov Breman Speaks", event_type="RBNZ", currency="NZD", importance="medium", date="2026-05-06"),
            ],
            today_str="2026-05-06",
        )
        summaries = build_lucid_summaries(
            {
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
                "CAD": _narrative("neutre", "neutre", "moderee"),
                "NZD": _narrative("fragile", "dovish", "forte"),
            },
            events,
            SimpleNamespace(label="neutral"),
        )

        event_headlines = [
            summary.headline
            for summary in summaries.values()
            if "in focus" in summary.headline or "next test" in summary.headline or "macro cue" in summary.headline
        ]
        self.assertLessEqual(len(event_headlines), 2)
        self.assertGreaterEqual(
            len([
                summary.headline
                for summary in (summaries["EUR"], summaries["CAD"], summaries["NZD"])
                if "in focus" not in summary.headline and "next test" not in summary.headline and "macro cue" not in summary.headline
            ]),
            2,
        )
        self.assertTrue(all(summary.key_event for summary in (summaries["USD"], summaries["EUR"], summaries["CAD"], summaries["NZD"])))
        assert_lucid_object_clean(summaries)


class LucidPayloadExportTests(unittest.TestCase):
    def test_payload_uses_macro_narratives_when_available(self):
        narratives = {
            "USD": _narrative("soutenu", "hawkish", "forte"),
            "EUR": _narrative("fragile", "dovish", "forte"),
        }

        payload = build_payload(
            raw_events=[],
            narratives=narratives,
            risk_environment=SimpleNamespace(label="neutral"),
        )

        self.assertEqual(payload["summary_mode"], "Narrative-derived")
        self.assertEqual(payload["lucid_summaries"]["USD"]["label"], "Supported")
        self.assertEqual(payload["lucid_summaries"]["EUR"]["label"], "Weak")
        self.assertIn(payload["lucid_summaries"]["USD"]["confidence"], ALLOWED_CONFIDENCE_LEVELS)
        self.assertIn(payload["lucid_summaries"]["USD"]["timeframe"], ALLOWED_TIMEFRAMES)
        self.assertTrue(payload["lucid_summaries"]["USD"]["invalidation"])
        self.assertEqual(len(payload["lucid_summaries"]), 8)
        self.assertIn("lucid_pairs", payload)
        self.assertTrue(any(item["pair"] == "EUR/USD" for item in payload["lucid_pairs"]))
        assert_lucid_object_clean(payload)

    def test_market_story_body_is_natural_and_not_product_explaining(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "GBP": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
                "JPY": _narrative("fragile", "dovish", "forte"),
                "CHF": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )

        body = payload["market_story"]["body"]
        self.assertEqual(body, "USD and GBP remain supported, while EUR, JPY, and CHF stay under pressure.")
        self.assertNotIn("look supported", body)
        self.assertNotIn("Lucid shows", body)

    def test_summary_payload_contains_no_trading_advice_terms(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )

        assert_lucid_object_clean(payload)

    def test_payload_pairs_are_context_only(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )

        self.assertEqual(len(payload["lucid_pairs"]), 20)
        eurusd = next(item for item in payload["lucid_pairs"] if item["pair"] == "EUR/USD")
        self.assertEqual(eurusd["base"], "EUR")
        self.assertEqual(eurusd["quote"], "USD")
        self.assertTrue(eurusd["takeaway"])
        assert_lucid_object_clean(payload["lucid_pairs"])

    def test_pair_narrative_is_relational(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
                "JPY": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )

        eurusd = next(item for item in payload["lucid_pairs"] if item["pair"] == "EUR/USD")
        narrative = eurusd["narrative"]
        self.assertEqual(narrative["pair"], "EUR/USD")
        self.assertEqual(narrative["dominant_currency"], "USD")
        self.assertTrue(narrative["tension_summary"])
        self.assertTrue(narrative["interaction_reason"])
        self.assertTrue(narrative["what_changes_this"])
        self.assertIn("theme", narrative)
        self.assertIn("interaction_type", narrative)
        self.assertIn("headline", narrative)
        self.assertIn("rationale", narrative)
        self.assertIn("tension_summary", eurusd)
        self.assertTrue(any(link in narrative["tension_summary"] for link in ("is set against", "sits against", "meets", "differs from")))
        self.assertNotIn(":", narrative["tension_summary"])
        assert_lucid_object_clean(narrative)

        usdjpy = next(item for item in payload["lucid_pairs"] if item["pair"] == "USD/JPY")
        self.assertTrue(any(link in usdjpy["narrative"]["tension_summary"] for link in ("is set against", "sits against", "meets", "differs from")))
        self.assertIn(usdjpy["narrative"]["interaction_type"], {"policy_vs_safe_haven", "risk_mood_vs_rates"})

    def test_pair_narrative_has_mixed_fallback(self):
        narrative = build_pair_narrative(
            "AUD/NZD",
            {
                "AUD": {"label": "Weak", "headline": "Softer global demand is pressuring AUD", "reasons": []},
                "NZD": {"label": "Weak", "headline": "Softer global demand is pressuring NZD", "reasons": []},
            },
        )

        self.assertIsNone(narrative["dominant_currency"])
        self.assertIn("AUD", narrative["tension_summary"])
        self.assertIn("NZD", narrative["tension_summary"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_policy_vs_growth(self):
        narrative = build_pair_narrative(
            "EUR/USD",
            {
                "EUR": _summary_dict("EUR", "Weak", "High", "Weak growth is weighing on EUR", ["Europe's economy lacks momentum"]),
                "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            },
        )

        self.assertEqual(narrative["interaction_type"], "policy_vs_growth")
        self.assertIn("Europe's softer growth picture", narrative["headline"])
        self.assertIn("US rate support", narrative["headline"])
        self.assertEqual(narrative["tension_summary"], narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_policy_vs_safe_haven(self):
        narrative = build_pair_narrative(
            "USD/JPY",
            {
                "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
                "JPY": _summary_dict("JPY", "Weak", "High", "Risk mood remains the main yen driver", ["The yen is sensitive to defensive market mood"]),
            },
        )

        self.assertEqual(narrative["interaction_type"], "policy_vs_safe_haven")
        self.assertIn("US rate support", narrative["headline"])
        self.assertIn("yen's sensitivity", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_global_demand_vs_policy(self):
        narrative = build_pair_narrative(
            "AUD/USD",
            {
                "AUD": _summary_dict("AUD", "Weak", "High", "China demand is pressuring AUD", ["Australia is sensitive to China and global commodity demand"]),
                "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            },
        )

        self.assertEqual(narrative["interaction_type"], "global_demand_vs_policy")
        self.assertIn("Global demand sensitivity in AUD", narrative["headline"])
        self.assertIn("US rate support", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_calendar_neutral_can_keep_relational_type(self):
        narrative = build_pair_narrative(
            "EUR/USD",
            {
                "EUR": _summary_dict("EUR", "Neutral", "Low", "No major event in focus", ["The calendar is quiet for this currency"]),
                "USD": _summary_dict("USD", "Neutral", "Medium", "Federal Reserve is in focus", ["Central bank language can change how investors read the economy"]),
            },
        )

        self.assertEqual(narrative["directional_state"], "Mixed")
        self.assertIsNone(narrative["dominant_currency"])
        self.assertEqual(narrative["interaction_type"], "policy_vs_growth")
        self.assertIn("US policy expectations", narrative["headline"])
        self.assertIn("Europe's growth picture", narrative["headline"])
        self.assertNotIn("softer US policy expectations", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_calendar_neutral_global_demand_vs_policy(self):
        narrative = build_pair_narrative(
            "AUD/USD",
            {
                "AUD": _summary_dict("AUD", "Neutral", "Low", "No major event in focus", ["The calendar is quiet for this currency"]),
                "USD": _summary_dict("USD", "Neutral", "Medium", "Federal Reserve is in focus", ["Central bank language can change how investors read the economy"]),
            },
        )

        self.assertEqual(narrative["directional_state"], "Mixed")
        self.assertIsNone(narrative["dominant_currency"])
        self.assertEqual(narrative["interaction_type"], "global_demand_vs_policy")
        self.assertIn("Global demand sensitivity in AUD", narrative["headline"])
        self.assertIn("US policy expectations", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_oil_vs_risk_mood(self):
        narrative = build_pair_narrative(
            "CAD/JPY",
            {
                "CAD": _summary_dict("CAD", "Supported", "High", "Oil prices are supporting CAD", ["Canada is sensitive to oil prices"]),
                "JPY": _summary_dict("JPY", "Weak", "High", "Risk mood remains the main yen driver", ["The yen is sensitive to defensive market mood"]),
            },
        )

        self.assertIn(narrative["interaction_type"], {"oil_vs_risk_mood", "commodity_vs_safe_haven"})
        self.assertIn("CAD", narrative["headline"])
        self.assertIn("yen", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_fixes_uk_policy_wording(self):
        narrative = build_pair_narrative(
            "GBP/JPY",
            {
                "GBP": _summary_dict("GBP", "Supported", "High", "BOE policy remains restrictive", ["The BOE is keeping rates high"]),
                "JPY": _summary_dict("JPY", "Weak", "High", "Risk mood remains the main yen driver", ["The yen is sensitive to defensive market mood"]),
            },
        )

        self.assertIn("UK policy expectations", narrative["headline"])
        self.assertNotIn("the UK policy expectations", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_mixed_driver_pairs_can_avoid_generic_fallback(self):
        cases = [
            (
                "AUD/JPY",
                {
                    "AUD": _summary_dict("AUD", "Weak", "High", "China demand is pressuring AUD", ["Australia is sensitive to China and global demand"]),
                    "JPY": _summary_dict("JPY", "Weak", "High", "Risk mood remains the main yen driver", ["The yen is sensitive to defensive market mood"]),
                },
                "cyclical_vs_defensive",
            ),
            (
                "NZD/JPY",
                {
                    "NZD": _summary_dict("NZD", "Weak", "High", "Global demand is weighing on NZD", ["New Zealand is sensitive to global trade conditions"]),
                    "JPY": _summary_dict("JPY", "Weak", "High", "Risk mood remains the main yen driver", ["The yen is sensitive to defensive market mood"]),
                },
                "cyclical_vs_defensive",
            ),
            (
                "EUR/AUD",
                {
                    "EUR": _summary_dict("EUR", "Weak", "High", "Weak growth is weighing on EUR", ["Europe's economy lacks momentum"]),
                    "AUD": _summary_dict("AUD", "Weak", "High", "China demand is pressuring AUD", ["Australia is sensitive to China and global demand"]),
                },
                "growth_vs_global_demand",
            ),
        ]

        for pair, summaries, interaction_type in cases:
            narrative = build_pair_narrative(pair, summaries)
            self.assertEqual(narrative["interaction_type"], interaction_type)
            self.assertNotIn("not clean enough", narrative["headline"])
            assert_lucid_object_clean(narrative)

    def test_pair_narrative_contrast_wording_is_not_overused(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
                "GBP": _narrative("soutenu", "hawkish", "forte"),
                "JPY": _narrative("fragile", "dovish", "forte"),
                "AUD": _narrative("fragile", "dovish", "forte"),
                "NZD": _narrative("fragile", "dovish", "forte"),
                "CAD": _narrative("neutre", "neutre", "faible"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )
        headlines = [item["narrative"]["headline"] for item in payload["lucid_pairs"]]

        self.assertLessEqual(sum("contrasts with" in headline for headline in headlines), 2)

    def test_pair_narrative_is_deterministic(self):
        summaries = {
            "EUR": _summary_dict("EUR", "Weak", "High", "Weak growth is weighing on EUR", ["Europe's economy lacks momentum"]),
            "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
        }

        first = build_pair_narrative("EUR/USD", summaries)
        second = build_pair_narrative("EUR/USD", summaries)

        self.assertEqual(first, second)

    def test_pair_narrative_macro_pressure_transmission(self):
        shock = detect_macro_shock([
            {"title": "Oil supply risk rises after energy disruption", "source": "source-a"},
            {"title": "Crude oil jumps as supply concern grows", "source": "source-b"},
        ])
        narrative = build_pair_narrative(
            "CAD/JPY",
            {
                "CAD": _summary_dict("CAD", "Supported", "High", "Oil prices are supporting CAD", ["Canada is sensitive to oil prices"]),
                "JPY": _summary_dict("JPY", "Weak", "High", "Risk mood remains the main yen driver", ["The yen is sensitive to defensive market mood"]),
            },
            macro_pressure=shock,
        )

        self.assertEqual(narrative["interaction_type"], "macro_pressure_transmission")
        self.assertIn("macro pressure", narrative["headline"])
        assert_lucid_object_clean(narrative)

    def test_pair_narrative_avoids_signal_language(self):
        narrative = build_pair_narrative(
            "EUR/USD",
            {
                "EUR": _summary_dict("EUR", "Weak", "High", "Weak growth is weighing on EUR", ["Europe's economy lacks momentum"]),
                "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            },
        )

        joined = " ".join(str(value) for value in narrative.values() if isinstance(value, str)).lower()
        for term in ("buy", "sell", "setup", "signal", "opportunity", "confirmed", "dominates"):
            self.assertNotIn(term, joined)
        assert_lucid_object_clean(narrative)

    def test_macro_evolution_stable_when_nothing_clear_changes(self):
        evolution = build_macro_evolution(
            narrative_focus={
                "theme": "global_macro_backdrop",
                "focus_currency": None,
                "headline": "The market is waiting for clearer macro direction",
                "supporting_themes": [],
                "rationale": "No single theme is strong enough to stand out clearly.",
            },
            summaries={},
            lucid_events=[],
        )

        self.assertEqual(evolution["state"], "stable")
        self.assertEqual(evolution["summary"], "The macro regime is largely unchanged.")
        self.assertIn(evolution["state"], ALLOWED_MACRO_EVOLUTION_STATES)
        self.assertIn(evolution["confidence"], ALLOWED_MACRO_EVOLUTION_CONFIDENCE)
        assert_lucid_object_clean(evolution)

    def test_macro_evolution_event_test_ahead_for_relevant_event(self):
        event = _event(
            title="US CPI",
            event_type="CPI",
            currency="USD",
            importance="high",
            date="2026-05-20",
        )
        event.timing_label = "Tomorrow"
        evolution = build_macro_evolution(
            narrative_focus={
                "theme": "us_inflation",
                "focus_currency": "USD",
                "headline": "US inflation is the main macro focus",
                "supporting_themes": [],
                "rationale": "A high-impact US inflation event matters.",
            },
            summaries={"USD": _summary_dict("USD", "Supported", "High", "US inflation is in focus", ["Inflation remains important"])},
            lucid_events=[event],
        )

        self.assertEqual(evolution["state"], "event_test_ahead")
        self.assertIn("US CPI", evolution["summary"])
        self.assertIn("US inflation", evolution["summary"])
        assert_lucid_object_clean(evolution)

    def test_macro_evolution_pressure_emerging_for_reliable_macro_pressure(self):
        shock = detect_macro_shock([
            {"title": "Oil supply risk rises after energy disruption", "source": "source-a"},
            {"title": "Crude oil supply concern grows", "source": "source-b"},
        ])
        evolution = build_macro_evolution(
            narrative_focus={
                "theme": "oil_cad",
                "focus_currency": "CAD",
                "headline": "Oil sensitivity is keeping CAD in focus",
                "supporting_themes": [],
                "rationale": "CAD and oil sensitivity create a clear channel.",
            },
            macro_pressure=shock,
            summaries={"CAD": _summary_dict("CAD", "Supported", "High", "Oil prices are supporting CAD", ["Canada is sensitive to oil prices"])},
            lucid_events=[],
        )

        self.assertEqual(evolution["state"], "pressure_emerging")
        self.assertEqual(evolution["emerging_theme"], "macro_pressure")
        self.assertIn(evolution["confidence"], {"medium", "high"})
        assert_lucid_object_clean(evolution)

    def test_macro_evolution_focus_shifting_for_supported_secondary_theme(self):
        evolution = build_macro_evolution(
            narrative_focus={
                "theme": "us_policy",
                "focus_currency": "USD",
                "headline": "Fed policy remains the main macro anchor",
                "supporting_themes": ["china_global_demand", "risk_mood"],
                "rationale": "Policy expectations remain the strongest channel.",
            },
            summaries={
                "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
                "AUD": _summary_dict("AUD", "Weak", "High", "China demand is pressuring AUD", ["Australia is sensitive to China and global demand"]),
                "NZD": _summary_dict("NZD", "Weak", "High", "Global demand is weighing on NZD", ["New Zealand is sensitive to global demand"]),
            },
            lucid_events=[],
        )

        self.assertEqual(evolution["state"], "focus_shifting")
        self.assertEqual(evolution["primary_theme"], "us_policy")
        self.assertEqual(evolution["emerging_theme"], "china_global_demand")
        self.assertIn("US policy expectations remain the backdrop", evolution["summary"])
        self.assertNotIn("US policy expectations remains", evolution["summary"])
        self.assertNotIn("becoming", evolution["summary"].lower())
        assert_lucid_object_clean(evolution)

    def test_macro_evolution_is_deterministic_and_clean(self):
        kwargs = {
            "narrative_focus": {
                "theme": "us_policy",
                "focus_currency": "USD",
                "headline": "Fed policy remains the main macro anchor",
                "supporting_themes": ["china_global_demand"],
                "rationale": "Policy expectations remain the strongest channel.",
            },
            "summaries": {
                "AUD": _summary_dict("AUD", "Weak", "High", "China demand is pressuring AUD", ["Australia is sensitive to China and global demand"]),
            },
            "lucid_events": [],
        }

        first = build_macro_evolution(**kwargs)
        second = build_macro_evolution(**kwargs)

        self.assertEqual(first, second)
        self.assertFalse(find_banned_terms(json.dumps(first)))
        assert_lucid_object_clean(first)

    def test_payload_contains_valid_macro_evolution_with_and_without_previous_payload(self):
        narratives = {
            "USD": _narrative("soutenu", "hawkish", "forte"),
            "AUD": _narrative("fragile", "dovish", "forte"),
        }
        payload = build_payload(
            raw_events=[],
            narratives=narratives,
            risk_environment=SimpleNamespace(label="neutral"),
        )
        previous_payload = {
            "narrative_focus": {
                "theme": "europe_growth",
                "focus_currency": "EUR",
                "headline": "European growth is the clearest macro drag",
                "supporting_themes": [],
                "rationale": "The EUR backdrop points to growth.",
            }
        }
        payload_with_previous = build_payload(
            raw_events=[],
            narratives=narratives,
            risk_environment=SimpleNamespace(label="neutral"),
            previous_payload=previous_payload,
        )

        self.assertIn("macro_evolution", payload)
        self.assertIn("macro_evolution", payload_with_previous)
        validate_payload(payload)
        validate_payload(payload_with_previous)

    def test_payload_validator_accepts_valid_payload(self):
        payload = build_payload(raw_events=[])

        validate_payload(payload)

    def test_price_alignment_absent_without_price_data(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )

        self.assertNotIn("price_alignment", payload)
        validate_payload(payload)

    def test_price_alignment_states_are_controlled_and_clean(self):
        now = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
        fresh = "2026-05-17T17:00:00+00:00"
        cases = [
            (
                "Aligned",
                _pair_context_dict("EUR/USD", "Weak", "Supported"),
                {"pair": "EUR/USD", "recent_change_pct": -0.40, "price_updated_at": fresh},
            ),
            (
                "Mixed",
                _pair_context_dict("GBP/USD", "Supported", "Supported"),
                {"pair": "GBP/USD", "recent_change_pct": 0.02, "price_updated_at": fresh},
            ),
            (
                "Diverging",
                _pair_context_dict("AUD/USD", "Weak", "Supported"),
                {"pair": "AUD/USD", "recent_change_pct": 0.35, "price_updated_at": fresh},
            ),
            (
                "Transitioning",
                _pair_context_dict("USD/JPY", "Supported", "Weak"),
                {"pair": "USD/JPY", "recent_change_pct": 0.31, "previous_change_pct": -0.25, "price_updated_at": fresh},
            ),
        ]

        for expected, pair_context, price_item in cases:
            alignment = build_price_alignment(pair_context, price_item, now=now)
            self.assertIsNotNone(alignment)
            self.assertEqual(alignment["state"], expected)
            self.assertIn(alignment["state"], ALLOWED_PRICE_ALIGNMENT_STATES)
            self.assertEqual(alignment["caveat"], PRICE_ALIGNMENT_CAVEAT)
            self.assertFalse(find_banned_terms(json.dumps(alignment)))
            assert_lucid_object_clean(alignment)

    def test_price_alignment_ignores_missing_or_stale_data(self):
        now = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        pair_context = _pair_context_dict("EUR/USD", "Weak", "Supported")

        self.assertIsNone(build_price_alignment(pair_context, None, now=now))
        self.assertIsNone(build_price_alignment(pair_context, {"pair": "EUR/USD", "recent_change_pct": -0.4}, now=now))
        self.assertIsNone(build_price_alignment(
            pair_context,
            {"pair": "EUR/USD", "recent_change_pct": -0.4, "price_updated_at": "2026-05-18T17:00:00+00:00"},
            now=now,
        ))

    def test_price_alignment_allows_weekend_daily_tolerance(self):
        now = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
        pair_context = _pair_context_dict("EUR/USD", "Weak", "Supported")

        alignment = build_price_alignment(
            pair_context,
            {"pair": "EUR/USD", "recent_change_pct": -0.4, "price_updated_at": "2026-05-15T21:00:00+00:00"},
            now=now,
        )

        self.assertIsNotNone(alignment)

    def test_price_alignment_is_deterministic_and_payload_valid_when_present(self):
        now = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
        pair_contexts = [
            _pair_context_dict("EUR/USD", "Weak", "Supported"),
            _pair_context_dict("USD/JPY", "Supported", "Weak"),
        ]
        price_data = {
            "pairs": [
                {"pair": "EUR/USD", "recent_change_pct": -0.40, "price_updated_at": "2026-05-17T17:00:00+00:00"},
                {"pair": "USD/JPY", "recent_change_pct": 0.28, "price_updated_at": "2026-05-17T17:00:00+00:00"},
            ]
        }
        current_price_data = {
            "pairs": [
                {"pair": "EUR/USD", "recent_change_pct": -0.40, "price_updated_at": "2026-05-19T00:00:00+00:00"},
                {"pair": "USD/JPY", "recent_change_pct": 0.28, "price_updated_at": "2026-05-19T00:00:00+00:00"},
            ]
        }

        first = build_price_alignments(pair_contexts, price_data, now=now)
        second = build_price_alignments(pair_contexts, price_data, now=now)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"EUR/USD", "USD/JPY"})
        self.assertFalse(find_banned_terms(json.dumps(first)))

        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "EUR": _narrative("fragile", "dovish", "forte"),
                "JPY": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
            price_data=current_price_data,
        )
        self.assertIn("price_alignment", payload)
        validate_payload(payload)

    def test_twelvedata_normalization_builds_minimal_price_shape(self):
        now = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
        raw = _twelvedata_raw([1.10, 1.11, 1.12, 1.13, 1.12, 1.11, 1.10, 1.09, 1.08], start_day=9)

        item = normalize_twelvedata_pair("EUR/USD", raw, now=now)

        self.assertIsNotNone(item)
        self.assertEqual(item["pair"], "EUR/USD")
        self.assertEqual(item["bars_count"], 9)
        self.assertEqual(item["last_close"], 1.08)
        self.assertIn("recent_change_pct", item)
        self.assertIn("previous_change_pct", item)
        self.assertEqual(item["price_updated_at"], "2026-05-17T00:00:00Z")

    def test_twelvedata_normalization_skips_insufficient_or_stale_data(self):
        now = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)

        self.assertIsNone(normalize_twelvedata_pair(
            "EUR/USD",
            _twelvedata_raw([1.10, 1.11, 1.12]),
            now=now,
        ))
        self.assertIsNone(normalize_twelvedata_pair(
            "EUR/USD",
            _twelvedata_raw([1.10, 1.11, 1.12, 1.13, 1.12, 1.11, 1.10, 1.09], start_day=1),
            now=now,
        ))

    def test_twelvedata_daily_freshness_uses_weekend_tolerance(self):
        sunday = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
        friday = datetime(2026, 5, 15, 21, 0, tzinfo=timezone.utc)
        old_monday = datetime(2026, 5, 11, 21, 0, tzinfo=timezone.utc)

        self.assertTrue(is_fresh_daily(friday, sunday))
        self.assertFalse(is_fresh_daily(old_monday, sunday))

    def test_daily_future_candle_tolerance_is_limited(self):
        now = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
        pair_context = _pair_context_dict("EUR/USD", "Weak", "Supported")

        self.assertTrue(is_fresh_daily(now + timedelta(hours=12), now))
        self.assertFalse(is_fresh_daily(now + timedelta(hours=36), now))
        self.assertIsNotNone(build_price_alignment(
            pair_context,
            {"pair": "EUR/USD", "recent_change_pct": -0.4, "price_updated_at": (now + timedelta(hours=12)).isoformat()},
            now=now,
        ))
        self.assertIsNone(build_price_alignment(
            pair_context,
            {"pair": "EUR/USD", "recent_change_pct": -0.4, "price_updated_at": (now + timedelta(hours=36)).isoformat()},
            now=now,
        ))

    def test_twelvedata_missing_api_key_is_clear(self):
        with self.assertRaisesRegex(RuntimeError, "TWELVE_DATA_API_KEY"):
            get_api_key({})

    def test_single_macro_shock_headline_is_ignored(self):
        shock = detect_macro_shock([
            {"title": "Oil rises after geopolitical escalation", "source": "one-source"},
        ])

        self.assertIsNone(shock)

    def test_confirmed_macro_shock_builds_transmission_chain(self):
        shock = detect_macro_shock([
            {"title": "Iran escalation raises geopolitical risk", "source": "source-a"},
            {"title": "Military conflict lifts defensive market mood", "source": "source-b"},
        ])

        self.assertIsNotNone(shock)
        self.assertEqual(shock["shock_type"], "geopolitical_risk")
        self.assertIn("USD", shock["supports"])
        self.assertIn("AUD", shock["pressures"])
        self.assertGreaterEqual(shock["source_count"], 2)
        self.assertIn("Geopolitical risk", shock["transmission_chain"])
        assert_lucid_object_clean(shock)

    def test_payload_includes_macro_shock_only_when_confirmed(self):
        quiet_payload = build_payload(
            raw_events=[],
            macro_shock_items=[{"title": "Oil supply risk rises", "source": "single"}],
        )
        self.assertIsNone(quiet_payload["macro_shock"])
        validate_payload(quiet_payload)

        shock_payload = build_payload(
            raw_events=[],
            macro_shock_items=[
                {"title": "Oil supply risk rises after energy disruption", "source": "source-a"},
                {"title": "Crude oil jumps as supply concern grows", "source": "source-b"},
            ],
        )
        self.assertIsNotNone(shock_payload["macro_shock"])
        self.assertEqual(shock_payload["macro_shock"]["shock_type"], "oil_supply_shock")
        validate_payload(shock_payload)
        assert_lucid_object_clean(shock_payload)

    def test_narrative_orchestrator_is_deterministic(self):
        summaries = {
            "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            "EUR": _summary_dict("EUR", "Weak", "High", "European growth is weak", ["Europe's economy lacks momentum"]),
        }
        events = [_event(title="US CPI", event_type="CPI", currency="USD", importance="high")]

        first = build_narrative_focus(summaries, events, SimpleNamespace(label="neutral"), None)
        second = build_narrative_focus(summaries, events, SimpleNamespace(label="neutral"), None)

        self.assertEqual(first, second)
        assert_lucid_object_clean(first)

    def test_narrative_orchestrator_us_cpi_can_choose_us_inflation(self):
        summaries = {
            "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            "EUR": _summary_dict("EUR", "Weak", "High", "European growth is weak", ["Europe's economy lacks momentum"]),
        }

        focus = build_narrative_focus(
            summaries,
            [_event(title="Core CPI m/m", event_type="CPI", currency="USD", importance="high")],
            SimpleNamespace(label="neutral"),
            None,
        )

        self.assertEqual(focus["theme"], "us_inflation")
        self.assertEqual(focus["focus_currency"], "USD")
        self.assertIn("inflation", focus["headline"].lower())
        assert_lucid_object_clean(focus)

    def test_narrative_orchestrator_oil_pressure_can_lead(self):
        summaries = {
            "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            "CAD": _summary_dict("CAD", "Neutral", "Low", "Oil and US demand are driving CAD", ["Canada is sensitive to oil prices"]),
        }
        shock = detect_macro_shock([
            {"title": "Oil supply risk rises after energy disruption", "source": "source-a"},
            {"title": "Crude oil jumps as supply concern grows", "source": "source-b"},
        ])

        focus = build_narrative_focus(summaries, [], SimpleNamespace(label="neutral"), shock)

        self.assertIn(focus["theme"], {"oil_cad", "macro_pressure"})
        self.assertIn("oil_cad", focus["supporting_themes"] + [focus["theme"]])
        assert_lucid_object_clean(focus)

    def test_narrative_orchestrator_surfaces_china_global_demand(self):
        summaries = {
            "USD": _summary_dict("USD", "Supported", "High", "Fed policy remains restrictive", ["The Fed is keeping rates high"]),
            "AUD": _summary_dict("AUD", "Weak", "High", "China demand is pressuring AUD", ["Australia is sensitive to China and global commodity demand"]),
            "NZD": _summary_dict("NZD", "Weak", "High", "Global demand is weighing on NZD", ["New Zealand is sensitive to global trade conditions"]),
        }

        focus = build_narrative_focus(summaries, [], SimpleNamespace(label="neutral"), None)

        self.assertIn("china_global_demand", focus["supporting_themes"] + [focus["theme"]])
        self.assertIn(focus["focus_currency"], {"USD", "AUD", "NZD"})
        assert_lucid_object_clean(focus)

    def test_narrative_orchestrator_has_stable_fallback(self):
        summaries = {
            currency: _summary_dict(currency)
            for currency in ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
        }

        focus = build_narrative_focus(summaries, [], None, None)

        self.assertEqual(focus["theme"], "global_macro_backdrop")
        self.assertIsNone(focus["focus_currency"])
        self.assertTrue(focus["headline"])
        assert_lucid_object_clean(focus)

    def test_payload_includes_narrative_focus(self):
        payload = build_payload(
            raw_events=[],
            narratives={
                "USD": _narrative("soutenu", "hawkish", "forte"),
                "AUD": _narrative("fragile", "dovish", "forte"),
                "NZD": _narrative("fragile", "dovish", "forte"),
            },
            risk_environment=SimpleNamespace(label="neutral"),
        )

        focus = payload["narrative_focus"]
        self.assertIn(focus["theme"], {
            "us_policy",
            "us_inflation",
            "europe_growth",
            "uk_inflation",
            "china_global_demand",
            "oil_cad",
            "risk_mood",
            "safe_haven",
            "macro_pressure",
            "central_bank_guidance",
            "commodities",
            "global_macro_backdrop",
        })
        self.assertIn("headline", focus)
        self.assertIn("rationale", focus)
        validate_payload(payload)
        assert_lucid_object_clean(focus)

    def test_dev_macro_headline_fixture_covers_core_shocks(self):
        expected = {
            "geopolitical_risk_confirmed": "geopolitical_risk",
            "oil_supply_shock_confirmed": "oil_supply_shock",
            "trade_tension_confirmed": "trade_tension",
            "banking_stress_confirmed": "banking_stress",
        }

        for scenario, shock_type in expected.items():
            shock = detect_macro_shock(_sample_macro_headlines(scenario))
            self.assertIsNotNone(shock, scenario)
            self.assertEqual(shock["shock_type"], shock_type)
            self.assertGreaterEqual(shock["source_count"], 2)
            self.assertTrue(shock["transmission_chain"])
            assert_lucid_object_clean(shock)

    def test_dev_macro_headline_fixture_ignores_weak_or_unrelated_inputs(self):
        self.assertIsNone(detect_macro_shock(_sample_macro_headlines("single_headline_ignored")))
        self.assertIsNone(detect_macro_shock(_sample_macro_headlines("unrelated_headlines_ignored")))

    def test_dev_macro_headline_fixture_can_feed_export_cli_loader(self):
        items = _load_raw_shocks(["data/sample_macro_headlines.json"])

        self.assertGreaterEqual(len(items), 8)
        shock = detect_macro_shock(items)
        self.assertIsNotNone(shock)
        self.assertGreaterEqual(shock["source_count"], 2)

    def test_payload_validator_rejects_missing_currency(self):
        payload = build_payload(raw_events=[])
        payload["lucid_summaries"].pop("NZD")

        with self.assertRaises(ValueError):
            validate_payload(payload)

    def test_app_payload_builder_falls_back_to_calendar_mode(self):
        payload = build_app_payload(
            macro_context_path="/private/tmp/lucid_pycache/missing_macro_context.json",
            output_path="/private/tmp/lucid_pycache/test_lucid_payload_calendar.json",
            raw_json_paths=[],
        )

        self.assertEqual(payload["summary_mode"], "Calendar-derived")
        validate_payload(payload)

    def test_app_payload_builder_can_use_sample_narrative_mode(self):
        payload = build_app_payload(
            macro_context_path="/private/tmp/lucid_pycache/missing_macro_context.json",
            use_sample_context=True,
            output_path="/private/tmp/lucid_pycache/test_lucid_payload_narrative.json",
            raw_json_paths=[],
        )

        self.assertEqual(payload["summary_mode"], "Narrative-derived")
        validate_payload(payload)

    def test_app_payload_builder_uses_valid_macro_context(self):
        macro_context_path = Path("/private/tmp/lucid_pycache/test_valid_macro_context.json")
        macro_context_path.write_text(json.dumps({
            "risk_environment": {"label": "neutral"},
            "narratives": {
                "USD": {"currency_bias": "soutenu", "dominant_tone": "hawkish", "coherence": "forte"},
                "EUR": {"currency_bias": "fragile", "dominant_tone": "dovish", "coherence": "forte"},
                "GBP": {"currency_bias": "neutre", "dominant_tone": "neutre", "coherence": "faible"},
            },
        }), encoding="utf-8")

        payload = build_app_payload(
            macro_context_path=str(macro_context_path),
            output_path="/private/tmp/lucid_pycache/test_lucid_payload_valid_macro_context.json",
            raw_json_paths=[],
        )

        self.assertEqual(payload["summary_mode"], "Narrative-derived")
        self.assertEqual(payload["lucid_summaries"]["USD"]["label"], "Supported")
        self.assertEqual(payload["lucid_summaries"]["EUR"]["label"], "Weak")
        validate_payload(payload)

    def test_invalid_macro_context_falls_back_with_warning(self):
        macro_context_path = Path("/private/tmp/lucid_pycache/test_invalid_macro_context.json")
        macro_context_path.write_text("{not-json", encoding="utf-8")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            payload = build_app_payload(
                macro_context_path=str(macro_context_path),
                output_path="/private/tmp/lucid_pycache/test_lucid_payload_invalid_macro_context.json",
                raw_json_paths=[],
            )

        self.assertEqual(payload["summary_mode"], "Calendar-derived")
        self.assertIn("WARNING: macro context was provided or detected but the payload is Calendar-derived", buffer.getvalue())
        validate_payload(payload)

    def test_macro_context_warning_helper_is_clear(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            warn_if_macro_context_not_used("api/macro_context.json", {"summary_mode": "Calendar-derived"})

        self.assertIn("WARNING: macro context was provided or detected", buffer.getvalue())


class LucidEventEngineTests(unittest.TestCase):
    def test_event_content_library_is_clean(self):
        assert_lucid_object_clean(EVENT_CONTENT)
        assert_lucid_object_clean(SPEAKER_CONTENT)

    def test_filters_low_non_cb_event(self):
        events = LucidEventEngine().build_lucid_events([
            _event(title="Minor Housing Data", event_type="OTHER", importance="low"),
        ])

        self.assertEqual(events, [])

    def test_keeps_high_medium_and_cb_speech(self):
        events = LucidEventEngine().build_lucid_events([
            _event(title="US CPI", event_type="CPI", importance="high"),
            _event(title="UK GDP", event_type="GDP", currency="GBP", importance="medium"),
            _event(title="Powell Speaks", event_type="OTHER", importance="low"),
        ])

        self.assertEqual(len(events), 3)
        self.assertTrue(any(event.is_cb_speech for event in events))
        for event in events:
            assert_lucid_object_clean(event)

    def test_source_importance_is_preserved_when_available(self):
        events = LucidEventEngine().build_lucid_events([
            _event(title="US CPI", event_type="CPI", importance="high", date="2026-05-05"),
            _event(title="UK GDP", event_type="GDP", currency="GBP", importance="medium", date="2026-05-05"),
            _event(title="Powell Speaks", event_type="OTHER", importance="low", date="2026-05-05"),
        ], today_str="2026-05-05")

        importance_by_title = {event.title: event.importance for event in events}
        self.assertEqual(importance_by_title["US CPI"], "high")
        self.assertEqual(importance_by_title["UK GDP"], "medium")
        self.assertEqual(importance_by_title["Powell Speaks"], "low")

    def test_missing_importance_does_not_invent_high_impact(self):
        events = LucidEventEngine().build_lucid_events([
            _event(title="Powell Speaks", event_type="OTHER", importance="", date="2026-05-05"),
        ], today_str="2026-05-05")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].importance, "unknown")
        self.assertNotEqual(events[0].importance, "high")

    def test_unknown_high_event_uses_safe_fallback(self):
        events = LucidEventEngine().build_lucid_events([
            _event(title="", event_type="UNKNOWN_TYPE", currency="", importance="high", date=""),
        ])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].timing_label, "Upcoming event")
        self.assertFalse(events[0].is_today)
        self.assertTrue(events[0].why_it_matters)
        assert_lucid_object_clean(events[0])

    def test_cb_event_keeps_source_title_and_does_not_invent_rate_decision(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="RBNZ Gov Breman Speaks",
                    event_type="RBNZ",
                    currency="NZD",
                    importance="medium",
                    date="2026-05-05",
                )
            ],
            today_str="2026-05-05",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "RBNZ Gov Breman Speaks")
        self.assertNotIn("Rate Decision", events[0].title)
        self.assertTrue(events[0].is_cb_speech)
        assert_lucid_object_clean(events[0])

    def test_true_rate_decision_title_is_preserved_only_when_source_says_so(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="RBA Rate Statement",
                    event_type="RBA",
                    currency="AUD",
                    importance="high",
                    date="2026-05-05",
                )
            ],
            today_str="2026-05-05",
        )

        self.assertEqual(events[0].title, "RBA Rate Statement")
        self.assertNotEqual(events[0].title, "RBA Rate Decision")

    def test_today_limit_is_enforced(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_events = [
            _event(title=f"US CPI {i}", event_type="CPI", importance="high", date=today)
            for i in range(MAX_TODAY_EVENTS + 3)
        ]

        events = LucidEventEngine().build_lucid_events(raw_events)

        self.assertEqual(len(events), MAX_TODAY_EVENTS)
        self.assertTrue(all(event.is_today for event in events))

    def test_past_events_are_not_used_as_upcoming_events(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(title="US CPI Past", event_type="CPI", importance="high", date="2026-05-04"),
                _event(title="US CPI Future", event_type="CPI", importance="high", date="2026-05-06"),
            ],
            today_str="2026-05-05",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].date, "2026-05-06")
        self.assertEqual(events[0].timing_label, "Tomorrow")

    def test_today_can_be_supplied_by_caller(self):
        events = LucidEventEngine().build_lucid_events(
            [_event(title="US CPI", event_type="CPI", importance="high", date="2026-05-05")],
            today_str="2026-05-05",
        )

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].is_today)
        self.assertEqual(events[0].timing_label, "Today")

    def test_event_timing_labels_use_real_date_distance(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(title="US CPI Today", event_type="CPI", importance="high", date="2026-05-05"),
                _event(title="US CPI Tomorrow", event_type="CPI", importance="high", date="2026-05-06"),
                _event(title="US CPI Soon", event_type="CPI", importance="high", date="2026-05-07"),
                _event(title="US CPI Later", event_type="CPI", importance="high", date="2026-05-13"),
            ],
            today_str="2026-05-05",
        )

        labels = {event.date: event.timing_label for event in events}
        self.assertEqual(labels["2026-05-05"], "Today")
        self.assertEqual(labels["2026-05-06"], "Tomorrow")
        self.assertEqual(labels["2026-05-07"], "In 2 days")
        self.assertEqual(labels["2026-05-13"], "May 13")

    def test_key_event_ignores_past_events(self):
        today = datetime.now(timezone.utc)
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="ECB Past Decision",
                    event_type="ECB",
                    currency="EUR",
                    importance="high",
                    date=(today - timedelta(days=1)).strftime("%Y-%m-%d"),
                ),
                _event(
                    title="ECB Future Decision",
                    event_type="ECB",
                    currency="EUR",
                    importance="high",
                    date=(today + timedelta(days=2)).strftime("%Y-%m-%d"),
                ),
            ],
            today_str=today.strftime("%Y-%m-%d"),
        )
        summaries = build_lucid_summaries(
            {"EUR": _narrative("fragile", "dovish", "forte")},
            events,
            SimpleNamespace(label="neutral"),
        )

        self.assertIn("in 2 days", summaries["EUR"].key_event)
        self.assertNotIn("Past", summaries["EUR"].key_event)

    def test_high_impact_key_event_beats_medium_event_even_if_later(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="NZD Medium Speech",
                    event_type="OTHER",
                    currency="NZD",
                    importance="medium",
                    date="2026-05-06",
                ),
                _event(
                    title="NZD Employment Change",
                    event_type="NFP",
                    currency="NZD",
                    importance="high",
                    date="2026-05-08",
                ),
            ],
            today_str="2026-05-06",
        )
        summaries = build_lucid_summaries(
            {"NZD": _narrative("fragile", "dovish", "forte")},
            events,
            SimpleNamespace(label="neutral"),
        )

        self.assertIn("NZD Employment Change", summaries["NZD"].key_event)
        self.assertIn("High impact", summaries["NZD"].key_event)

    def test_key_event_uses_source_event_title(self):
        events = LucidEventEngine().build_lucid_events(
            [
                _event(
                    title="RBNZ Gov Breman Speaks",
                    event_type="RBNZ",
                    currency="NZD",
                    importance="medium",
                    date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                )
            ],
            today_str=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        summaries = build_lucid_summaries(
            {"NZD": _narrative("fragile", "dovish", "forte")},
            events,
            SimpleNamespace(label="neutral"),
        )

        self.assertIn("RBNZ Gov Breman Speaks", summaries["NZD"].key_event)
        self.assertIn("Medium impact", summaries["NZD"].key_event)
        self.assertNotIn("Rate Decision", summaries["NZD"].key_event)

    def test_detail_ui_label_uses_upcoming_event(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("Upcoming event", html)
        self.assertNotIn("Next key event", html)

    def test_event_impact_ui_has_visual_hierarchy_copy(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("Market-moving event", html)
        self.assertIn("Can shift expectations", html)
        self.assertIn("Limited impact expected", html)
        self.assertIn("line-clamp-2", html)

    def test_market_mood_ui_uses_beginner_friendly_language(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("Market mood", html)
        self.assertIn("Investors are moving toward safer assets.", html)
        self.assertIn("Risk appetite improving", html)
        self.assertNotIn("Risk-on", html)
        self.assertNotIn("Risk-off", html)

    def test_macro_story_uses_one_global_thread(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("detectMacroThread", html)
        self.assertIn("eventMatchesThread", html)
        self.assertIn("What matters now", html)
        self.assertIn("What comes next", html)
        self.assertNotIn("Watch ${shortEventTitle(nextEvent).toLowerCase()}; it could change expectations.", html)

    def test_pair_tension_scene_is_context_not_signal(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("Macro tension scene", html)
        self.assertIn("Strongest tension", html)
        self.assertIn("Macro imbalances", html)
        self.assertIn("Explore all macro tensions", html)
        self.assertIn("What would change this", html)
        self.assertIn("macro backdrop", html)
        self.assertNotIn("macro weight", html)
        self.assertIn("safePairText", html)
        self.assertIn("ErrorBoundary", html)
        self.assertIn("direction.strong === pair.base", html)
        self.assertIn("direction.strong === pair.quote", html)
        self.assertNotIn("BUY", html)
        self.assertNotIn("SELL", html)

    def test_learn_page_explains_lucid_without_signal_framing(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("How to read Lucid", html)
        self.assertIn("Lucid explains macro context, not price prediction.", html)
        self.assertIn("Use Lucid to understand what is shaping currencies, not to receive trading instructions.", html)
        self.assertIn("Macro backdrop", html)
        self.assertIn("Pair tension", html)
        self.assertIn("Macro pressure", html)
        self.assertIn("Price alignment", html)
        self.assertIn("Three simple moves", html)
        self.assertIn("Start with the backdrop", html)
        self.assertIn("Read the tension", html)
        self.assertIn("Keep price separate", html)
        self.assertIn("Macro vs price", html)
        self.assertIn("Macro can be right while price stays mixed.", html)
        self.assertIn("Lucid explains the backdrop. Price can lag, resist, or move differently in the near term.", html)
        self.assertNotIn("How forex works", html)

    def test_frontend_fetches_payload_without_browser_cache(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("function uncachedUrl", html)
        self.assertIn("_lucid_ts", html)
        self.assertIn('fetch(uncachedUrl(LUCID_API_URL), { cache: "no-store" })', html)

    def test_frontend_uses_narrative_focus_lightly_in_hero(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("narrative_focus", html)
        self.assertIn("formatNarrativeTheme", html)
        self.assertIn("Today’s focus:", html)
        self.assertIn("China & global demand", html)

    def test_frontend_noise_reduction_mobile_polish_exists(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("currency-card", html)
        self.assertIn("currency-support-list", html)
        self.assertIn("pair-card", html)
        self.assertIn("section-block", html)
        self.assertIn("aria-label={`Open ${item.pair} macro tension`}", html)
        self.assertNotIn(">Open context</span>", html)

    def test_frontend_price_alignment_is_pair_detail_only(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn("price_alignment: payload?.price_alignment || null", html)
        self.assertIn("function PriceAlignmentBlock", html)
        self.assertIn("data.price_alignment?.[pair.pair]", html)
        self.assertIn("Price alignment", html)
        self.assertIn("{alignment.summary}", html)
        self.assertIn("{alignment.caveat}", html)
        self.assertIn("<PriceAlignmentBlock alignment={priceAlignment} t={t}/>", html)

    def test_frontend_calendar_mode_uses_attention_badges_without_directional_bias(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn('summaryMode === "Calendar-derived"', html)
        self.assertIn('return summary?.key_event ? "In focus" : "Quiet";', html)
        self.assertIn("function currencyBadgeStyle", html)
        self.assertIn("summaryMode={data.summary_mode}", html)
        self.assertIn("currencyDisplayLabel(summary, summaryMode)", html)
        self.assertIn('text: "In focus"', html)
        self.assertIn('text: "Quiet"', html)

    def test_frontend_pair_ordering_uses_relational_tension_without_renaming_mixed(self):
        with open("lucid_web_app_v2_lucid.html", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn('interactionType !== "mixed_macro_forces" ? 7 : 0', html)
        self.assertIn("item?.has_key_event || item?.base_key_event || item?.quote_key_event", html)
        self.assertIn('item.state === "Mixed macro picture" ? "Macro relationship in focus" : "Strongest tension"', html)
        self.assertIn("const relational = interactionType && interactionType !== \"mixed_macro_forces\";", html)
        self.assertIn("surface.relational", html)

    def test_weekly_limit_is_enforced(self):
        today = datetime.now(timezone.utc)
        raw_events = [
            _event(
                title=f"US CPI {i}",
                event_type="CPI",
                importance="high",
                date=(today + timedelta(days=i + 1)).strftime("%Y-%m-%d"),
            )
            for i in range(MAX_WEEKLY_EVENTS + 4)
        ]

        events = LucidEventEngine().build_lucid_events(raw_events)

        self.assertEqual(len(events), MAX_WEEKLY_EVENTS)


if __name__ == "__main__":
    unittest.main()

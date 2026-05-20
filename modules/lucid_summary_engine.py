"""
modules/lucid_summary_engine.py — Lucid Summary Engine (V2)

Product layer. Not a data layer. Not a report.

Each LucidSummary is readable in 3–5 seconds.
No jargon. No numbers. No signals. No analysis.

Rules:
  - 1 clear idea (headline)
  - 2–3 simple reasons
  - 1 short insight
  - 1 key event (optional)
  - Always 8 currencies returned
  - Never fails

If it needs explaining → it is wrong.
If it feels like a report → it is wrong.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from modules.lucid_compliance import (
    ALLOWED_SUMMARY_LABELS,
    ALLOWED_CONFIDENCE_LEVELS,
    ALLOWED_TIMEFRAMES,
    DISCLAIMER,
    assert_lucid_object_clean,
    clean_lucid_text,
)

try:
    from config import SUPPORTED_CURRENCIES
except Exception:
    SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LucidSummary:
    """
    What the user sees in the app. Readable in 3–5 seconds.
    No jargon. No numbers. No trading signals.
    """
    currency:  str
    label:     str           # "Supported" | "Neutral" | "Weak"
    confidence: str          # "Low" | "Medium" | "High"
    timeframe:  str          # "Short-term" | "Medium-term" | "Mixed"
    headline:  str           # 1 clear idea — 5–7 words
    reasons:   List[str]     # 2–3 plain sentences
    invalidation: str        # simple condition that would weaken the view
    insight:   str           # 1 sentence — the "so what"
    key_event: Optional[str] # "Fed speaks today" | None


@dataclass(frozen=True)
class MacroDriver:
    """
    Internal rule result. It decides the dominant Lucid idea before copy is built.
    This stays invisible to the user and keeps the output deterministic.
    """
    key: str
    headline: str
    reason: str
    insight: Optional[str] = None


@dataclass(frozen=True)
class NarrativeAngle:
    """
    Invisible copy choice. It refreshes the framing without changing the bias.
    """
    key: str
    headline: str


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — MAPPINGS
# ═══════════════════════════════════════════════════════════════════════════════

_BIAS_TO_LABEL: Dict[str, str] = {
    "soutenu": "Supported",
    "neutre":  "Neutral",
    "fragile": "Weak",
}

LUCID_DISCLAIMER = DISCLAIMER

_CB_NAMES: Dict[str, str] = {
    "USD": "The Fed",
    "EUR": "The ECB",
    "GBP": "The BOE",
    "JPY": "The BOJ",
    "CHF": "The SNB",
    "CAD": "The BOC",
    "AUD": "The RBA",
    "NZD": "The RBNZ",
}

# Refuge: driven by market fear / calm
_REFUGE_CURRENCIES   = frozenset({"JPY", "CHF"})

# Cyclical: driven by global mood / commodities
_CYCLICAL_CURRENCIES = frozenset({"AUD", "NZD", "CAD"})

_MAX_EVENT_HEADLINES = 2
_STRUCTURAL_EVENT_TERMS = frozenset({
    "cpi",
    "inflation",
    "gdp",
    "employment",
    "unemployment",
    "jobs",
    "payroll",
    "pmi",
    "rate statement",
    "rate decision",
    "cash rate",
    "interest rate",
    "central bank",
    "fed",
    "ecb",
    "boe",
    "boj",
    "snb",
    "boc",
    "rba",
    "rbnz",
})


def _rotation_index(currency: str, driver_key: str, option_count: int) -> int:
    if option_count <= 1:
        return 0
    # Date-based, deterministic rotation. It refreshes framing without random copy.
    seed = datetime.utcnow().toordinal() + sum(ord(char) for char in f"{currency}:{driver_key}")
    return seed % option_count


def _select_driver(currency: str, tone: str, bias: str, coherence: str, risk_label: str) -> MacroDriver:
    """
    Pick the most distinctive macro driver for this currency.
    Rates still matter, but they should not dominate every headline.
    """
    direction = "supporting" if bias == "soutenu" else "weighing on"

    if currency == "JPY":
        if risk_label == "risk_off":
            return MacroDriver(
                "risk",
                "Safety demand is supporting the yen",
                "The yen can gain demand when markets get nervous",
                "JPY can react quickly when global risk sentiment changes",
            )
        return MacroDriver(
            "risk",
            "The yen is driven by risk mood",
            "JPY can react quickly when global risk sentiment changes",
            "Market mood is the key driver for the yen right now",
        )

    if currency == "CHF":
        if risk_label == "risk_off":
            return MacroDriver(
                "risk",
                "Safety demand is supporting the franc",
                "The franc can attract demand when markets get nervous",
                "The franc depends heavily on whether investors want safety",
            )
        return MacroDriver(
            "risk",
            "The franc is waiting on risk mood",
            "Swiss stability matters most when markets become nervous",
            "The franc needs a clearer shift in market mood",
        )

    if currency == "AUD":
        if bias == "soutenu":
            return MacroDriver(
                "china_commodities",
                "Global demand is supporting AUD",
                "Australia is sensitive to China and global commodity demand",
                "AUD needs global demand to stay firm",
            )
        if bias == "neutre":
            return MacroDriver(
                "china_commodities",
                "China and commodity demand are driving AUD",
                "Australia is sensitive to China and global commodity demand",
                "AUD needs a clearer global demand story",
            )
        return MacroDriver(
            "china_commodities",
            "China and commodity demand are weighing on AUD",
            "Australia is sensitive to China and global commodity demand",
            "AUD needs a stronger global demand story",
        )

    if currency == "NZD":
        if bias == "soutenu":
            return MacroDriver(
                "global_demand",
                "Global demand is supporting NZD",
                "New Zealand is sensitive to global trade conditions",
                "NZD depends heavily on confidence in global demand",
            )
        if bias == "neutre":
            return MacroDriver(
                "global_demand",
                "Global demand is driving NZD",
                "New Zealand is sensitive to global trade conditions",
                "NZD needs clearer support from global demand",
            )
        return MacroDriver(
            "global_demand",
            "Global demand is weighing on NZD",
            "New Zealand is sensitive to global trade conditions",
            "NZD needs clearer support from global demand",
        )

    if currency == "CAD":
        if bias == "soutenu":
            return MacroDriver(
                "oil",
                "Oil and US demand are supporting CAD",
                "Canada is sensitive to oil prices and US demand",
                "CAD is strongest when oil and US demand are firm",
            )
        if bias == "neutre":
            return MacroDriver(
                "oil",
                "Oil and US demand are driving CAD",
                "Canada is sensitive to oil prices and US demand",
                "CAD needs a clearer lift from oil or US demand",
            )
        return MacroDriver(
            "oil",
            "Oil and US demand are weighing on CAD",
            "Canada is sensitive to oil prices and US demand",
            "CAD needs a clearer lift from oil or US demand",
        )

    if currency == "EUR" and bias == "fragile":
        return MacroDriver(
            "growth",
            "Weak growth is weighing on the euro",
            "Europe's economy lacks enough momentum",
            "The euro needs stronger growth to regain support",
        )

    if currency == "GBP":
        if bias == "neutre":
            return MacroDriver(
                "inflation_growth",
                "The pound is waiting for clarity",
                "UK growth and inflation are pulling in different directions",
                "The pound needs cleaner data to find direction",
            )
        if bias == "soutenu" and tone in ("hawkish", "hawkish_modere"):
            return MacroDriver(
                "inflation",
                "Inflation is supporting the pound",
                "Inflation is keeping the BOE cautious about cutting rates",
                "The pound still has support while inflation stays sticky",
            )

    if currency == "USD" and bias == "soutenu" and coherence in ("forte", "moderee"):
        return MacroDriver(
            "rates",
            "The Fed is keeping rates high",
            "The Fed is keeping rates high and has no plans to cut",
            "Higher rates keep money flowing into the dollar",
        )

    cb = _CB_NAMES.get(currency, "The central bank")
    if tone in ("hawkish", "hawkish_modere"):
        return MacroDriver(
            "rates",
            f"{cb} is keeping rates high",
            _reason_cb(currency, tone),
            "Higher rates can keep a currency supported",
        )
    if tone in ("dovish", "dovish_modere"):
        return MacroDriver(
            "rates",
            f"{cb} is moving toward rate cuts",
            _reason_cb(currency, tone),
            "Lower rates can reduce support for a currency",
        )

    return MacroDriver(
        "mixed",
        f"No clear direction for {currency} right now",
        "No single driver stands out right now",
        "The market is waiting for clearer data",
    )


def _event_headline(currency: str, key_event: Optional[str]) -> Optional[str]:
    if not key_event:
        return None
    title = key_event.split(" · ", 1)[0]
    title = re.sub(r"\s+(today|tomorrow|in \d+ days)$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+[A-Z][a-z]{2,8}\s+\d{1,2}$", "", title)
    title = title.strip()
    if not title:
        return None
    lower = title.lower()
    if any(term in lower for term in ("gov", "president", "speaks", "speech", "testifies")):
        cb = _CB_NAMES.get(currency, "")
        if cb:
            return f"{cb.replace('The ', '')} guidance is in focus for {currency}"
        return f"Central bank guidance is in focus for {currency}"
    if any(term in lower for term in ("cpi", "inflation")):
        return f"Inflation data is in focus for {currency}"
    if any(term in lower for term in ("employment", "unemployment", "jobs", "payroll", "adp", "claimant", "earnings", "wage")):
        return f"Labor data is the next test for {currency}"
    if any(term in lower for term in ("home sales", "housing", "building permits", "house price")):
        return f"Housing data is the next test for {currency}"
    if "pmi" in lower:
        return f"Growth data is in focus for {currency}"
    if any(term in lower for term in ("rate statement", "rate decision", "cash rate", "interest rate")):
        cb = _CB_NAMES.get(currency, "")
        return f"{cb.replace('The ', '') or currency} policy is in focus"
    if currency == "USD":
        return "US data is the next test for dollar support"
    return f"Markets are waiting for the next {currency} macro cue"


def _is_structural_medium_event(key_event: Optional[str], driver: MacroDriver) -> bool:
    if not key_event or "Medium impact" not in key_event:
        return False
    lower = key_event.lower()
    if not any(term in lower for term in _STRUCTURAL_EVENT_TERMS):
        return False
    if driver.key in {"rates", "inflation", "inflation_growth"}:
        return any(term in lower for term in (
            "cpi", "inflation", "employment", "unemployment", "jobs", "payroll",
            "rate statement", "rate decision", "cash rate", "central bank", "fed",
            "ecb", "boe", "boj", "snb", "boc", "rba", "rbnz",
        ))
    if driver.key == "growth":
        return any(term in lower for term in ("gdp", "pmi", "employment", "unemployment", "jobs"))
    if driver.key in {"china_commodities", "global_demand", "oil"}:
        return any(term in lower for term in ("gdp", "pmi", "employment", "unemployment", "jobs"))
    if driver.key == "risk":
        return any(term in lower for term in ("central bank", "fed", "ecb", "boe", "boj", "snb", "boc", "rba", "rbnz"))
    return False


def _event_can_lead_headline(key_event: Optional[str], driver: MacroDriver) -> bool:
    if not key_event:
        return False
    if "High impact" in key_event:
        return True
    return _is_structural_medium_event(key_event, driver)


_NARRATIVE_HEADLINES: Dict[str, Dict[str, List[str]]] = {
    "rates": {
        "soutenu": [
            "Rate expectations continue supporting USD",
            "Fed policy remains restrictive",
            "USD demand stays firm as rates stay elevated",
        ],
        "fragile": [
            "Rate expectations are weighing on the currency",
            "Central bank caution is reducing support",
            "Lower rate expectations are pressuring the currency",
        ],
        "neutre": [
            "Markets are waiting for clearer policy direction",
            "Rate expectations are not giving a clear direction",
        ],
    },
    "growth": {
        "soutenu": [
            "Growth momentum is supporting the currency",
            "Better economic data is lifting confidence",
        ],
        "fragile": [
            "Weak growth is weighing on the euro",
            "Weak growth data is pressuring the euro",
            "Growth remains the euro's main challenge",
        ],
        "neutre": [
            "Growth data is not giving a clear direction",
            "The market is waiting for cleaner growth signals",
        ],
    },
    "inflation": {
        "soutenu": [
            "Inflation is supporting the pound",
            "Sticky inflation keeps GBP supported",
            "BOE caution is helping the pound",
        ],
        "fragile": [
            "Cooling inflation is reducing currency support",
            "Lower inflation is easing policy pressure",
        ],
        "neutre": [
            "Inflation and growth are pulling in different directions",
            "The market is waiting for cleaner UK data",
        ],
    },
    "inflation_growth": {
        "soutenu": [
            "UK data is keeping GBP supported",
            "Inflation is still helping the pound",
        ],
        "fragile": [
            "Soft UK data is weighing on GBP",
            "Growth concerns are pressuring the pound",
        ],
        "neutre": [
            "The pound is waiting for clarity",
            "UK data is giving a mixed picture",
            "Inflation and growth are pulling apart",
        ],
    },
    "risk": {
        "soutenu": [
            "Safety demand is supporting the yen",
            "Risk mood is helping safer currencies",
            "Risk mood is supporting safer currencies",
        ],
        "fragile": [
            "The yen is driven by risk mood",
            "Calmer markets are changing risk mood",
            "Risk mood remains the main yen driver",
        ],
        "neutre": [
            "Risk mood is setting the tone",
            "Safer currencies are waiting for a trigger",
        ],
    },
    "china_commodities": {
        "soutenu": [
            "Global demand is supporting AUD",
            "China and commodities are helping AUD",
            "Commodity demand is lifting AUD",
        ],
        "fragile": [
            "China and commodity demand are weighing on AUD",
            "China demand is pressuring AUD",
            "Commodity weakness is weighing on AUD",
        ],
        "neutre": [
            "China and commodity demand are driving AUD",
            "AUD is waiting for clearer global demand",
        ],
    },
    "global_demand": {
        "soutenu": [
            "Global demand is supporting NZD",
            "Improving global trade is helping NZD",
            "NZD is benefiting from firmer demand",
        ],
        "fragile": [
            "Global demand is weighing on NZD",
            "Softer global demand is pressuring NZD",
            "NZD needs stronger global demand",
        ],
        "neutre": [
            "Global demand is driving NZD",
            "NZD is waiting for a clearer global demand story",
        ],
    },
    "oil": {
        "soutenu": [
            "Oil and US demand are supporting CAD",
            "Firm oil prices are helping CAD",
            "Oil-sensitive demand is keeping CAD supported",
        ],
        "fragile": [
            "Oil and US demand are weighing on CAD",
            "Weaker oil demand is pressuring CAD",
            "CAD needs stronger oil or US demand",
        ],
        "neutre": [
            "Oil and US demand are driving CAD",
            "CAD is waiting for a clearer oil story",
        ],
    },
}


def _select_narrative_angle(
    currency: str,
    bias: str,
    risk_label: str,
    driver: MacroDriver,
    key_event: Optional[str],
    allow_event_headline: bool = False,
) -> NarrativeAngle:
    """
    Choose a safe framing angle without changing the macro bias.
    Events can lead only when a real key_event exists.
    """
    event_headline = _event_headline(currency, key_event)
    if allow_event_headline and event_headline:
        return NarrativeAngle("upcoming_catalyst", event_headline)

    if driver.key == "risk":
        options = _NARRATIVE_HEADLINES["risk"].get(bias, _NARRATIVE_HEADLINES["risk"]["neutre"])
        if currency == "CHF":
            options = [
                option.replace("yen", "franc").replace("currency", "franc")
                for option in options
            ]
        return NarrativeAngle("risk_mood", options[_rotation_index(currency, driver.key, len(options))])

    if risk_label == "risk_off" and currency in {"USD", "AUD", "NZD"}:
        if currency == "USD" and bias == "soutenu":
            return NarrativeAngle("risk_mood", "Defensive markets are supporting USD")
        if currency in {"AUD", "NZD"} and bias == "fragile":
            return NarrativeAngle("risk_mood", f"Defensive markets are pressuring {currency}")

    by_bias = _NARRATIVE_HEADLINES.get(driver.key, {})
    options = by_bias.get(bias) or by_bias.get("neutre") or [driver.headline]
    return NarrativeAngle(driver.key, options[_rotation_index(currency, driver.key, len(options))])


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — HEADLINE  (the 1 clear idea)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_headline(
    currency: str,
    tone: str,
    bias: str,
    risk_label: str,
    driver: Optional[MacroDriver] = None,
    key_event: Optional[str] = None,
    allow_event_headline: bool = False,
) -> str:
    """
    One idea. Five to seven words.
    Refuge currencies → driven by market mood.
    Cyclical currencies → driven by global sentiment.
    Others → driven by central bank stance.
    """
    if driver is not None:
        return _select_narrative_angle(
            currency,
            bias,
            risk_label,
            driver,
            key_event,
            allow_event_headline,
        ).headline

    # ── Safe-haven currencies ────────────────────────────────────────────────
    if currency == "JPY":
        if risk_label == "risk_off":
            return "Investors are moving to safety"
        if risk_label == "risk_on":
            return "Calm markets reduce yen demand"
        return "Yen waiting for a market trigger"

    if currency == "CHF":
        if risk_label == "risk_off":
            return "Uncertainty is driving money to Switzerland"
        if risk_label == "risk_on":
            return "Risk appetite is reducing franc demand"
        return "Franc steady with no strong catalyst"

    # ── Commodity / sentiment currencies ────────────────────────────────────
    if currency == "AUD":
        if risk_label == "risk_on":
            return "Global optimism is lifting the Australian dollar"
        if risk_label == "risk_off":
            return "Risk aversion is pressuring the Australian dollar"

    if currency == "NZD":
        if risk_label == "risk_on":
            return "Positive mood is supporting the New Zealand dollar"
        if risk_label == "risk_off":
            return "Market nerves are weighing on the New Zealand dollar"

    if currency == "CAD":
        if risk_label == "risk_on":
            return "Strong global demand is helping the Canadian dollar"
        if risk_label == "risk_off":
            return "Weaker global demand is hurting the Canadian dollar"

    # ── Central bank-driven (USD, EUR, GBP + cyclical fallback) ─────────────
    cb = _CB_NAMES.get(currency, "Central bank")
    _tone_to_headline: Dict[str, str] = {
        "hawkish":        f"{cb} is keeping rates high",
        "hawkish_modere": f"{cb} isn't ready to cut yet",
        "neutre":         f"{cb} is watching and waiting",
        "dovish_modere":  f"{cb} is moving toward rate cuts",
        "dovish":         f"{cb} is cutting rates",
    }
    headline = _tone_to_headline.get(tone)
    if headline:
        return headline

    # Absolute fallback
    if bias == "soutenu":
        return f"{currency} has solid support right now"
    if bias == "fragile":
        return f"{currency} is under pressure"
    return f"No clear direction for {currency} right now"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — REASONS  (2–3, no repetition, no jargon)
# ═══════════════════════════════════════════════════════════════════════════════

# What the central bank is doing — plain English
_CB_STANCE: Dict[str, str] = {
    "hawkish":        "is keeping rates high and has no plans to cut",
    "hawkish_modere": "wants to see more progress before cutting rates",
    "neutre":         "is waiting for clearer economic data",
    "dovish_modere":  "is becoming more open to rate cuts",
    "dovish":         "is actively cutting rates",
}

def _reason_cb(currency: str, tone: str) -> str:
    cb = _CB_NAMES.get(currency, "Central bank")
    stance = _CB_STANCE.get(tone, "is monitoring the situation")
    return f"{cb} {stance}"


# What the economic data is saying
_MOMENTUM: Dict[str, Dict[str, str]] = {
    "soutenu": {
        "forte":   "The economy is performing well",
        "moderee": "Most recent data has been positive",
        "faible":  "Data is leaning positive, though not consistently",
    },
    "fragile": {
        "forte":   "The economic data has been consistently weak",
        "moderee": "More weak data than strong lately",
        "faible":  "Some weak data is adding to the pressure",
    },
    "neutre": {
        "forte":   "Data is not giving a clear direction",
        "moderee": "The economic picture is mixed",
        "faible":  "Not enough data yet to read a trend",
    },
}

def _reason_momentum(bias: str, coherence: str) -> str:
    block = _MOMENTUM.get(bias, _MOMENTUM["neutre"])
    return block.get(coherence, block["moderee"])


# Structural factor specific to each currency
_STRUCTURAL: Dict[str, Dict[str, str]] = {
    "JPY": {
        "risk_off": "Global uncertainty is boosting demand for the yen",
        "risk_on":  "Low rates make the yen less attractive in calm markets",
        "neutral":  "The yen is highly sensitive to sudden mood changes",
    },
    "CHF": {
        "risk_off": "The franc is attracting money as investors seek safety",
        "risk_on":  "Investors are moving out of safe assets",
        "neutral":  "Swiss stability is steady in the background",
    },
    "AUD": {
        "risk_on":  "Commodity demand and China's growth are adding support",
        "risk_off": "Weak commodity demand is making things harder",
        "neutral":  "Commodity prices and China remain the key factors",
    },
    "NZD": {
        "risk_on":  "Improving global trade is a tailwind",
        "risk_off": "Small economies get hit harder when risk falls",
        "neutral":  "Global trade conditions matter more than local data",
    },
    "CAD": {
        "risk_on":  "Rising oil prices are supporting the Canadian dollar",
        "risk_off": "Lower oil prices are adding to the pressure",
        "neutral":  "Oil prices and US economic health are the key drivers",
    },
    "USD": {
        "risk_off": "The dollar benefits from global demand for safety",
        "risk_on":  "Strong US growth is keeping demand for the dollar solid",
        "neutral":  "The dollar remains the world's most-used currency",
    },
    "EUR": {
        "risk_off": "Slow European growth is weighing on the outlook",
        "risk_on":  "Europe's slow growth is limiting the euro's upside",
        "neutral":  "Slow growth in Germany is a persistent drag",
    },
    "GBP": {
        "risk_off": "The UK's trade exposure increases its vulnerability",
        "risk_on":  "The UK services sector is supporting the growth picture",
        "neutral":  "High inflation is limiting the BOE's room to act",
    },
}

def _reason_structural(currency: str, risk_label: str) -> Optional[str]:
    block = _STRUCTURAL.get(currency)
    if not block:
        return None
    return block.get(risk_label, block.get("neutral"))


def _build_reasons(
    currency: str,
    tone: str,
    bias: str,
    coherence: str,
    risk_label: str,
    driver: Optional[MacroDriver] = None,
) -> List[str]:
    """
    Assembles 2–3 reasons. Filters aggressively.
    Logic varies by currency type — never forces weak reasons.
    """
    reasons: List[str] = []

    def add(reason: Optional[str]) -> None:
        if reason and reason not in reasons:
            reasons.append(reason)

    if driver is not None:
        add(driver.reason)

    # ── Safe-haven currencies: risk context is primary ───────────────────────
    if currency in _REFUGE_CURRENCIES:
        r_struct = _reason_structural(currency, risk_label)
        add(r_struct)
        # Add CB only if it is clearly directional
        if tone in ("hawkish", "hawkish_modere", "dovish", "dovish_modere"):
            add(_reason_cb(currency, tone))
        # 2 reasons is enough
        return reasons[:3]

    # ── Cyclical currencies: risk + CB + data if strongly directional ────────
    if currency in _CYCLICAL_CURRENCIES:
        r_struct = _reason_structural(currency, risk_label)
        add(r_struct)
        if tone not in ("neutre",):
            add(_reason_cb(currency, tone))
        # Only add data if the picture is unambiguous
        if bias != "neutre" and coherence == "forte":
            add(_reason_momentum(bias, coherence))
        return reasons[:3]

    # ── CB-driven currencies (USD, EUR, GBP) ─────────────────────────────────
    add(_reason_cb(currency, tone))
    add(_reason_momentum(bias, coherence))

    r_struct = _reason_structural(currency, risk_label)
    add(r_struct)

    return reasons[:3]


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — INSIGHT  (the "so what" — 1 sentence)
# ═══════════════════════════════════════════════════════════════════════════════

# Refuge and cyclical: indexed by risk_label
_INSIGHTS_REFUGE: Dict[str, Dict[str, str]] = {
    "JPY": {
        "risk_off": "The yen rises when the world gets nervous",
        "risk_on":  "Low-yield currencies lose appeal when risk is on",
        "neutral":  "A sudden shift in mood could move the yen fast",
    },
    "CHF": {
        "risk_off": "Switzerland is where money goes when fear rises",
        "risk_on":  "Safe assets lose ground when confidence returns",
        "neutral":  "Calm markets keep the franc in a holding pattern",
    },
}

_INSIGHTS_CYCLICAL: Dict[str, Dict[str, str]] = {
    "AUD": {
        "risk_on":  "Global growth is the main wind in AUD's sails",
        "risk_off": "The AUD moves with the world's mood more than its own",
        "neutral":  "The next big move will likely come from outside Australia",
    },
    "NZD": {
        "risk_on":  "High-yield currencies shine when confidence is high",
        "risk_off": "When risk falls, high-yield currencies fall faster",
        "neutral":  "Global sentiment is the dominant driver here",
    },
    "CAD": {
        "risk_on":  "Oil and US demand are the real engine for the loonie",
        "risk_off": "Oil weakness and risk aversion hit CAD at the same time",
        "neutral":  "Oil prices often move before CAD does",
    },
}

# CB-driven: indexed by tone
_INSIGHTS_CB: Dict[str, Dict[str, str]] = {
    "USD": {
        "hawkish":        "Higher rates keep money flowing into the dollar",
        "hawkish_modere": "The longer rates stay high, the longer the dollar holds",
        "neutre":         "The dollar needs clearer data to find direction",
        "dovish_modere":  "Rate cuts ahead are slowly reducing the dollar's appeal",
        "dovish":         "Falling rates are reducing the dollar's advantage",
    },
    "EUR": {
        "hawkish":        "Higher rates in Europe are keeping the euro supported",
        "hawkish_modere": "The ECB is holding steady, and the euro with it",
        "neutre":         "The euro is stuck between slow growth and high rates",
        "dovish_modere":  "Rate cuts ahead are taking the shine off the euro",
        "dovish":         "Falling rates are the main weight on the euro right now",
    },
    "GBP": {
        "hawkish":        "High UK rates are keeping the pound well supported",
        "hawkish_modere": "Sticky inflation is keeping the BOE from cutting",
        "neutre":         "The pound is caught between growth worries and inflation",
        "dovish_modere":  "Expected rate cuts are reducing the pound's support",
        "dovish":         "Falling rates are weakening the pound's support",
    },
}

# Generic fallbacks by tone
_INSIGHTS_GENERIC: Dict[str, str] = {
    "hawkish":        "Higher rates attract capital and support the currency",
    "hawkish_modere": "Rates staying high keep this currency competitive",
    "neutre":         "The market is waiting for clearer data",
    "dovish_modere":  "Rate cuts ahead are already putting pressure on the currency",
    "dovish":         "Falling rates reduce what this currency offers investors",
}


def _build_insight(currency: str, tone: str, risk_label: str) -> str:
    if currency in _REFUGE_CURRENCIES:
        block = _INSIGHTS_REFUGE.get(currency, {})
        return block.get(risk_label, "Market mood is the dominant force here")

    if currency in _CYCLICAL_CURRENCIES:
        block = _INSIGHTS_CYCLICAL.get(currency, {})
        return block.get(risk_label, "Global sentiment matters more than local data")

    cb_block = _INSIGHTS_CB.get(currency, {})
    result = cb_block.get(tone)
    if result:
        return result

    return _INSIGHTS_GENERIC.get(tone, "Rate expectations are moving this currency")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — KEY EVENT  (the most relevant upcoming event for this currency)
# ═══════════════════════════════════════════════════════════════════════════════

# Maps keywords in event titles to clean readable labels
_TITLE_KEYWORDS: Dict[str, str] = {
    "nfp":               "jobs report",
    "non-farm":          "jobs report",
    "employment change": "jobs report",
    "unemployment":      "jobs data",
    "cpi":               "CPI release",
    "inflation":         "inflation data",
    "ppi":               "PPI release",
    "gdp":               "GDP release",
    "retail sales":      "retail sales",
    "pmi":               "PMI survey",
    "ism":               "ISM survey",
    "confidence":        "confidence survey",
    "decision":          "rate decision",
    "presser":           "press conference",
    "testimony":         "testimony",
    "speech":            "speech",
    "remarks":           "speech",
    "speaks":            "speech",
    "conference":        "press conference",
}

_KNOWN_SPEAKERS = [
    "powell", "lagarde", "bailey", "ueda", "macklem",
    "bullock", "orr", "waller", "pill", "nagel",
]

_CB_IN_TITLE: Dict[str, str] = {
    "fomc": "Fed", "fed ": "Fed",
    "ecb":  "ECB",
    "boe":  "BOE",
    "boj":  "BOJ",
    "snb":  "SNB",
    "boc":  "BOC",
    "rba":  "RBA",
    "rbnz": "RBNZ",
}


def _clean_title(title: str) -> str:
    """
    Turns a raw event title into a short, plain-language label.
    "Consumer Price Index (CPI) m/m" → "CPI release"
    "Powell Speaks — Testimony"       → "Powell speech"
    "BOE Monetary Policy Decision"    → "BOE decision"
    """
    t = title.lower()

    # Named speaker
    for speaker in _KNOWN_SPEAKERS:
        if speaker in t:
            return f"{speaker.capitalize()} speech"

    # CB name in title
    for pattern, cb_name in _CB_IN_TITLE.items():
        if pattern in t:
            for kw, label in _TITLE_KEYWORDS.items():
                if kw in t:
                    return f"{cb_name} {label}"
            return f"{cb_name} decision"

    # Generic keyword
    for kw, label in _TITLE_KEYWORDS.items():
        if kw in t:
            return label

    # Fallback: truncated original
    return title[:30].lower()


def _find_key_event(currency: str, lucid_events: List) -> Optional[str]:
    """
    Returns the most relevant upcoming event for a given currency.
    Format: "RBNZ Gov Speaks today" | "CPI release May 8" | None
    """
    today_dates = []
    for event in lucid_events:
        if getattr(event, "is_today", False):
            try:
                today_dates.append(datetime.strptime(getattr(event, "date", ""), "%Y-%m-%d").date())
            except (TypeError, ValueError):
                pass
    today = min(today_dates) if today_dates else datetime.now().date()
    events = []
    for event in lucid_events:
        if getattr(event, "currency", "") != currency:
            continue
        event_date = getattr(event, "date", "")
        try:
            parsed_date = datetime.strptime(event_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if parsed_date >= today:
            events.append(event)
    if not events:
        return None

    # Sort: high > medium > low, then nearest date.
    _imp_order = {"high": 0, "medium": 1, "low": 2, "unknown": 9}
    events.sort(key=lambda e: (
        _imp_order.get(getattr(e, "importance", "low"), 9),
        getattr(e, "date", ""),
    ))

    ev       = events[0]
    title    = getattr(ev, "title", "")
    timing   = getattr(ev, "timing_label", "This week")
    timing_text = timing if timing[:1].isupper() and timing.split(" ", 1)[0] not in {"Today", "Tomorrow", "In"} else timing.lower()
    event_title = clean_lucid_text(title) or f"Upcoming {currency} event"
    importance = getattr(ev, "importance", "unknown")
    impact_text = f" · {importance.capitalize()} impact" if importance in {"high", "medium", "low"} else ""

    if timing == "Upcoming event":
        return f"{event_title}{impact_text}"
    return f"{event_title} {timing_text}{impact_text}"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — PRODUCT GUARDS
# ═══════════════════════════════════════════════════════════════════════════════

_TONE_TO_BIAS: Dict[str, str] = {
    "hawkish": "soutenu",
    "hawkish_modere": "soutenu",
    "neutre": "neutre",
    "dovish_modere": "fragile",
    "dovish": "fragile",
}


def _has_strong_contradiction(currency: str, tone: str, bias: str, risk_label: str) -> bool:
    """
    Hide backend conflict from the user. If the tone-derived reading and the
    explicit currency bias disagree, Lucid shows a mixed/neutral view.
    """
    expected_bias = _TONE_TO_BIAS.get(tone)
    if expected_bias and bias in _BIAS_TO_LABEL and expected_bias != bias:
        return True

    if currency in _REFUGE_CURRENCIES:
        if risk_label == "risk_off" and bias == "fragile":
            return True
        if risk_label == "risk_on" and bias == "soutenu":
            return True

    if currency in _CYCLICAL_CURRENCIES:
        if risk_label == "risk_off" and bias == "soutenu":
            return True
        if risk_label == "risk_on" and bias == "fragile":
            return True

    return False


def _mixed_summary(currency: str, lucid_events: List) -> LucidSummary:
    return LucidSummary(
        currency=currency,
        label="Neutral",
        confidence="Low",
        timeframe="Mixed",
        headline="Mixed picture, no clear driver",
        reasons=[
            "The main forces are pulling in different directions",
            "The market is waiting for clearer economic data",
        ],
        invalidation="This view changes when the data points in one clearer direction",
        insight="There is not enough alignment to call this currency supported or weak",
        key_event=_find_key_event(currency, lucid_events),
    )


def _clean_summary(summary: LucidSummary) -> LucidSummary:
    label = summary.label if summary.label in ALLOWED_SUMMARY_LABELS else "Neutral"
    confidence = summary.confidence if summary.confidence in ALLOWED_CONFIDENCE_LEVELS else "Low"
    timeframe = summary.timeframe if summary.timeframe in ALLOWED_TIMEFRAMES else "Mixed"
    cleaned = LucidSummary(
        currency=clean_lucid_text(summary.currency).upper()[:3],
        label=label,
        confidence=confidence,
        timeframe=timeframe,
        headline=clean_lucid_text(summary.headline),
        reasons=[clean_lucid_text(reason) for reason in summary.reasons if clean_lucid_text(reason)][:3],
        invalidation=clean_lucid_text(summary.invalidation),
        insight=clean_lucid_text(summary.insight),
        key_event=clean_lucid_text(summary.key_event) if summary.key_event else None,
    )
    if len(cleaned.reasons) < 2:
        cleaned.reasons.append("The market is waiting for clearer economic data")
    cleaned.reasons = cleaned.reasons[:3]
    assert_lucid_object_clean(cleaned)
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — FALLBACK
# ═══════════════════════════════════════════════════════════════════════════════

def _fallback_summary(currency: str, lucid_events: List) -> LucidSummary:
    """Safe fallback — always valid, never fails."""
    return _clean_summary(LucidSummary(
        currency=currency,
        label="Neutral",
        confidence="Low",
        timeframe="Mixed",
        headline="No clear direction right now",
        reasons=[
            "The picture is mixed",
            "The market is waiting for new data",
        ],
        invalidation="This view changes when fresh data gives a clearer picture",
        insight="The next move depends on what the data shows",
        key_event=_find_key_event(currency, lucid_events),
    ))


def _build_confidence(bias: str, tone: str, coherence: str) -> str:
    expected_bias = _TONE_TO_BIAS.get(tone, "neutre")
    if bias == "neutre" or tone == "neutre":
        return "Low" if coherence == "faible" else "Medium"
    if expected_bias != bias:
        return "Low"
    if coherence == "forte":
        return "High"
    if coherence == "moderee":
        return "Medium"
    return "Low"


def _build_timeframe(key_event: Optional[str], bias: str, tone: str, coherence: str) -> str:
    if bias == "neutre" or tone == "neutre" or coherence == "faible":
        return "Mixed"
    if key_event:
        return "Short-term"
    return "Medium-term"


def _build_invalidation(currency: str, tone: str, bias: str, driver: Optional[MacroDriver] = None) -> str:
    if driver is not None:
        if driver.key in ("china_commodities", "global_demand"):
            return "This view changes if China or global demand improves clearly"
        if driver.key == "oil":
            return "This view changes if oil prices or US demand improve clearly"
        if driver.key == "risk":
            return "This view changes if global market mood shifts clearly"
        if driver.key == "growth":
            return "This view changes if growth data improves clearly"
        if driver.key == "inflation":
            return "This view changes if inflation cools faster than expected"

    cb = _CB_NAMES.get(currency, "The central bank")
    if bias == "soutenu":
        if tone in ("hawkish", "hawkish_modere"):
            return f"This view weakens if inflation slows or {cb} sounds more careful"
        return "This view weakens if economic data starts to disappoint"
    if bias == "fragile":
        if tone in ("dovish", "dovish_modere"):
            return f"This view weakens if growth improves or {cb} sounds less cautious"
        return "This view weakens if the economy shows clearer strength"
    return "This view changes when fresh data gives a clearer picture"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — BUILDER PER CURRENCY
# ═══════════════════════════════════════════════════════════════════════════════

def _build_one(
    currency:     str,
    narrative,              # Optional[Narrative]
    lucid_events: List,     # List[LucidEvent]
    risk_label:   str,      # "risk_on" | "risk_off" | "neutral"
    allow_event_headline: bool = False,
) -> LucidSummary:

    if narrative is None:
        return _fallback_summary(currency, lucid_events)

    try:
        bias      = getattr(narrative, "currency_bias",  "neutre")
        tone      = getattr(narrative, "dominant_tone",  "neutre")
        coherence = getattr(narrative, "coherence",      "moderee")

        if _has_strong_contradiction(currency, tone, bias, risk_label):
            return _clean_summary(_mixed_summary(currency, lucid_events))

        label     = _BIAS_TO_LABEL.get(bias, "Neutral")
        driver    = _select_driver(currency, tone, bias, coherence, risk_label)
        key_event = _find_key_event(currency, lucid_events)
        headline  = _make_headline(currency, tone, bias, risk_label, driver, key_event, allow_event_headline)
        reasons   = _build_reasons(currency, tone, bias, coherence, risk_label, driver)
        insight   = driver.insight or _build_insight(currency, tone, risk_label)
        confidence = _build_confidence(bias, tone, coherence)
        timeframe = _build_timeframe(key_event, bias, tone, coherence)
        invalidation = _build_invalidation(currency, tone, bias, driver)

        # Guarantee minimum 2 reasons
        if len(reasons) < 2:
            if bias == "soutenu":
                reasons.append("Most factors point in the same direction")
            elif bias == "fragile":
                reasons.append("Several factors are working against this currency")
            else:
                reasons.append("No single driver stands out right now")

        return _clean_summary(LucidSummary(
            currency=currency,
            label=label,
            confidence=confidence,
            timeframe=timeframe,
            headline=headline,
            reasons=reasons[:3],
            invalidation=invalidation,
            insight=insight,
            key_event=key_event,
        ))

    except Exception as exc:
        logger.warning(f"LucidSummaryEngine: error on {currency} — {exc} — fallback applied")
        return _fallback_summary(currency, lucid_events)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def build_lucid_summaries(
    narratives:       Dict,   # Dict[str, Narrative]
    lucid_events:     List,   # List[LucidEvent]
    risk_environment,         # Optional[RiskEnvironment]
) -> Dict[str, "LucidSummary"]:
    """
    Builds LucidSummary for all 8 currencies.

    Always returns exactly 8 entries.
    Never raises — each currency is individually protected.

    Args:
        narratives        : from WeeklyMacroAnalysis
        lucid_events      : from LucidEventEngine
        risk_environment  : from compute_risk_environment()

    Returns:
        Dict[currency, LucidSummary] — 8 entries guaranteed
    """
    risk_label: str = "neutral"
    if risk_environment is not None:
        risk_label = getattr(risk_environment, "label", "neutral") or "neutral"

    event_headline_budget: set[str] = set()
    for currency in SUPPORTED_CURRENCIES:
        try:
            narrative = narratives.get(currency) if narratives else None
            if narrative is None:
                continue
            bias      = getattr(narrative, "currency_bias",  "neutre")
            tone      = getattr(narrative, "dominant_tone",  "neutre")
            coherence = getattr(narrative, "coherence",      "moderee")
            if _has_strong_contradiction(currency, tone, bias, risk_label):
                continue
            driver = _select_driver(currency, tone, bias, coherence, risk_label)
            key_event = _find_key_event(currency, lucid_events or [])
            if _event_can_lead_headline(key_event, driver):
                event_headline_budget.add(currency)
                if len(event_headline_budget) >= _MAX_EVENT_HEADLINES:
                    break
        except Exception:
            continue

    summaries: Dict[str, LucidSummary] = {}

    for currency in SUPPORTED_CURRENCIES:
        try:
            narrative = narratives.get(currency) if narratives else None
            summaries[currency] = _build_one(
                currency=currency,
                narrative=narrative,
                lucid_events=lucid_events or [],
                risk_label=risk_label,
                allow_event_headline=currency in event_headline_budget,
            )
        except Exception as exc:
            logger.error(f"LucidSummaryEngine: critical error on {currency} — {exc}")
            summaries[currency] = _fallback_summary(currency, lucid_events or [])

    supported = [c for c, s in summaries.items() if s.label == "Supported"]
    weak      = [c for c, s in summaries.items() if s.label == "Weak"]
    neutral   = [c for c, s in summaries.items() if s.label == "Neutral"]
    logger.info(
        f"LucidSummaries: {len(summaries)}/8 — "
        f"Supported={supported} | Neutral={neutral} | Weak={weak}"
    )

    return summaries

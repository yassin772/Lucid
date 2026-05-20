"""
Deterministic macro shock filter for Lucid.

This layer is intentionally rare and conservative. It turns confirmed narrative
events into a simple macro transmission chain, without creating a news feed or
trading recommendation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, List, Optional

from modules.lucid_compliance import assert_lucid_object_clean, clean_lucid_text


@dataclass(frozen=True)
class MacroShock:
    shock_type: str
    severity: str
    confidence: str
    title: str
    transmission_chain: List[str]
    supports: List[str]
    pressures: List[str]
    explanation: str
    source_count: int
    evidence_titles: List[str]


SHOCK_RULES = {
    "geopolitical_risk": {
        "keywords": ("iran", "war", "missile", "attack", "military", "escalation", "conflict", "geopolitical"),
        "title": "Geopolitical risk is shaping market mood",
        "chain": ["Geopolitical risk", "Defensive market mood", "Demand for safer currencies"],
        "supports": ["USD", "CHF", "JPY"],
        "pressures": ["AUD", "NZD", "EUR"],
        "explanation": "Investors may prefer safer currencies when geopolitical uncertainty rises.",
    },
    "oil_supply_shock": {
        "keywords": ("oil", "crude", "brent", "opec", "supply", "strait of hormuz", "energy"),
        "title": "Oil supply risk is affecting the macro backdrop",
        "chain": ["Oil supply risk", "Energy price pressure", "Inflation concern"],
        "supports": ["CAD", "USD"],
        "pressures": ["EUR", "JPY", "AUD", "NZD"],
        "explanation": "Energy supply stress can lift inflation concern and affect oil-sensitive currencies.",
    },
    "trade_tension": {
        "keywords": ("tariff", "tariffs", "trade tension", "trade war", "export controls", "import ban"),
        "title": "Trade tension is weighing on global demand",
        "chain": ["Trade tension", "Global demand concern", "Pressure on cyclical currencies"],
        "supports": ["USD", "JPY"],
        "pressures": ["AUD", "NZD", "EUR"],
        "explanation": "Trade tension can make investors more cautious about global growth.",
    },
    "banking_stress": {
        "keywords": ("banking stress", "bank crisis", "bank failure", "liquidity stress", "deposit outflow"),
        "title": "Banking stress is tightening market mood",
        "chain": ["Banking stress", "Credit concern", "Defensive market mood"],
        "supports": ["USD", "CHF", "JPY"],
        "pressures": ["AUD", "NZD", "EUR", "GBP"],
        "explanation": "Banking stress can make markets more defensive and reduce risk appetite.",
    },
    "recession_fear": {
        "keywords": ("recession", "hard landing", "growth scare", "demand slowdown", "slowdown fears"),
        "title": "Growth concern is shaping the macro backdrop",
        "chain": ["Growth concern", "Lower risk appetite", "Demand for safer currencies"],
        "supports": ["USD", "CHF", "JPY"],
        "pressures": ["AUD", "NZD", "EUR", "GBP"],
        "explanation": "Growth concern can pressure currencies linked to global demand.",
    },
    "political_risk": {
        "keywords": ("political crisis", "election crisis", "government collapse", "no confidence vote"),
        "title": "Political risk is adding macro uncertainty",
        "chain": ["Political risk", "Policy uncertainty", "Cautious market mood"],
        "supports": ["USD", "CHF", "JPY"],
        "pressures": ["EUR", "GBP", "AUD", "NZD"],
        "explanation": "Political uncertainty can make markets more cautious.",
    },
    "sanctions": {
        "keywords": ("sanction", "sanctions", "asset freeze", "embargo"),
        "title": "Sanctions risk is affecting the macro backdrop",
        "chain": ["Sanctions risk", "Trade and supply disruption", "Defensive market mood"],
        "supports": ["USD", "CHF", "JPY"],
        "pressures": ["AUD", "NZD", "EUR"],
        "explanation": "Sanctions can disrupt trade, energy supply, and market confidence.",
    },
}


def _normalise_item(item) -> dict:
    if isinstance(item, str):
        return {"title": item, "source": "unknown"}
    if isinstance(item, dict):
        return {
            "title": str(item.get("title") or item.get("headline") or ""),
            "source": str(item.get("source") or item.get("publisher") or "unknown"),
        }
    title = getattr(item, "title", "") or getattr(item, "headline", "")
    source = getattr(item, "source", None) or getattr(item, "publisher", None) or "unknown"
    return {"title": str(title), "source": str(source)}


def _score_headlines(items: List[dict]) -> dict:
    scores = {key: 0 for key in SHOCK_RULES}
    evidence = {key: [] for key in SHOCK_RULES}
    sources = {key: set() for key in SHOCK_RULES}

    for item in items:
        title = clean_lucid_text(item.get("title", ""))
        lower = title.lower()
        if not title:
            continue
        for shock_type, rule in SHOCK_RULES.items():
            matched = [keyword for keyword in rule["keywords"] if keyword in lower]
            if not matched:
                continue
            scores[shock_type] += len(matched)
            evidence[shock_type].append(title)
            sources[shock_type].add(item.get("source") or "unknown")

    return {"scores": scores, "evidence": evidence, "sources": sources}


def detect_macro_shock(raw_items: Optional[Iterable]) -> Optional[dict]:
    items = [_normalise_item(item) for item in (raw_items or [])]
    scored = _score_headlines(items)
    scores = scored["scores"]
    shock_type = max(scores, key=scores.get) if scores else None
    if not shock_type or scores[shock_type] <= 0:
        return None

    evidence_titles = scored["evidence"][shock_type]
    source_count = len(scored["sources"][shock_type])
    evidence_count = len(evidence_titles)

    # Conservative gate: avoid letting one isolated headline alter Lucid.
    if source_count < 2 and evidence_count < 3:
        return None

    confidence = "High" if source_count >= 3 or evidence_count >= 4 else "Medium"
    severity = "High" if scores[shock_type] >= 5 and source_count >= 2 else "Medium"
    rule = SHOCK_RULES[shock_type]
    shock = MacroShock(
        shock_type=shock_type,
        severity=severity,
        confidence=confidence,
        title=rule["title"],
        transmission_chain=list(rule["chain"]),
        supports=list(rule["supports"]),
        pressures=list(rule["pressures"]),
        explanation=rule["explanation"],
        source_count=source_count,
        evidence_titles=evidence_titles[:3],
    )
    result = asdict(shock)
    assert_lucid_object_clean(result)
    return result

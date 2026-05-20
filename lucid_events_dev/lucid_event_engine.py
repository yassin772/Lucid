"""
lucid_event_engine.py — Standalone Lucid Events Engine
=======================================================

Transforme des événements macro bruts (dict JSON) en alertes pédagogiques
pour l'application Lucid.

Philosophie Lucid :
  ✓ Pourquoi cet événement compte
  ✓ Sur quoi le marché se concentre
  ✓ Quelle leçon macro retenir
  ✗ Pas de signal directionnel
  ✗ Pas de buy/sell
  ✗ Pas de setup tradable

Standalone : aucun import du projet macro-scenarios-bot.
Dépendances : Python stdlib uniquement (json, dataclasses, datetime, typing, logging).

Usage :
    from lucid_event_engine import LucidEventEngine, load_events_from_json

    raw_events = load_events_from_json("sample_events.json")
    engine = LucidEventEngine()
    today_events, weekly_events = engine.build(raw_events)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — STRUCTURES DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RawEvent:
    """
    Représentation normalisée d'un événement brut depuis le JSON.
    Tous les champs sont optionnels pour éviter tout crash sur donnée incomplète.
    """
    date:       str            # "2026-05-07"
    currency:   str            # "USD"
    event_type: str            # "CPI", "FOMC", "OTHER"…
    title:      str            # Titre original
    importance: str            # Normalisé → "high" | "medium" | "low"
    expected:   Optional[str] = None
    previous:   Optional[str] = None
    actual:     Optional[str] = None
    note:       Optional[str] = None


@dataclass
class LucidEvent:
    """
    Événement macro enrichi pour l'interface Lucid.
    Contient uniquement du contenu pédagogique — aucune directive de trading.
    """
    date:            str
    currency:        str
    title:           str              # Titre court lisible (ex : "US CPI")
    event_type:      str
    importance:      str              # "high" | "medium" | "low"
    timing_label:    str              # "Today" | "Tomorrow" | "Wednesday" | "Thu May 7"
    is_today:        bool
    is_cb_speech:    bool
    speaker:         Optional[str]   # "Powell" | "Lagarde" | None
    why_it_matters:  str
    market_focus:    str
    insight:         str
    expected:        Optional[str] = None
    note:            Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — NORMALISATION DE L'IMPORTANCE
# ══════════════════════════════════════════════════════════════════════════════

# Toutes les variantes possibles → "high" | "medium" | "low"
_IMPORTANCE_MAP: Dict[str, str] = {
    # Anglais
    "high":         "high",
    "medium":       "medium",
    "low":          "low",
    # Majuscules
    "high":         "high",
    "medium":       "medium",
    "low":          "low",
    # Français
    "haute":        "high",
    "fort":         "high",
    "fort_positif": "high",
    "forte":        "high",
    "moyenne":      "medium",
    "moyen":        "medium",
    "modere":       "medium",
    "modéré":       "medium",
    "faible":       "low",
    # Valeurs numériques (parfois utilisées)
    "3":            "high",
    "2":            "medium",
    "1":            "low",
}

def normalize_importance(raw: Optional[str]) -> str:
    """
    Convertit n'importe quelle valeur d'importance en "high" | "medium" | "low".
    Case-insensitive. Retourne "low" si la valeur est inconnue ou manquante.
    """
    if not raw:
        return "low"
    return _IMPORTANCE_MAP.get(raw.strip().lower(), "low")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — DÉTECTION DES DISCOURS CB
# ══════════════════════════════════════════════════════════════════════════════

# Types qui sont des réunions officielles — jamais des speeches
_CB_MEETING_TYPES = frozenset({
    "FOMC", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ", "INTEREST_RATE"
})

# (mots-clés dans le titre, nom normalisé du speaker)
# Ordre du plus spécifique au plus générique
_SPEAKER_KEYWORDS: List[Tuple[Tuple[str, ...], str]] = [
    # Présidents de banques centrales
    (("powell",),                                            "Powell"),
    (("lagarde",),                                           "Lagarde"),
    (("bailey",),                                            "Bailey"),
    (("ueda",),                                              "Ueda"),
    (("macklem",),                                           "Macklem"),
    (("bullock",),                                           "Bullock"),
    (("orr",),                                               "Orr"),
    (("schlegel", "jordan"),                                 "SNB Chief"),
    # Membres du board
    (("nagel", "lane", "de guindos", "schnabel"),            "ECB Official"),
    (("pill", "mann", "dhingra", "haskel", "ramsden"),       "BOE Official"),
    (("waller", "jefferson", "cook", "daly", "kashkari",
      "bostic", "barkin", "williams", "mester", "goolsbee"), "Fed Official"),
    # Mots-clés génériques indiquant un discours CB
    (("speaks", "speech", "remarks", "testimony",
      "press conference", "presser", "statement",
      "economic outlook", "monetary policy"),                 None),
]

# Mots-clés de banques centrales dans le titre → devise associée
_CB_TITLE_KEYWORDS: Dict[str, str] = {
    "fed":      "USD",
    "fomc":     "USD",
    "federal reserve": "USD",
    "ecb":      "EUR",
    "european central bank": "EUR",
    "boe":      "GBP",
    "bank of england": "GBP",
    "boj":      "JPY",
    "bank of japan": "JPY",
    "snb":      "CHF",
    "swiss national": "CHF",
    "boc":      "CAD",
    "bank of canada": "CAD",
    "rba":      "AUD",
    "reserve bank of australia": "AUD",
    "rbnz":     "NZD",
    "reserve bank of new zealand": "NZD",
}


def detect_cb_speech(title: str, event_type: str) -> Tuple[bool, Optional[str]]:
    """
    Détecte si un événement est un discours de banquier central.
    Retourne (is_cb_speech: bool, speaker: Optional[str]).

    Les réunions officielles (FOMC, ECB…) ne sont pas considérées comme des speeches.
    """
    if event_type in _CB_MEETING_TYPES:
        return False, None

    title_lower = title.lower()

    for keywords, speaker in _SPEAKER_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return True, speaker

    return False, None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — PRIORITÉ DES TYPES D'ÉVÉNEMENTS
# ══════════════════════════════════════════════════════════════════════════════

_EVENT_PRIORITY: Dict[str, int] = {
    "FOMC": 1, "ECB": 1, "BOE": 1, "BOJ": 1,
    "SNB": 1, "BOC": 1, "RBA": 1, "RBNZ": 1, "INTEREST_RATE": 1,
    "CPI": 2, "NFP": 2,
    "GDP": 3, "PPI": 3, "UNEMPLOYMENT": 3,
    "ISM": 4, "PMI_COMPOSITE": 4, "PMI_MFG": 4, "PMI_SERVICES": 4,
    "RETAIL_SALES": 5, "CONSUMER_CONFIDENCE": 5,
    "TRADE_BALANCE": 6, "INDUSTRIAL_PRODUCTION": 6,
    "OTHER": 9,
}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — BIBLIOTHÈQUE DE CONTENU PÉDAGOGIQUE
# ══════════════════════════════════════════════════════════════════════════════
#
# Structure : CONTENT[event_type][currency | "default"]
#             → {"why": ..., "focus": ..., "insight": ...}
#
# Priorité : currency spécifique > "default"

_C = Dict[str, Dict[str, str]]

CONTENT: Dict[str, _C] = {

    # ── Banques centrales ──────────────────────────────────────────────────────
    "FOMC": {"USD": {
        "why":     "The Fed sets the benchmark rate that shapes borrowing costs across the entire global economy",
        "focus":   "Rate decision, statement wording & tone of the press conference",
        "insight": "The decision itself is usually priced in — the statement and presser are what move markets",
    }},
    "ECB": {"EUR": {
        "why":     "The ECB controls monetary policy for 20 countries sharing the euro",
        "focus":   "Rate path, growth outlook & inflation mandate balance",
        "insight": "Watch the press conference — Lagarde's tone reveals more than the decision",
    }},
    "BOE": {"GBP": {
        "why":     "BOE decisions reflect the UK's challenge of sticky inflation against slowing growth",
        "focus":   "Vote split among the 9 MPC members — each vote signals future direction",
        "insight": "A split vote often points to the next decision before markets fully price it in",
    }},
    "BOJ": {"JPY": {
        "why":     "The BOJ is the last major central bank still navigating policy normalization",
        "focus":   "Yield curve signals, rate hike hints & yen stability considerations",
        "insight": "Any hawkish surprise from the BOJ ripples across global bond and FX markets instantly",
    }},
    "SNB": {"CHF": {
        "why":     "The SNB manages one of the world's most defensive safe-haven currencies",
        "focus":   "Inflation control, exchange rate signals & intervention language",
        "insight": "The SNB acts outside scheduled meetings when needed — when they move, they mean it",
    }},
    "BOC": {"CAD": {
        "why":     "BOC policy reflects an economy highly sensitive to oil prices and US trade conditions",
        "focus":   "Rate path commentary & any reference to commodity-driven growth divergence",
        "insight": "Canada often mirrors the Fed with a lag — the BOC signals when that divergence changes",
    }},
    "RBA": {"AUD": {
        "why":     "RBA decisions shape carry dynamics for AUD — one of the highest-yielding major currencies",
        "focus":   "Balance between persistent inflation and household financial stress from high rates",
        "insight": "RBA is navigating a unique late-cycle position — forward guidance matters more than the decision",
    }},
    "RBNZ": {"NZD": {
        "why":     "RBNZ is among the most proactive central banks globally — it moves before others",
        "focus":   "Rate cut pace and confidence that inflation has returned to the 1-3% target band",
        "insight": "RBNZ was first to hike and first to cut — NZD volatility reflects the full cycle",
    }},
    "INTEREST_RATE": {"default": {
        "why":     "Central bank rate decisions directly influence currency valuations and capital flows",
        "focus":   "The decision itself, statement language & any forward guidance",
        "insight": "Markets price future moves, not current ones — focus on the language, not the number",
    }},

    # ── Inflation ──────────────────────────────────────────────────────────────
    "CPI": {
        "USD": {
            "why":     "US inflation drives Fed rate expectations — and therefore USD strength globally",
            "focus":   "Core CPI vs headline — the Fed excludes food and energy to see the trend",
            "insight": "Markets react to the surprise vs expectations, not to the data itself",
        },
        "EUR": {
            "why":     "Eurozone inflation determines whether the ECB can continue its easing cycle",
            "focus":   "Services inflation — the stickiest component and the ECB's key concern",
            "insight": "If services inflation stays elevated, the ECB easing pace slows or pauses",
        },
        "GBP": {
            "why":     "UK inflation has been the most persistent among G10 economies since 2022",
            "focus":   "Services CPI — the BOE's primary metric for rate cut timing",
            "insight": "If UK services inflation stays high, BOE rate cuts get pushed further out",
        },
        "JPY": {
            "why":     "Japan CPI validates the BOJ's move away from 30 years of ultra-loose policy",
            "focus":   "Core CPI ex-fresh food — the BOJ's preferred inflation measure",
            "insight": "Rising Japanese inflation is structurally significant — it breaks a decades-long narrative",
        },
        "default": {
            "why":     "Inflation data shapes central bank timing on rate changes",
            "focus":   "Actual vs expected — the surprise direction is what the market reacts to",
            "insight": "A single hot print can delay rate cuts by several months",
        },
    },
    "PPI": {
        "USD": {
            "why":     "Producer prices in the US lead consumer inflation by 1 to 3 months",
            "focus":   "Core PPI — pipeline pressure on future CPI readings",
            "insight": "Hot PPI today often shows up in CPI data 1-3 months later",
        },
        "default": {
            "why":     "Producer prices anticipate consumer inflation — what costs more to make costs more to buy",
            "focus":   "Pipeline inflation pressure on future consumer price readings",
            "insight": "PPI is an early warning on CPI direction — watch the trend, not one print",
        },
    },

    # ── Emploi ─────────────────────────────────────────────────────────────────
    "NFP": {
        "USD": {
            "why":     "US non-farm payrolls is the single most-watched macro release in global markets",
            "focus":   "Headline jobs + unemployment rate + average hourly earnings — all three matter",
            "insight": "A strong NFP reduces urgency for Fed rate cuts — markets reprice immediately",
        },
        "CAD": {
            "why":     "Canada employment mirrors US labor conditions with added sensitivity to oil and trade",
            "focus":   "Jobs added vs expectations — often moves CAD alongside the USD NFP reaction",
            "insight": "Canadian and US jobs data release simultaneously — double volatility for CAD",
        },
        "default": {
            "why":     "Employment data reflects labor market health and forward consumer spending capacity",
            "focus":   "Net jobs change and unemployment rate direction together",
            "insight": "Employment is a lagging indicator — but markets treat it as if it leads",
        },
    },
    "UNEMPLOYMENT": {
        "default": {
            "why":     "Unemployment is part of the dual mandate for most major central banks",
            "focus":   "Rate trend — whether unemployment is rising or falling over multiple months",
            "insight": "A rising unemployment rate changes central bank sentiment faster than any other data",
        },
    },

    # ── Croissance ─────────────────────────────────────────────────────────────
    "GDP": {
        "USD": {
            "why":     "US GDP defines the global economic cycle and shapes long-term USD flow trends",
            "focus":   "Growth trend across quarters — two consecutive negatives signal technical recession",
            "insight": "One quarterly print rarely shifts the narrative — the trend across quarters does",
        },
        "EUR": {
            "why":     "Eurozone GDP reveals whether the economic slowdown is deepening or stabilizing",
            "focus":   "Germany and France divergence — the two largest euro economies often split",
            "insight": "Weak European growth supports ECB easing — but weakness may already be priced in",
        },
        "default": {
            "why":     "GDP growth defines the phase of the economic cycle for this currency",
            "focus":   "Quarter-on-quarter direction — is growth accelerating, slowing or reversing",
            "insight": "Central banks look through one quarter — sustained weakness changes policy stance",
        },
    },

    # ── PMI / Activité ─────────────────────────────────────────────────────────
    "PMI_COMPOSITE": {
        "default": {
            "why":     "Composite PMI surveys business managers — they see conditions before hard data does",
            "focus":   "Above 50 = expansion, below 50 = contraction — the threshold is the reference point",
            "insight": "PMI leads GDP by 1-2 months — it's one of the earliest reliable signals available",
        },
    },
    "PMI_MFG": {
        "default": {
            "why":     "Manufacturing PMI reflects global trade demand and industrial activity",
            "focus":   "New orders sub-index — the most forward-looking component in the survey",
            "insight": "Manufacturing weakness shows up in PMI months before official output data confirms it",
        },
    },
    "PMI_SERVICES": {
        "default": {
            "why":     "Services PMI measures the now-dominant sector in most developed economies",
            "focus":   "Activity vs new orders — the momentum comparison tells the near-term direction",
            "insight": "Strong services PMI often coincides with services inflation — relevant for CB timing",
        },
    },
    "ISM": {
        "USD": {
            "why":     "ISM is the benchmark business activity survey for the world's largest economy",
            "focus":   "New orders sub-index — the component that drives the real market reaction",
            "insight": "ISM new orders predict economic momentum 3 months out better than the headline",
        },
        "default": {
            "why":     "ISM captures real business conditions across key industrial and service sectors",
            "focus":   "Headline vs new orders — sustained sub-50 readings precede recession risk",
            "insight": "ISM below 50 for 3+ months is a reliable early signal of growth deterioration",
        },
    },

    # ── Consommation / Confiance ────────────────────────────────────────────────
    "RETAIL_SALES": {
        "USD": {
            "why":     "US consumer spending drives roughly 70% of GDP — retail sales is the pulse",
            "focus":   "Core retail sales ex-autos — the cleaner signal on underlying consumer momentum",
            "insight": "Strong retail sales reduce pressure on the Fed to cut — the consumer is still fine",
        },
        "default": {
            "why":     "Consumer spending is the backbone of GDP growth in all developed economies",
            "focus":   "Core retail sales trend — the month-on-month direction matters more than one print",
            "insight": "Declining retail sales reduce the justification for keeping rates restrictive",
        },
    },
    "CONSUMER_CONFIDENCE": {
        "default": {
            "why":     "Confidence surveys predict spending behavior — consumers act on how they feel",
            "focus":   "Current conditions vs future expectations index — the expectations part is forward-looking",
            "insight": "Confidence typically falls before spending falls — treat it as an early warning signal",
        },
    },

    # ── Commerce / Production ───────────────────────────────────────────────────
    "TRADE_BALANCE": {
        "default": {
            "why":     "Trade balance reflects the underlying currency demand from import and export flows",
            "focus":   "Whether the deficit is widening or narrowing — the trend matters more than the level",
            "insight": "Persistent trade deficits create structural long-term pressure on a currency",
        },
    },
    "INDUSTRIAL_PRODUCTION": {
        "default": {
            "why":     "Industrial output reflects manufacturing health and energy consumption patterns",
            "focus":   "Month-on-month trend — is production accelerating or slowing",
            "insight": "Weak industrial production typically follows weak PMI data by 1 to 2 months",
        },
    },

    # ── Fallback ────────────────────────────────────────────────────────────────
    "OTHER": {"default": {
        "why":     "This event has been identified as potentially market-moving this week",
        "focus":   "Actual vs expected deviation — the direction of the surprise",
        "insight": "The market reaction depends entirely on the current macro narrative context",
    }},
}


# ── Contenu pour les discours CB (par speaker) ─────────────────────────────────

SPEAKER_CONTENT: Dict[str, Dict[str, str]] = {
    "Powell": {
        "why":     "Fed Chair Powell's comments shape global rate expectations more than most data releases",
        "focus":   "Tone on inflation, labor market health and any shift in forward guidance language",
        "insight": "One carefully chosen word from Powell can reprice the entire rate curve in minutes",
    },
    "Lagarde": {
        "why":     "ECB President Lagarde sets the narrative for European monetary policy between decisions",
        "focus":   "Confidence in inflation returning to target and hints on the pace of easing",
        "insight": "Lagarde's language on 'data dependence' is the clearest signal for the next ECB move",
    },
    "Bailey": {
        "why":     "BOE Governor Bailey navigates the UK's inflation-vs-growth dilemma in public",
        "focus":   "Signals on internal MPC disagreement and pace of potential rate cuts",
        "insight": "Bailey tends to prepare markets before decisions — his tone matters more than his words",
    },
    "Ueda": {
        "why":     "BOJ Governor Ueda manages the most globally watched policy normalization in decades",
        "focus":   "Wage growth confidence, yen stability and yield curve exit pace signals",
        "insight": "Ueda moves the yen more reliably than any data release — every word is calibrated",
    },
    "Macklem": {
        "why":     "BOC Governor Macklem provides guidance for a commodity-sensitive, trade-exposed economy",
        "focus":   "Oil sensitivity, housing market stress and divergence from Fed policy signals",
        "insight": "Canada often front-runs the Fed — Macklem's speeches signal the timing of divergence",
    },
    "Bullock": {
        "why":     "RBA Governor Bullock guides one of the highest-yielding major currencies",
        "focus":   "Balance between household financial stress and inflation still above the 2-3% target",
        "insight": "RBA is late-cycle — Bullock's tone reveals how close the next policy pivot is",
    },
    "Orr": {
        "why":     "RBNZ Governor Orr leads one of the world's most proactive central banks",
        "focus":   "Rate cut pace and confidence that inflation has returned to the 1-3% target band",
        "insight": "RBNZ moves fast and early — Orr speeches often confirm what markets already suspect",
    },
    "SNB Chief": {
        "why":     "SNB leadership communicates rare but significant signals on rates and the franc",
        "focus":   "Inflation outlook and any currency intervention signals",
        "insight": "The SNB acts decisively and infrequently — any speech carries outsized weight",
    },
    "ECB Official": {
        "why":     "ECB board members shape the market's understanding of internal policy views",
        "focus":   "Whether their view diverges from the consensus — hawks vs doves within the Council",
        "insight": "Internal ECB disagreement often surfaces in speeches before it appears in decisions",
    },
    "BOE Official": {
        "why":     "MPC members express individual views that together form the BOE vote count",
        "focus":   "Their stance vs the consensus — leaning toward faster or slower cuts",
        "insight": "Tracking individual MPC members helps anticipate the next vote split",
    },
    "Fed Official": {
        "why":     "Fed governors and regional presidents collectively calibrate market rate expectations",
        "focus":   "Whether their view aligns with or diverges from the Chair's latest guidance",
        "insight": "Regional Fed presidents rotate voting rights — knowing who votes this year matters",
    },
    None: {
        "why":     "Central bank officials communicate policy shifts outside of scheduled decisions",
        "focus":   "Any deviation from the current official stance on rates or the outlook",
        "insight": "Speeches between meetings deliberately prepare markets for upcoming decisions",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — GÉNÉRATION DES TITRES COURTS
# ══════════════════════════════════════════════════════════════════════════════

_CB_SHORT_TITLES: Dict[str, str] = {
    "FOMC":          "Fed Rate Decision",
    "ECB":           "ECB Rate Decision",
    "BOE":           "BOE Rate Decision",
    "BOJ":           "BOJ Rate Decision",
    "SNB":           "SNB Rate Decision",
    "BOC":           "BOC Rate Decision",
    "RBA":           "RBA Rate Decision",
    "RBNZ":          "RBNZ Rate Decision",
}

_TYPE_SHORT_TITLES: Dict[str, str] = {
    "CPI":                   "{currency} CPI",
    "PPI":                   "{currency} PPI",
    "NFP":                   "{currency} NFP",
    "UNEMPLOYMENT":          "{currency} Unemployment",
    "GDP":                   "{currency} GDP",
    "PMI_COMPOSITE":         "{currency} PMI",
    "PMI_MFG":               "{currency} Manufacturing PMI",
    "PMI_SERVICES":          "{currency} Services PMI",
    "ISM":                   "US ISM",
    "RETAIL_SALES":          "{currency} Retail Sales",
    "CONSUMER_CONFIDENCE":   "{currency} Consumer Confidence",
    "TRADE_BALANCE":         "{currency} Trade Balance",
    "INDUSTRIAL_PRODUCTION": "{currency} Industrial Output",
    "INTEREST_RATE":         "{currency} Rate Decision",
}


def make_short_title(
    title: str,
    currency: str,
    event_type: str,
    is_cb_speech: bool,
    speaker: Optional[str],
) -> str:
    """Produit un titre court adapté à l'interface Lucid."""
    # CB speech avec speaker connu
    if is_cb_speech and speaker:
        return f"{speaker} Speech"
    if is_cb_speech:
        return "CB Speech"

    # Réunion CB officielle
    if event_type in _CB_SHORT_TITLES:
        return _CB_SHORT_TITLES[event_type]

    # Type connu avec devise
    template = _TYPE_SHORT_TITLES.get(event_type)
    if template:
        return template.replace("{currency}", currency)

    # Fallback : titre tronqué
    return title[:48] + "…" if len(title) > 48 else title


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — TIMING
# ══════════════════════════════════════════════════════════════════════════════

def compute_timing(event_date: str, today_str: str) -> Tuple[str, bool]:
    """
    Calcule le label de timing et is_today.
    Retourne ("Today" | "Tomorrow" | "Wednesday" | "Fri May 9", is_today: bool).
    """
    if not event_date:
        return "This week", False

    try:
        ev = datetime.strptime(event_date, "%Y-%m-%d")
        td = datetime.strptime(today_str, "%Y-%m-%d")
        delta = (ev - td).days

        if delta == 0:
            return "Today", True
        elif delta == 1:
            return "Tomorrow", False
        elif 2 <= delta <= 6:
            return ev.strftime("%A"), False          # ex : "Wednesday"
        elif delta > 6:
            return ev.strftime("%a %b %-d"), False   # ex : "Thu May 14"
        else:
            return "Past", False                     # événement passé
    except ValueError:
        return "This week", False


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — RÉSOLUTION DU CONTENU
# ══════════════════════════════════════════════════════════════════════════════

def resolve_content(
    event_type: str,
    currency: str,
    is_cb_speech: bool,
    speaker: Optional[str],
) -> Tuple[str, str, str]:
    """
    Retourne (why_it_matters, market_focus, insight) pour l'événement.

    Ordre de priorité :
    1. Speaker connu  → SPEAKER_CONTENT[speaker]
    2. Speech générique → SPEAKER_CONTENT[None]
    3. EVENT[type][currency]
    4. EVENT[type]["default"]
    5. EVENT["OTHER"]["default"]
    """
    if is_cb_speech:
        block = SPEAKER_CONTENT.get(speaker, SPEAKER_CONTENT[None])
        return block["why"], block["focus"], block["insight"]

    type_block = CONTENT.get(event_type) or CONTENT["OTHER"]

    block = (
        type_block.get(currency)
        or type_block.get("default")
        or CONTENT["OTHER"]["default"]
    )
    return block["why"], block["focus"], block["insight"]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — PARSING DU JSON
# ══════════════════════════════════════════════════════════════════════════════

def parse_raw_event(raw: dict) -> Optional[RawEvent]:
    """
    Parse un dict brut (depuis JSON) en RawEvent.
    Retourne None si les champs essentiels sont manquants.
    Ne plante jamais sur un champ absent ou mal typé.
    """
    try:
        date       = str(raw.get("date", "") or "").strip()
        currency   = str(raw.get("currency", "") or "").strip().upper()
        event_type = str(raw.get("event_type", "OTHER") or "OTHER").strip().upper()
        title      = str(raw.get("title", "") or "").strip()

        if not date or not currency or not title:
            logger.debug(f"Événement ignoré (champs manquants) : {raw}")
            return None

        importance = normalize_importance(raw.get("importance"))

        return RawEvent(
            date=date,
            currency=currency,
            event_type=event_type,
            title=title,
            importance=importance,
            expected=raw.get("expected"),
            previous=raw.get("previous"),
            actual=raw.get("actual"),
            note=raw.get("note"),
        )
    except Exception as exc:
        logger.debug(f"Erreur parse événement : {exc} | raw={raw}")
        return None


def load_events_from_json(path: str) -> List[RawEvent]:
    """
    Charge et parse les événements depuis un fichier JSON.
    Le fichier doit contenir une liste de dicts.
    Retourne une liste vide si le fichier est absent ou invalide.
    """
    filepath = Path(path)
    if not filepath.exists():
        logger.error(f"Fichier introuvable : {path}")
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"Erreur lecture {path} : {exc}")
        return []

    if not isinstance(data, list):
        logger.error(f"Format invalide : {path} doit contenir une liste JSON")
        return []

    events = []
    for item in data:
        if isinstance(item, dict):
            ev = parse_raw_event(item)
            if ev is not None:
                events.append(ev)

    logger.info(f"Chargé {len(events)}/{len(data)} événements depuis {path}")
    return events


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — MOTEUR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

MAX_TODAY  = 5   # Limite today
MAX_WEEKLY = 7   # Limite weekly (hors today)


class LucidEventEngine:
    """
    Moteur de transformation des événements macro en alertes Lucid.

    Usage :
        engine = LucidEventEngine()
        today_events, weekly_events = engine.build(raw_events)

    Filtrage :
        - Garde : importance "high" ou "medium" + réunions CB + speeches CB
        - Écarte : importance "low" sans caractère CB
        - Limite  : 5 today, 7 weekly max

    Tri :
        - Date ASC
        - Priorité event_type ASC
        - Importance DESC
    """

    def __init__(self, today_str: Optional[str] = None):
        """
        today_str : date au format "YYYY-MM-DD". Si None, utilise la date UTC du jour.
        """
        self.today_str = today_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def build(
        self,
        raw_events: List[RawEvent],
    ) -> Tuple[List[LucidEvent], List[LucidEvent]]:
        """
        Construit les listes today_events et weekly_events.

        Returns:
            (today_events, weekly_events)
            today_events  : événements du jour (max MAX_TODAY)
            weekly_events : événements du reste de la semaine (max MAX_WEEKLY)
        """
        all_lucid: List[LucidEvent] = []

        for raw in raw_events:
            try:
                lucid = self._convert(raw)
                if lucid is not None:
                    all_lucid.append(lucid)
            except Exception as exc:
                logger.debug(f"Erreur conversion {raw.title!r} : {exc}")

        # Tri global
        all_lucid.sort(key=lambda e: (
            e.date,
            _EVENT_PRIORITY.get(e.event_type, 9),
            {"high": 0, "medium": 1, "low": 2}.get(e.importance, 9),
        ))

        # Séparation today / weekly avec limites
        today_events:  List[LucidEvent] = []
        weekly_events: List[LucidEvent] = []

        for ev in all_lucid:
            if ev.is_today:
                if len(today_events) < MAX_TODAY:
                    today_events.append(ev)
            else:
                if len(weekly_events) < MAX_WEEKLY:
                    weekly_events.append(ev)

        logger.info(
            f"LucidEventEngine : {len(today_events)} today, "
            f"{len(weekly_events)} weekly "
            f"(source : {len(raw_events)} événements bruts)"
        )
        return today_events, weekly_events

    def _convert(self, raw: RawEvent) -> Optional[LucidEvent]:
        """
        Convertit un RawEvent en LucidEvent.
        Retourne None si l'événement ne mérite pas d'alerte Lucid.
        """
        is_meeting  = raw.event_type in _CB_MEETING_TYPES
        is_cb_speech, speaker = detect_cb_speech(raw.title, raw.event_type)

        # Filtre : écarter les événements peu importants non-CB
        if raw.importance == "low" and not is_meeting and not is_cb_speech:
            return None

        # Exclure les événements passés
        timing_label, is_today = compute_timing(raw.date, self.today_str)
        if timing_label == "Past":
            return None

        why, focus, insight = resolve_content(
            raw.event_type, raw.currency, is_cb_speech, speaker
        )

        short_title = make_short_title(
            raw.title, raw.currency, raw.event_type, is_cb_speech, speaker
        )

        return LucidEvent(
            date=raw.date,
            currency=raw.currency,
            title=short_title,
            event_type=raw.event_type,
            importance=raw.importance,
            timing_label=timing_label,
            is_today=is_today,
            is_cb_speech=is_cb_speech,
            speaker=speaker,
            why_it_matters=why,
            market_focus=focus,
            insight=insight,
            expected=raw.expected,
            note=raw.note,
        )

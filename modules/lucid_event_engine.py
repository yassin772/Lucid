"""
modules/lucid_event_engine.py — Moteur Lucid Events (V1)

Transforme les événements macro bruts (UpcomingEvent) en alertes pédagogiques
pour l'application Lucid, destinée aux traders retail.

Philosophie :
  - Pas de signal, pas de buy/sell, pas de setup
  - Uniquement : pourquoi cet événement compte, sur quoi le marché va se concentrer,
    et quelle leçon macro retenir
  - "Moins d'information, plus de compréhension"

Pipeline :
  UpcomingEvent (existant)
    → filtre importance + CB speeches
    → résolution contenu par event_type + devise + speaker
    → calcul timing_label (Today / Tomorrow / Wed May 7)
    → limites : 5 events Today, 7 events total
    → List[LucidEvent]

Intégration :
  - Appelé depuis build_weekly_summary() après l'étape upcoming_events
  - Résultat stocké dans WeeklyMacroSummary.lucid_events
  - N'affecte pas le rapport Discord existant
  - Destiné à l'API web Lucid

Compatibilité :
  - Python 3.10+
  - Pas de pandas / numpy / requests
  - Robuste aux champs manquants (try/except par événement)
  - Case-insensitive sur les champs importance / impact
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from modules.lucid_compliance import DISCLAIMER, assert_lucid_object_clean, clean_lucid_text

try:
    from modules.calendar_provider import UpcomingEvent
except Exception:
    UpcomingEvent = Any

logger = logging.getLogger(__name__)


# ─── Limites produit ───────────────────────────────────────────────────────────

MAX_TODAY_EVENTS:  int = 5   # Max alertes "Today" affichées
MAX_WEEKLY_EVENTS: int = 7   # Max alertes totales affichées
LUCID_DISCLAIMER = DISCLAIMER


# ─── Priorité des types d'événements (tri interne) ────────────────────────────

EVENT_PRIORITY: Dict[str, int] = {
    "FOMC": 1, "ECB": 1, "BOE": 1, "BOJ": 1,
    "SNB": 1, "BOC": 1, "RBA": 1, "RBNZ": 1,
    "INTEREST_RATE": 1,
    "CPI": 2, "NFP": 2,
    "GDP": 3, "PPI": 3, "UNEMPLOYMENT": 3,
    "ISM": 4, "PMI_COMPOSITE": 4, "PMI_MFG": 4, "PMI_SERVICES": 4,
    "RETAIL_SALES": 5, "CONSUMER_CONFIDENCE": 5,
    "TRADE_BALANCE": 6, "INDUSTRIAL_PRODUCTION": 6,
    "OTHER": 9,
}

# ─── Détection des discours CB ────────────────────────────────────────────────

# Chaque tuple : (liste de mots-clés dans le titre, nom du speaker normalisé)
# Ordre décroissant de spécificité
SPEAKER_KEYWORDS: List[Tuple[List[str], str]] = [
    (["powell"],                          "Powell"),
    (["lagarde"],                         "Lagarde"),
    (["bailey"],                          "Bailey"),
    (["ueda"],                            "Ueda"),
    (["macklem"],                         "Macklem"),
    (["bullock"],                         "Bullock"),
    (["orr"],                             "Orr"),
    (["schlegel", "jordan"],              "SNB Chief"),
    (["nagel", "lane", "de guindos"],     "ECB Official"),
    (["pill", "mann", "dhingra"],         "BOE Official"),
    (["waller", "jefferson", "cook",
      "daly", "kashkari", "bostic",
      "barkin", "williams", "mester"],    "Fed Official"),
    # Titres génériques CB speech
    (["speaks", "speech", "remarks",
      "testimony", "presser",
      "press conference", "statement"],   None),   # None = speaker inconnu
]

# Types d'événements qui sont toujours des réunions CB (jamais un speech)
CB_MEETING_TYPES = {
    "FOMC", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ", "INTEREST_RATE"
}

# ─── Contenu pédagogique par event_type + devise ──────────────────────────────

_EC = Dict[str, Tuple[str, str, str]]

EVENT_CONTENT: Dict[str, _EC] = {
    "FOMC": {
        "USD": (
            "The Fed can change how attractive the dollar feels",
            "Whether rates stay high and how the Fed describes the economy",
            "The tone matters because it shapes what investors expect next",
        ),
    },
    "ECB": {
        "EUR": (
            "The ECB sets the rate backdrop for the euro area",
            "Whether slow growth makes the ECB more careful",
            "The euro needs stronger economic support to improve",
        ),
    },
    "BOE": {
        "GBP": (
            "The BOE is balancing high prices with weaker growth",
            "Whether inflation keeps the bank cautious",
            "The pound depends on which concern looks bigger",
        ),
    },
    "BOJ": {
        "JPY": (
            "The BOJ can quickly change how investors view the yen",
            "Whether Japan is ready for higher rates",
            "The yen is sensitive to even small policy changes",
        ),
    },
    "SNB": {
        "CHF": (
            "The SNB matters because the franc is a defensive currency",
            "Inflation, rates, and any comments about the franc",
            "Clear SNB language can shift demand for the franc",
        ),
    },
    "BOC": {
        "CAD": (
            "The BOC reads an economy tied closely to the US and oil",
            "Growth, inflation, and how cautious the bank sounds",
            "The Canadian dollar often follows oil and US demand",
        ),
    },
    "RBA": {
        "AUD": (
            "The RBA affects a currency linked to global growth",
            "Inflation and signs of pressure on households",
            "The Australian dollar needs confidence in growth to stay supported",
        ),
    },
    "RBNZ": {
        "NZD": (
            "The RBNZ can move expectations for the New Zealand dollar",
            "Inflation and whether the economy is slowing",
            "The kiwi is sensitive when global confidence changes",
        ),
    },
    "INTEREST_RATE": {
        "default": (
            "Rate decisions shape how attractive a currency feels",
            "The decision and the wording around future policy",
            "Simple language from the central bank can matter more than the rate itself",
        ),
    },
    "CPI": {
        "default": (
            "Inflation tells central banks how much pressure remains",
            "Whether prices are cooling or staying sticky",
            "Slower inflation can reduce support for a currency",
        ),
    },
    "PPI": {
        "default": (
            "Producer prices can show early pressure before consumers feel it",
            "Whether business costs are rising or cooling",
            "Lower price pressure gives central banks more room to wait",
        ),
    },
    "NFP": {
        "USD": (
            "US jobs data shapes the dollar because it guides the Fed",
            "Job growth, unemployment, and wage pressure",
            "A strong labor market can keep the dollar supported",
        ),
        "default": (
            "Jobs data shows whether households can keep spending",
            "Whether employment is improving or weakening",
            "Weak jobs data can make a central bank more cautious",
        ),
    },
    "UNEMPLOYMENT": {
        "default": (
            "Unemployment shows whether the economy is losing strength",
            "Whether joblessness is rising or falling",
            "A weaker labor market can weigh on the currency",
        ),
    },
    "GDP": {
        "default": (
            "GDP shows whether the economy is growing or slowing",
            "Whether growth is strong enough to support the currency",
            "Weak growth can make a currency feel less attractive",
        ),
    },
    "PMI_COMPOSITE": {
        "default": (
            "PMI surveys show how businesses feel before official data arrives",
            "Whether activity is improving or slowing",
            "Better business activity can support the currency",
        ),
    },
    "PMI_MFG": {
        "default": (
            "Manufacturing PMI shows pressure in factories and global demand",
            "New orders and whether production is slowing",
            "Weak factory activity can weigh on growth expectations",
        ),
    },
    "PMI_SERVICES": {
        "default": (
            "Services PMI tracks the biggest part of many economies",
            "Whether service activity is holding up",
            "Strong services data can keep inflation concerns alive",
        ),
    },
    "ISM": {
        "default": (
            "ISM shows how US businesses are seeing the economy",
            "New orders and business activity",
            "It helps explain whether US growth still has support",
        ),
    },
    "RETAIL_SALES": {
        "default": (
            "Retail sales show whether consumers are still spending",
            "Whether spending is rising or cooling",
            "Consumer strength can support growth and the currency",
        ),
    },
    "CONSUMER_CONFIDENCE": {
        "default": (
            "Confidence affects how willing people are to spend",
            "Whether households feel better or worse about the economy",
            "Falling confidence can point to weaker spending ahead",
        ),
    },
    "TRADE_BALANCE": {
        "default": (
            "Trade balance shows whether foreign demand supports the currency",
            "Whether exports are stronger than imports",
            "A healthier external position can help the currency",
        ),
    },
    "INDUSTRIAL_PRODUCTION": {
        "default": (
            "Industrial production shows whether factories are active",
            "Whether output is rising or slowing",
            "Weak production can add pressure to the growth picture",
        ),
    },
    "OTHER": {
        "default": (
            "This event may help explain this week's market mood",
            "Whether the result changes the current economic story",
            "The key is whether it makes the picture clearer",
        ),
    },
}

SPEAKER_CONTENT: Dict[str, Tuple[str, str, str]] = {
    "Powell": (
        "Powell's comments shape how investors read the Fed",
        "Inflation, jobs, and whether rates may stay high",
        "His tone can quickly change the dollar story",
    ),
    "Lagarde": (
        "Lagarde explains how the ECB sees growth and inflation",
        "Whether the ECB sounds worried about weak growth",
        "Her tone helps the market read the euro backdrop",
    ),
    "Bailey": (
        "Bailey explains how the BOE weighs inflation against growth",
        "Whether the BOE sounds more cautious",
        "His comments can clarify the pound's main driver",
    ),
    "Ueda": (
        "Ueda guides how investors understand Japan's policy path",
        "Whether Japan is moving closer to higher rates",
        "The yen reacts when BOJ language changes",
    ),
    "Macklem": (
        "Macklem explains how Canada is balancing growth and inflation",
        "Oil, housing, and how cautious the BOC sounds",
        "His comments help explain the Canadian dollar's backdrop",
    ),
    "Bullock": (
        "Bullock explains how Australia is handling inflation pressure",
        "Household pressure and whether rates need to stay high",
        "The Australian dollar depends on confidence in growth",
    ),
    "Orr": (
        "Orr explains how New Zealand is handling slower growth",
        "Inflation and whether the economy needs more support",
        "The kiwi reacts when the RBNZ tone changes",
    ),
    "SNB Chief": (
        "SNB comments matter because the franc is a defensive currency",
        "Inflation, rates, and any comments about the franc",
        "Clear SNB language can shift demand for the franc",
    ),
    "ECB Official": (
        "ECB officials help explain the bank's current thinking",
        "Growth, inflation, and how cautious the ECB sounds",
        "Their comments can clarify the euro story",
    ),
    "BOE Official": (
        "BOE officials show how the bank is reading the UK economy",
        "Inflation pressure and whether growth is weakening",
        "Their comments help explain the pound's backdrop",
    ),
    "Fed Official": (
        "Fed officials shape how investors read the US rate backdrop",
        "Inflation, jobs, and whether rates may stay high",
        "Their comments can change the dollar story",
    ),
    "default_speaker": (
        "Central bank speeches help explain policy between meetings",
        "Whether the speaker sounds worried about inflation or growth",
        "The comments matter when they make the outlook clearer",
    ),
}


# ─── Mapping importance UpcomingEvent → LucidEvent ────────────────────────────

_IMPORTANCE_MAP: Dict[str, str] = {
    "haute":   "high",
    "high":    "high",
    "moyenne": "medium",
    "medium":  "medium",
    "faible":  "low",
    "low":     "low",
}


# ─── Dataclass LucidEvent ──────────────────────────────────────────────────────

@dataclass
class LucidEvent:
    """
    Représentation pédagogique d'un événement macro pour l'application Lucid.

    Ne contient aucun signal directionnel, aucun buy/sell, aucun setup.
    Uniquement : contexte, focus marché, et leçon macro.
    """
    date:            str              # "2026-05-07"
    currency:        str              # "USD"
    title:           str              # "US CPI" — version courte lisible
    event_type:      str              # Type interne : "CPI", "FOMC", etc.
    importance:      str              # "high" | "medium" | "low" | "unknown"
    timing_label:    str              # "Today" | "Tomorrow" | "Wednesday" | "Thu May 7"
    is_today:        bool
    is_cb_speech:    bool
    speaker:         Optional[str]    # "Powell" | "Lagarde" | None
    why_it_matters:  str              # Phrase éducative — pourquoi ça compte
    market_focus:    str              # Sur quoi le marché va se concentrer
    insight:         str              # Leçon macro à retenir


# ─── Moteur principal ──────────────────────────────────────────────────────────

class LucidEventEngine:
    """
    Transforme des UpcomingEvent en LucidEvent pédagogiques.

    Usage :
        engine = LucidEventEngine()
        lucid_events = engine.build_lucid_events(upcoming_events)

    Limites appliquées :
        - MAX_TODAY_EVENTS  : max 5 événements "Today"
        - MAX_WEEKLY_EVENTS : max 7 événements au total
    """

    def build_lucid_events(
        self,
        upcoming_events: List[UpcomingEvent],
        today_str: Optional[str] = None,
    ) -> List[LucidEvent]:
        """
        Point d'entrée principal.

        Args:
            upcoming_events : liste brute d'UpcomingEvent depuis l'event_memory_service

        Returns:
            List[LucidEvent] triée par date puis par priorité d'event_type,
            limitée à MAX_TODAY_EVENTS aujourd'hui et MAX_WEEKLY_EVENTS au total.
        """
        if today_str is None:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()

        candidates: List[LucidEvent] = []

        for event in upcoming_events:
            try:
                lucid = self._convert(event, today_str)
                if lucid is not None:
                    try:
                        event_dt = datetime.strptime(lucid.date, "%Y-%m-%d").date()
                    except ValueError:
                        event_dt = None
                    if event_dt is not None and event_dt < today_dt:
                        continue
                    candidates.append(lucid)
            except Exception as exc:
                logger.debug(
                    f"LucidEventEngine : erreur conversion {getattr(event, 'title', '')!r} : {exc}"
                )

        # ── Tri : date ASC, puis priorité event_type ASC, puis importance ──
        candidates.sort(key=lambda e: (
            e.date,
            EVENT_PRIORITY.get(e.event_type, 9),
            {"high": 0, "medium": 1, "low": 2, "unknown": 9}.get(e.importance, 9),
        ))

        # ── Appliquer les limites ────────────────────────────────────────────
        result: List[LucidEvent] = []
        today_count = 0

        for event in candidates:
            if len(result) >= MAX_WEEKLY_EVENTS:
                break
            if event.is_today:
                if today_count >= MAX_TODAY_EVENTS:
                    continue
                today_count += 1
            result.append(event)

        logger.info(
            f"LucidEventEngine : {len(result)} événements Lucid générés "
            f"({today_count} today, {len(result) - today_count} this week)"
        )
        return result

    # ── Conversion interne ─────────────────────────────────────────────────────

    def _convert(
        self,
        event: UpcomingEvent,
        today_str: str,
    ) -> Optional[LucidEvent]:
        """
        Convertit un UpcomingEvent en LucidEvent.
        Retourne None si l'événement ne mérite pas d'alerte Lucid.
        """
        # ── Importance (case-insensitive) ────────────────────────────────────
        event_type = clean_lucid_text(getattr(event, "event_type", "") or "OTHER").upper()
        currency = clean_lucid_text(getattr(event, "currency", "") or "").upper()[:3] or "N/A"
        title = clean_lucid_text(getattr(event, "title", "") or "")
        raw_importance = (getattr(event, "importance", "") or "").strip().lower()
        importance = _IMPORTANCE_MAP.get(raw_importance, "unknown")

        # ── Détection CB speech ───────────────────────────────────────────────
        is_cb_speech, speaker = self._detect_cb_speech(title, event_type)
        is_meeting = event_type in CB_MEETING_TYPES and not is_cb_speech

        # ── Filtre : on garde uniquement les événements significatifs ─────────
        # Importance haute ou moyenne + réunions CB + discours CB importants
        if importance in ("low", "unknown") and not is_meeting and not is_cb_speech:
            return None

        # ── Timing ───────────────────────────────────────────────────────────
        event_date = clean_lucid_text(getattr(event, "date", "") or "")
        is_today = (event_date == today_str)
        timing_label = self._compute_timing(event_date, today_str)

        # ── Contenu pédagogique ───────────────────────────────────────────────
        why, focus, insight = self._resolve_content(
            event_type, currency, is_cb_speech, speaker
        )

        # ── Titre court ───────────────────────────────────────────────────────
        short_title = self._short_title(title, currency, event_type)

        lucid = LucidEvent(
            date=event_date,
            currency=currency,
            title=clean_lucid_text(short_title),
            event_type=event_type,
            importance=importance,
            timing_label=timing_label,
            is_today=is_today,
            is_cb_speech=is_cb_speech,
            speaker=speaker,
            why_it_matters=clean_lucid_text(why),
            market_focus=clean_lucid_text(focus),
            insight=clean_lucid_text(insight),
        )
        assert_lucid_object_clean(lucid)
        return lucid

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_cb_speech(title: str, event_type: str) -> Tuple[bool, Optional[str]]:
        """
        Détecte si le titre correspond à un discours de banquier central.
        Retourne (is_cb_speech, speaker_name_or_None).
        Les réunions officielles (FOMC, ECB…) ne sont pas des speeches.
        """
        title_lower = title.lower()

        for keywords, speaker in SPEAKER_KEYWORDS:
            for kw in keywords:
                if kw in title_lower:
                    return True, speaker

        return False, None

    @staticmethod
    def _compute_timing(event_date: str, today_str: str) -> str:
        """
        Calcule le label de timing lisible pour l'interface Lucid.
        - "Today" si c'est aujourd'hui
        - "Tomorrow" si c'est demain
        - "In X days" si c'est dans 2 à 6 jours
        - "May 8" si plus loin ou passé
        """
        if not event_date:
            return "Upcoming event"

        try:
            ev_dt = datetime.strptime(event_date, "%Y-%m-%d")
            today_dt = datetime.strptime(today_str, "%Y-%m-%d")
            delta = (ev_dt - today_dt).days

            if delta == 0:
                return "Today"
            elif delta == 1:
                return "Tomorrow"
            elif 2 <= delta <= 6:
                return f"In {delta} days"
            else:
                return f"{ev_dt.strftime('%B')} {ev_dt.day}"
        except ValueError:
            return "Upcoming event"

    @staticmethod
    def _resolve_content(
        event_type: str,
        currency: str,
        is_cb_speech: bool,
        speaker: Optional[str],
    ) -> Tuple[str, str, str]:
        """
        Résout le contenu pédagogique (why, focus, insight) pour l'événement.

        Ordre de priorité :
        1. Speaker connu (Powell, Lagarde…) → SPEAKER_CONTENT
        2. CB speech générique → SPEAKER_CONTENT["default_speaker"]
        3. EVENT_CONTENT[event_type][currency] → spécifique devise
        4. EVENT_CONTENT[event_type]["default"] → générique type
        5. EVENT_CONTENT["OTHER"]["default"] → fallback absolu
        """
        if is_cb_speech:
            key = speaker if (speaker and speaker in SPEAKER_CONTENT) else "default_speaker"
            return SPEAKER_CONTENT[key]

        type_content = EVENT_CONTENT.get(event_type) or EVENT_CONTENT.get("OTHER", {})

        # Essai devise spécifique
        if currency in type_content:
            return type_content[currency]

        # Fallback générique pour ce type
        if "default" in type_content:
            return type_content["default"]

        # Fallback absolu
        return EVENT_CONTENT["OTHER"]["default"]

    @staticmethod
    def _short_title(title: str, currency: str, event_type: str) -> str:
        """
        Produit un titre court adapté à l'interface Lucid.
        Ex : "Non-Farm Employment Change" → "US NFP"
             "Consumer Price Index (CPI) m/m" → "US CPI"
             "ECB Interest Rate Decision" → "ECB Decision"
        """
        # Never invent or generalize a source event title. In particular, a
        # central-bank event becomes "Rate Decision" only when the source title
        # itself says so.
        fallback = title[:50] if len(title) > 50 else title
        return fallback or f"Upcoming {currency} event"


# ─── Fonction utilitaire standalone ───────────────────────────────────────────

def build_lucid_events(upcoming_events: List[UpcomingEvent]) -> List[LucidEvent]:
    """
    Raccourci fonctionnel — instancie l'engine et retourne les events.
    Usage depuis build_weekly_summary() :
        from modules.lucid_event_engine import build_lucid_events
        lucid_events = build_lucid_events(upcoming_events)
    """
    return LucidEventEngine().build_lucid_events(upcoming_events)

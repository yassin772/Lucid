"""
modules/forexfactory_scraper.py — Scraper calendrier ForexFactory (V7)

Récupère les événements macro de la semaine courante et des 2 semaines précédentes
depuis le calendrier ForexFactory (JSON officiel non-documenté).

Endpoints utilisés :
  https://nfs.faireconomy.media/ff_calendar_thisweek.json
  https://nfs.faireconomy.media/ff_calendar_lastweek.json
  https://nfs.faireconomy.media/ff_calendar_nextweek.json

Ces endpoints sont publics et retournent directement du JSON structuré,
sans nécessiter de scraping HTML ni de contournement anti-bot.

Structure d'un événement ForexFactory brut :
  {
    "title":    "Non-Farm Employment Change",
    "country":  "USD",
    "date":     "03-07-2026",
    "time":     "8:30am",
    "impact":   "High",
    "forecast": "185K",
    "previous": "256K",
    "actual":   "227K"      ← null si non encore publié
  }

Pipeline de transformation :
  FF brut → normalized_event → storage JSON (format interne)

Résolution surprise :
  actual vs forecast : >0.5% écart relatif → positive/negative
  Si forecast null → neutre

Commande manuelle : python -m modules.forexfactory_scraper
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)


# ─── Configuration ─────────────────────────────────────────────────────────────

FF_ENDPOINTS: Dict[str, str] = {
    "lastweek":  "https://nfs.faireconomy.media/ff_calendar_lastweek.json",
    "thisweek":  "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "nextweek":  "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}

# Mapping pays FF → devise ISO
COUNTRY_TO_CURRENCY: Dict[str, str] = {
    "USD": "USD", "EUR": "EUR", "GBP": "GBP",
    "JPY": "JPY", "CHF": "CHF", "CAD": "CAD",
    "AUD": "AUD", "NZD": "NZD",
}

SUPPORTED_CURRENCIES = list(COUNTRY_TO_CURRENCY.values())

# Impacts ForexFactory à retenir (on ignore "Low")
IMPACT_FILTER = {"High", "Medium"}

# Mapping titre FF → event_type interne
# IMPORTANT : les mots-clés CB spécifiques doivent précéder "interest rate" (générique)
TITLE_TO_EVENT_TYPE: List[Tuple[str, str]] = [
    # Banques centrales — CB-spécifiques en PREMIER (priorité sur "interest rate")
    ("fomc",                       "FOMC"),
    ("ecb",                        "ECB"),
    ("boe",                        "BOE"),
    ("boj",                        "BOJ"),
    ("snb",                        "SNB"),
    ("boc",                        "BOC"),
    ("rba",                        "RBA"),
    ("rbnz",                       "RBNZ"),
    # Générique taux (après les CB spécifiques)
    ("interest rate",              "INTEREST_RATE"),
    ("monetary policy",            "INTEREST_RATE"),
    # Inflation
    ("cpi",                        "CPI"),
    ("pce",                        "CPI"),
    ("ppi",                        "PPI"),
    # Emploi
    ("non-farm",                   "NFP"),
    ("nonfarm",                    "NFP"),
    ("employment change",          "NFP"),
    ("unemployment",               "UNEMPLOYMENT"),
    ("jobless claims",             "UNEMPLOYMENT"),
    ("claimant",                   "UNEMPLOYMENT"),
    # Croissance
    ("gdp",                        "GDP"),
    ("gross domestic",             "GDP"),
    # PMI — spécifiques avant générique
    ("ism",                        "ISM"),
    ("manufacturing pmi",          "PMI_MFG"),
    ("services pmi",               "PMI_SERVICES"),
    ("composite pmi",              "PMI_COMPOSITE"),
    ("pmi",                        "PMI_COMPOSITE"),    # générique en dernier
    # Confiance / sentiment
    ("consumer confidence",        "CONSUMER_CONFIDENCE"),
    ("sentiment",                  "CONSUMER_CONFIDENCE"),
    ("zew",                        "CONSUMER_CONFIDENCE"),
    ("ifo",                        "CONSUMER_CONFIDENCE"),
    # Activité
    ("retail sales",               "RETAIL_SALES"),
    ("industrial production",      "INDUSTRIAL_PRODUCTION"),
    ("trade balance",              "TRADE_BALANCE"),
    # Divers
    ("oil",                        "OIL_INVENTORY"),
    ("crude",                      "OIL_INVENTORY"),
]

# Seuils de surprise (écart relatif entre actual et forecast)
SURPRISE_THRESHOLD = 0.005   # 0.5%

# Délai entre requêtes HTTP (politesse)
REQUEST_DELAY_SEC = 1.5

# Timeout requête HTTP
REQUEST_TIMEOUT_SEC = 10

# User-agent neutre
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MacroBot/1.0; "
        "+https://github.com/macro-bot)"
    ),
    "Accept": "application/json",
}


# ─── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class FFEvent:
    """Événement brut issu de l'API ForexFactory."""
    title:    str
    country:  str
    date:     str        # "03-07-2026"
    time:     str        # "8:30am" ou "All Day"
    impact:   str        # "High" | "Medium" | "Low"
    forecast: Optional[str]
    previous: Optional[str]
    actual:   Optional[str]


@dataclass
class NormalizedEvent:
    """Événement normalisé au format stockage interne."""
    date:        str            # "2026-03-07"
    currency:    str            # "USD"
    event_type:  str            # "NFP"
    title:       str
    expected:    Optional[float]
    actual:      Optional[float]
    surprise:    str            # "positive" | "negative" | "neutre"
    tone:        str            # dérivé de surprise + event_type
    impact:      str            # "fort_positif" | "positif" | "negatif" | ...
    theme:       str            # "emploi" | "inflation" | ...
    summary:     str
    is_upcoming: bool


# ─── Scraper principal ─────────────────────────────────────────────────────────

class ForexFactoryScraper:
    """
    Récupère et normalise les événements ForexFactory.

    Usage :
        scraper = ForexFactoryScraper()
        events_by_currency = scraper.fetch_and_normalize(["thisweek", "lastweek"])
        # events_by_currency["USD"] → List[NormalizedEvent]
    """

    def fetch_and_normalize(
        self,
        weeks: Optional[List[str]] = None,
    ) -> Dict[str, List[NormalizedEvent]]:
        """
        Récupère les événements des semaines spécifiées et les normalise.
        weeks : liste de clés parmi {"lastweek", "thisweek", "nextweek"}
        Retourne un dict currency → list d'événements normalisés.
        """
        if weeks is None:
            weeks = ["lastweek", "thisweek", "nextweek"]

        result: Dict[str, List[NormalizedEvent]] = {c: [] for c in SUPPORTED_CURRENCIES}

        for week_key in weeks:
            url = FF_ENDPOINTS.get(week_key)
            if not url:
                logger.warning(f"Clé semaine inconnue : {week_key}")
                continue

            raw_events = self._fetch_json(url)
            if raw_events is None:
                logger.warning(f"Impossible de récupérer {week_key}")
                continue

            logger.info(f"ForexFactory {week_key} : {len(raw_events)} événements bruts")
            is_upcoming = (week_key == "nextweek")

            for raw in raw_events:
                try:
                    ff_event = self._parse_raw(raw)
                    if not ff_event:
                        continue
                    if ff_event.impact not in IMPACT_FILTER:
                        continue
                    currency = COUNTRY_TO_CURRENCY.get(ff_event.country)
                    if not currency:
                        continue

                    # Marquer comme upcoming si nextweek ou actual non fourni
                    ev_upcoming = is_upcoming or (ff_event.actual is None)
                    normalized = self._normalize(ff_event, ev_upcoming)
                    if normalized:
                        result[currency].append(normalized)
                except Exception as e:
                    logger.debug(f"Erreur événement FF ({raw.get('title', '?')}) : {e}")

            time.sleep(REQUEST_DELAY_SEC)

        # Dédupliquer par (date, event_type) et trier
        for currency in result:
            result[currency] = self._dedup_sort(result[currency])
            logger.info(
                f"ForexFactory {currency} : "
                f"{len(result[currency])} événements normalisés"
            )

        return result

    def update_storage(
        self,
        events_by_currency: Dict[str, List[NormalizedEvent]],
        storage_dir: str,
    ) -> Dict[str, int]:
        """
        Écrit les événements normalisés dans les fichiers JSON de stockage.
        Fusionne avec les données existantes (conserve l'historique).
        Retourne un dict currency → nombre d'événements nouvellement ajoutés.
        """
        added: Dict[str, int] = {}
        for currency, new_events in events_by_currency.items():
            if not new_events:
                added[currency] = 0
                continue
            path = os.path.join(storage_dir, f"{currency}_events.json")
            existing = self._load_existing(path, currency)
            merged, n_added = self._merge_events(existing, new_events)
            self._write_events(path, currency, merged)
            added[currency] = n_added
            logger.info(
                f"Storage {currency} : {n_added} événements ajoutés "
                f"({len(merged)} total)"
            )
        return added

    # ── Helpers privés ─────────────────────────────────────────────────────────

    def _fetch_json(self, url: str) -> Optional[List[dict]]:
        """Requête HTTP simple vers l'API FF avec retries."""
        for attempt in range(3):
            try:
                req = Request(url, headers=HTTP_HEADERS)
                with urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data if isinstance(data, list) else None
            except (URLError, HTTPError) as e:
                logger.warning(f"HTTP erreur {url} (tentative {attempt+1}/3) : {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Erreur fetch {url} : {e}")
                break
        return None

    def _parse_raw(self, raw: dict) -> Optional[FFEvent]:
        """Parse un événement brut FF en FFEvent."""
        title = (raw.get("title") or "").strip()
        country = (raw.get("country") or "").strip().upper()
        date_str = (raw.get("date") or "").strip()
        impact = (raw.get("impact") or "").strip().title()   # "high" → "High"

        if not (title and country and date_str):
            return None

        return FFEvent(
            title=title,
            country=country,
            date=date_str,
            time=(raw.get("time") or "").strip(),
            impact=impact,
            forecast=raw.get("forecast"),
            previous=raw.get("previous"),
            actual=raw.get("actual"),
        )

    def _normalize(self, ev: FFEvent, is_upcoming: bool) -> Optional[NormalizedEvent]:
        """Transforme un FFEvent en NormalizedEvent au format interne."""
        # ── Date ────────────────────────────────────────────────────────────
        iso_date = self._parse_date(ev.date)
        if not iso_date:
            return None

        # ── Currency ────────────────────────────────────────────────────────
        currency = COUNTRY_TO_CURRENCY.get(ev.country)
        if not currency:
            return None

        # ── Event type ──────────────────────────────────────────────────────
        event_type = self._resolve_event_type(ev.title)

        # ── Valeurs numériques ──────────────────────────────────────────────
        expected = self._parse_number(ev.forecast)
        actual   = self._parse_number(ev.actual) if not is_upcoming else None

        # ── Surprise ────────────────────────────────────────────────────────
        surprise = self._compute_surprise(expected, actual, event_type)

        # ── Tone et impact ──────────────────────────────────────────────────
        tone   = self._surprise_to_tone(surprise, event_type, currency)
        impact = self._surprise_to_impact(surprise, ev.impact)

        # ── Thème ───────────────────────────────────────────────────────────
        theme = self._event_type_to_theme(event_type)

        # ── Résumé ──────────────────────────────────────────────────────────
        summary = self._build_summary(ev, expected, actual, surprise)

        return NormalizedEvent(
            date=iso_date,
            currency=currency,
            event_type=event_type,
            title=ev.title,
            expected=expected,
            actual=actual,
            surprise=surprise,
            tone=tone,
            impact=impact,
            theme=theme,
            summary=summary,
            is_upcoming=is_upcoming,
        )

    # ── Utilitaires de parsing ─────────────────────────────────────────────────

    @staticmethod
    def _parse_date(date_str: str) -> Optional[str]:
        """Convertit les formats ForexFactory courants en 'YYYY-MM-DD'."""
        value = (date_str or "").strip()
        if not value:
            return None

        # Format actuel de l'endpoint JSON FF :
        # "2026-04-30T08:30:00-04:00"
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d")
        except ValueError:
            pass

        for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        logger.debug(f"Date non parseable : {value!r}")
        return None

    @staticmethod
    def _parse_number(s: Optional[str]) -> Optional[float]:
        """Parse '185K', '2.3%', '-0.1', etc. → float ou None."""
        if not s or s.strip() in ("", "N/A", "null", "—"):
            return None
        s = s.strip().replace(",", "")
        multiplier = 1.0
        if s.endswith("K") or s.endswith("k"):
            multiplier = 1_000
            s = s[:-1]
        elif s.endswith("M") or s.endswith("m"):
            multiplier = 1_000_000
            s = s[:-1]
        elif s.endswith("B") or s.endswith("b"):
            multiplier = 1_000_000_000
            s = s[:-1]
        s = s.rstrip("%")
        try:
            return float(s) * multiplier
        except ValueError:
            return None

    @staticmethod
    def _resolve_event_type(title: str) -> str:
        """Résout event_type à partir du titre (matching insensible à la casse)."""
        title_lower = title.lower()
        for keyword, etype in TITLE_TO_EVENT_TYPE:
            if keyword in title_lower:
                return etype
        return "OTHER"

    def _compute_surprise(
        self,
        expected: Optional[float],
        actual: Optional[float],
        event_type: str,
    ) -> str:
        if actual is None or expected is None:
            return "neutre"
        if expected == 0:
            diff = actual
        else:
            diff = (actual - expected) / abs(expected)

        # Pour le chômage et les jobless claims, + = mauvais (inversion)
        invert = event_type in ("UNEMPLOYMENT",)
        if invert:
            diff = -diff

        if diff > SURPRISE_THRESHOLD:
            return "positive"
        if diff < -SURPRISE_THRESHOLD:
            return "negative"
        return "neutre"

    @staticmethod
    def _surprise_to_tone(surprise: str, event_type: str, currency: str) -> str:
        """Dérive le ton CB/macro depuis la surprise."""
        if surprise == "positive":
            return "hawkish" if event_type in (
                "NFP", "CPI", "GDP", "ISM", "INTEREST_RATE",
                "FOMC", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ",
            ) else "hawkish_modere"
        if surprise == "negative":
            return "dovish" if event_type in (
                "NFP", "CPI", "GDP", "ISM", "INTEREST_RATE",
                "FOMC", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ",
            ) else "dovish_modere"
        return "neutre"

    @staticmethod
    def _surprise_to_impact(surprise: str, ff_impact: str) -> str:
        is_high = (ff_impact == "High")
        if surprise == "positive":
            return "fort_positif" if is_high else "positif"
        if surprise == "negative":
            return "fort_negatif" if is_high else "negatif"
        return "neutre"

    @staticmethod
    def _event_type_to_theme(event_type: str) -> str:
        mapping = {
            "INTEREST_RATE": "politique_monetaire",
            "FOMC": "politique_monetaire", "ECB": "politique_monetaire",
            "BOE": "politique_monetaire",  "BOJ": "politique_monetaire",
            "SNB": "politique_monetaire",  "BOC": "politique_monetaire",
            "RBA": "politique_monetaire",  "RBNZ": "politique_monetaire",
            "CPI": "inflation",  "PPI": "inflation",
            "NFP": "emploi",     "UNEMPLOYMENT": "emploi",
            "GDP": "croissance", "PMI_MFG": "croissance",
            "PMI_SERVICES": "croissance", "PMI_COMPOSITE": "croissance",
            "ISM": "croissance", "RETAIL_SALES": "croissance",
            "CONSUMER_CONFIDENCE": "sentiment",
            "TRADE_BALANCE": "commerce",
            "INDUSTRIAL_PRODUCTION": "croissance",
            "OIL_INVENTORY": "energie",
        }
        return mapping.get(event_type, "calendrier")

    @staticmethod
    def _build_summary(
        ev: FFEvent,
        expected: Optional[float],
        actual: Optional[float],
        surprise: str,
    ) -> str:
        parts = [ev.title]
        if actual is not None and expected is not None:
            parts.append(f"Actual={ev.actual} vs Forecast={ev.forecast}")
            if surprise == "positive":
                parts.append("Meilleur qu'attendu.")
            elif surprise == "negative":
                parts.append("Inférieur aux attentes.")
        elif ev.forecast:
            parts.append(f"Forecast={ev.forecast}.")
        return "  ".join(parts)

    # ── Déduplication & stockage ───────────────────────────────────────────────

    @staticmethod
    def _dedup_sort(events: List[NormalizedEvent]) -> List[NormalizedEvent]:
        """Déduplique par (date, event_type) — préfère les données complètes."""
        seen: Dict[tuple, NormalizedEvent] = {}
        for e in events:
            key = (e.date, e.event_type)
            existing = seen.get(key)
            if existing is None or (e.actual is not None and existing.actual is None):
                seen[key] = e
        return sorted(seen.values(), key=lambda x: x.date)

    @staticmethod
    def _load_existing(path: str, currency: str) -> List[dict]:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("events", [])
        except Exception as e:
            logger.warning(f"Chargement {path} : {e}")
            return []

    @staticmethod
    def _merge_events(
        existing: List[dict],
        new_events: List[NormalizedEvent],
    ) -> Tuple[List[dict], int]:
        """
        Fusionne nouveaux événements avec existants.
        - Si même (date, event_type) : met à jour si actual devient disponible
        - Sinon : ajoute
        """
        index: Dict[tuple, dict] = {}
        for e in existing:
            key = (e.get("date"), e.get("event_type"))
            index[key] = e

        n_added = 0
        for new in new_events:
            key = (new.date, new.event_type)
            if key not in index:
                index[key] = asdict(new)
                n_added += 1
            else:
                # Mettre à jour si actual maintenant disponible
                existing_ev = index[key]
                if new.actual is not None and existing_ev.get("actual") is None:
                    existing_ev.update({
                        "actual":   new.actual,
                        "surprise": new.surprise,
                        "tone":     new.tone,
                        "impact":   new.impact,
                        "summary":  new.summary,
                        "is_upcoming": False,
                    })

        merged = sorted(index.values(), key=lambda x: x.get("date", ""))
        return merged, n_added

    @staticmethod
    def _write_events(path: str, currency: str, events: List[dict]) -> None:
        data = {
            "currency":   currency,
            "updated_at": datetime.utcnow().isoformat(),
            "events":     events,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Commande standalone ───────────────────────────────────────────────────────

def run_scrape(storage_dir: Optional[str] = None) -> Dict[str, int]:
    """
    Point d'entrée principal — utilisable depuis Discord ou cron.
    Récupère les 3 semaines (last/this/next) et met à jour le stockage.
    """
    if storage_dir is None:
        storage_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "storage", "events"
        )
    scraper = ForexFactoryScraper()
    logger.info("ForexFactory scraping démarré (last + this + next week)")
    events_by_currency = scraper.fetch_and_normalize(["lastweek", "thisweek", "nextweek"])
    added = scraper.update_storage(events_by_currency, storage_dir)
    total = sum(added.values())
    logger.info(
        f"ForexFactory scraping terminé — {total} événements ajoutés : "
        + "  ".join(f"{c}+{n}" for c, n in added.items() if n > 0)
    )
    return added


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # ── Diagnostic rapide avant le scrape complet ─────────────────────────────
    print("\n=== DIAGNOSTIC ForexFactory (thisweek) ===")
    _scraper = ForexFactoryScraper()
    _raw = _scraper._fetch_json(FF_ENDPOINTS["thisweek"])
    if _raw:
        print(f"Total brut        : {len(_raw)} événements")
        _impacts   = sorted({e.get("impact", "") for e in _raw})
        _countries = sorted({e.get("country", "") for e in _raw})
        _dates     = sorted({e.get("date", "") for e in _raw})[:3]
        print(f"Impacts uniques   : {_impacts}")
        print(f"Countries uniques : {_countries}")
        print(f"Dates (3 premières): {_dates}")
        print(f"\n=== PREMIER ÉVÉNEMENT BRUT ===")
        import json as _json
        print(_json.dumps(_raw[0], indent=2, ensure_ascii=False))

        # Compter ce que le filtre laisse passer
        _high_med = [e for e in _raw if (e.get("impact") or "").strip().title() in {"High", "Medium"}]
        _known_cc = [e for e in _high_med if (e.get("country") or "").upper() in COUNTRY_TO_CURRENCY]
        print(f"\nAprès filtre impact (High/Medium) : {len(_high_med)}/{len(_raw)}")
        print(f"Après filtre currency connue       : {len(_known_cc)}/{len(_high_med)}")
    else:
        print("Impossible de récupérer thisweek")
    print("==========================================\n")

    result = run_scrape()
    print(f"\nRésultat final : {result}")

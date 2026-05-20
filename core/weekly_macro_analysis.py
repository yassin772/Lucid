"""
core/weekly_macro_analysis.py — Analyse macro hebdomadaire globale (V6)

V6 — Paires de devises & différentiels de taux :
- CurrencyPairsEngine génère des setups classés par conviction
- WeeklyMacroSummary.pair_setups : List[PairSetup]
- Tableau des taux directeurs inclus dans le rapport

V5 — Data Surprise Index :
- DSIProvider calcule le score de surprise des données économiques
- WeeklyMacroSummary.dsi_scores : Dict[str, DSIScore]
- CBCalendarAuditor valide les dates des réunions CB

V4 :
- Fundamentals dynamiques via RealFundamentalsProvider
- Historique narratif SQLite via NarrativeHistoryDB

Conservé de V3.1 :
- WeeklyMacroSummary.news_headlines : List[TrustedHeadline]
- TrustedNewsProvider (Reuters RSS + NewsAPI optionnel)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import logging

from core.narrative_tracker import NarrativeTracker
from core.scenario_service import ScenarioService
from core.event_memory_service import EventMemoryService
from core.narrative_history import NarrativeHistoryDB, NarrativeChange
from modules.calendar_provider import UpcomingEvent
from modules.risk_engine import compute_risk_environment, RiskEnvironment, get_conditional_note
from modules.trusted_news_provider import TrustedNewsProvider, TrustedHeadline
from modules.provider_factory import get_provider_status, get_fundamentals_provider
from modules.data_surprise_index import DSIProvider, DSIScore
from modules.cb_calendar_validator import CBCalendarAuditor
from modules.currency_pairs import CurrencyPairsEngine, PairSetup
from modules.lucid_event_engine import LucidEvent, build_lucid_events
from modules.lucid_summary_engine import LucidSummary, build_lucid_summaries
from config import SUPPORTED_CURRENCIES

logger = logging.getLogger(__name__)


@dataclass
class WeeklyMacroSummary:
    """Résumé macro hebdomadaire global."""
    supported_currencies:   List[str]           # Devises currency_bias == "soutenu"
    fragile_currencies:     List[str]           # Devises currency_bias == "fragile"
    neutral_currencies:     List[str]           # Devises dominant_tone == "neutre"
    narratives:             Dict
    upcoming_events:        List[UpcomingEvent]
    dominant_scenarios:     Dict
    global_theme:           str
    risk_environment:       Optional[RiskEnvironment] = None
    conditional_currencies: Optional[Dict[str, str]] = None
    news_headlines:         List[TrustedHeadline] = field(default_factory=list)
    narrative_changes:      Dict[str, NarrativeChange] = field(default_factory=dict)  # V4
    dsi_scores:             Dict[str, DSIScore] = field(default_factory=dict)          # V5
    pair_setups:            List[PairSetup] = field(default_factory=list)              # V6
    fundamentals_all:       Dict = field(default_factory=dict)                         # V6
    lucid_events:           List[LucidEvent] = field(default_factory=list)             # V7 Lucid
    lucid_summaries:        Dict[str, LucidSummary] = field(default_factory=dict)     # V7 Lucid


class WeeklyMacroAnalysis:
    """
    Agrège l'analyse macro de toutes les devises.
    Le RiskEnvironment est calculé une seule fois et propagé à toutes les devises.
    Les headlines Reuters/Bloomberg enrichissent le contexte sans modifier le moteur.
    """

    def __init__(self):
        self.narrative_tracker      = NarrativeTracker()
        self.scenario_service       = ScenarioService()
        self.event_service          = EventMemoryService()
        self.fundamentals_provider  = get_fundamentals_provider()   # V4 : dynamique
        self.trusted_news           = TrustedNewsProvider()
        self.narrative_history      = NarrativeHistoryDB()           # V4 : historique SQLite
        self.dsi_provider           = DSIProvider()                  # V5 : Data Surprise Index
        self._cb_auditor            = CBCalendarAuditor()            # V5 : validateur calendrier CB
        self._pairs_engine          = CurrencyPairsEngine()          # V6 : setups paires de devises

        status = get_provider_status()
        logger.info(
            f"WeeklyMacroAnalysis initialisé — "
            f"Calendar: {status['calendar']} | News: {status['news']} | "
            f"Fundamentals: {status['fundamentals']}"
        )

    def build_weekly_summary(self) -> WeeklyMacroSummary:
        """Construit le résumé macro de la semaine avec RiskEnvironment + news fiables."""
        logger.info("build_weekly_summary démarré")

        # ── 0. Audit calendrier CB (non-bloquant) ──────────────────────────
        try:
            import storage_manager as sm
            cb_reports = self._cb_auditor.audit_all(SUPPORTED_CURRENCIES, sm.load_events)
            has_errors = any(not r.is_valid for r in cb_reports.values())
            if has_errors:
                logger.warning(
                    "⚠️  Incohérences CB détectées :\n"
                    + self._cb_auditor.audit_summary(cb_reports)
                )
            else:
                logger.info("Audit calendrier CB : aucune incohérence")
        except Exception as e:
            logger.warning(f"Audit calendrier CB (non-bloquant) : {e}")

        # ── 1. RiskEnvironment global ──────────────────────────────────────
        try:
            risk_environment = self._compute_risk_environment()
            logger.info(
                f"Climat détecté : {risk_environment.label_fr} "
                f"{risk_environment.intensity_fr} (score={risk_environment.score:.2f})"
            )
        except Exception as e:
            logger.error(f"Erreur calcul RiskEnvironment : {e}", exc_info=True)
            risk_environment = _neutral_risk_environment()

        logger.info("providers chargés")

        # ── 2. Narratifs ───────────────────────────────────────────────────
        narratives: Dict = {}
        for currency in SUPPORTED_CURRENCIES:
            try:
                narratives[currency] = self.narrative_tracker.get_narrative(
                    currency, risk_environment=risk_environment
                )
            except Exception as e:
                logger.error(f"Erreur narratif {currency}: {e}")

        logger.info(f"narratives générés : {len(narratives)}")

        # ── 2b. Historique narratif + détection retournements (V4) ────────
        narrative_changes: Dict[str, NarrativeChange] = {}
        try:
            self.narrative_history.save_all_narratives(narratives)
            narrative_changes = self.narrative_history.detect_all_changes(SUPPORTED_CURRENCIES)
            if narrative_changes:
                logger.info(
                    f"Retournements détectés : {list(narrative_changes.keys())}"
                )
            else:
                logger.info("Aucun retournement narratif cette semaine")
        except Exception as e:
            logger.error(f"Erreur historique narratif (non-bloquant) : {e}")

        # ── 3. Événements à venir ──────────────────────────────────────────
        upcoming_events: List[UpcomingEvent] = []
        try:
            upcoming_events = self.event_service.get_upcoming_events() or []
        except Exception as e:
            logger.error(f"Erreur événements à venir: {e}")

        logger.info(f"upcoming_events : {len(upcoming_events)}")

        # ── 3b. Lucid Events (V7) ──────────────────────────────────────────────
        lucid_events: List[LucidEvent] = []
        try:
            lucid_events = build_lucid_events(upcoming_events)
            today_count = sum(1 for e in lucid_events if e.is_today)
            logger.info(
                f"Lucid Events : {len(lucid_events)} générés "
                f"({today_count} today, "
                f"{sum(1 for e in lucid_events if e.is_cb_speech)} CB speeches)"
            )
        except Exception as e:
            logger.warning(f"Lucid Events (non-bloquant) : {e}")

        # ── 3c. Lucid Summaries (V7) ──────────────────────────────────────────
        lucid_summaries: Dict[str, LucidSummary] = {}
        try:
            lucid_summaries = build_lucid_summaries(
                narratives=narratives,
                lucid_events=lucid_events,
                risk_environment=risk_environment,
            )
            logger.info(f"Lucid Summaries : {len(lucid_summaries)} devises")
        except Exception as e:
            logger.warning(f"Lucid Summaries (non-bloquant) : {e}")

        # ── 4. Scénarios dominants ─────────────────────────────────────────
        dominant_scenarios: Dict = {}
        for currency in SUPPORTED_CURRENCIES:
            try:
                scenarios = self.scenario_service.get_scenarios(
                    currency, risk_environment=risk_environment
                )
                if scenarios:
                    dominant_scenarios[currency] = max(scenarios, key=lambda s: s.probability)
            except Exception as e:
                logger.error(f"Erreur scénario {currency}: {e}")

        logger.info(f"dominant_scenarios : {len(dominant_scenarios)}")

        # ── 5. Classification des devises ──────────────────────────────────
        soutenu = [c for c, n in narratives.items() if n and n.currency_bias == "soutenu"]
        fragile = [c for c, n in narratives.items() if n and n.currency_bias == "fragile"]
        neutral = [c for c, n in narratives.items() if n and n.dominant_tone == "neutre"]

        # ── 6. Thème global ────────────────────────────────────────────────
        global_theme = self._derive_global_theme(narratives, risk_environment)

        # ── 7. Notes conditionnelles (refuges + cycliques) ─────────────────
        conditional_currencies: Dict[str, str] = {}
        try:
            conditional_currencies = self._build_conditional_currencies(risk_environment)
        except Exception as e:
            logger.error(f"Erreur conditional_currencies: {e}")

        # ── 8. Data Surprise Index (V5) ───────────────────────────────
        dsi_scores: Dict[str, DSIScore] = {}
        try:
            dsi_scores = self.dsi_provider.get_all_dsi(SUPPORTED_CURRENCIES)
            logger.info(
                f"DSI calculé pour {len(dsi_scores)} devises : "
                + " | ".join(f"{c}={dsi_scores[c].score:+.2f}" for c in dsi_scores)
            )
        except Exception as e:
            logger.warning(f"Erreur DSI (non-bloquant) : {e}")

        # ── 9. Headlines Reuters / Bloomberg ──────────────────────────────
        news_headlines: List[TrustedHeadline] = []
        try:
            news_headlines = self.trusted_news.get_headlines()
            logger.info(
                f"headlines fiables : {len(news_headlines)} "
                f"({', '.join({h.source for h in news_headlines})})"
            )
        except Exception as e:
            logger.warning(f"Erreur headlines news (non-bloquant) : {e}")

        # ── 10. Fundamentals complets (pour paires) (V6) ──────────────────
        fundamentals_all: Dict = {}
        try:
            for currency in SUPPORTED_CURRENCIES:
                fund = self.fundamentals_provider.get_fundamentals(currency)
                if fund:
                    fundamentals_all[currency] = fund
            logger.info(
                f"Fundamentals collectés pour les paires : {list(fundamentals_all.keys())}"
            )
        except Exception as e:
            logger.warning(f"Erreur fundamentals (paires, non-bloquant) : {e}")

        # ── 11. Setups paires de devises (V6) ─────────────────────────────
        pair_setups: List[PairSetup] = []
        try:
            pair_setups = self._pairs_engine.generate_setups(
                narratives=narratives,
                dsi_scores=dsi_scores,
                fundamentals=fundamentals_all,
            )
            tradable = sum(1 for s in pair_setups if s.is_tradable())
            logger.info(
                f"Paires générées : {len(pair_setups)} total, "
                f"{tradable} tradables (conviction ≥ 25%)"
            )
        except Exception as e:
            logger.warning(f"Erreur paires de devises (non-bloquant) : {e}")

        logger.info("build_weekly_summary terminé")

        return WeeklyMacroSummary(
            supported_currencies=soutenu,
            fragile_currencies=fragile,
            neutral_currencies=neutral,
            narratives=narratives,
            upcoming_events=upcoming_events,
            dominant_scenarios=dominant_scenarios,
            global_theme=global_theme,
            risk_environment=risk_environment,
            conditional_currencies=conditional_currencies,
            news_headlines=news_headlines,
            narrative_changes=narrative_changes,   # V4
            dsi_scores=dsi_scores,                 # V5
            pair_setups=pair_setups,               # V6
            fundamentals_all=fundamentals_all,     # V6
            lucid_events=lucid_events,             # V7 Lucid
            lucid_summaries=lucid_summaries,       # V7 Lucid
        )

    # ─── Helpers privés ────────────────────────────────────────────────────────

    def _compute_risk_environment(self) -> RiskEnvironment:
        """Bootstrap : calcule le RiskEnvironment depuis les narratifs de base."""
        narratives: Dict = {}
        fundamentals_dict: Dict = {}
        for currency in SUPPORTED_CURRENCIES:
            try:
                narratives[currency] = self.narrative_tracker.get_narrative(currency)
                fundamentals_dict[currency] = self.fundamentals_provider.get_fundamentals(currency)
            except Exception as e:
                logger.warning(f"_compute_risk_environment ({currency}): {e}")
        return compute_risk_environment(narratives, fundamentals_dict)

    def _build_conditional_currencies(self, risk_environment: RiskEnvironment) -> Dict[str, str]:
        from modules.risk_engine import REFUGE_CURRENCIES, CYCLICAL_CURRENCIES
        result: Dict[str, str] = {}
        for currency in REFUGE_CURRENCIES | CYCLICAL_CURRENCIES:
            note = get_conditional_note(currency, risk_environment.label)
            if note:
                result[currency] = note
        return result

    def _derive_global_theme(
        self,
        narratives: Dict,
        risk_environment: Optional[RiskEnvironment] = None,
    ) -> str:
        if risk_environment:
            if risk_environment.label == "risk_off":
                return "🔴 Semaine risk-off — refuges sous surveillance"
            if risk_environment.label == "risk_on":
                return "🟢 Sentiment positif — carry trade et cycliques favorisés"

        hawkish_count = sum(
            1 for n in narratives.values()
            if n and n.dominant_tone in ("hawkish", "hawkish_modere")
        )
        dovish_count = sum(
            1 for n in narratives.values()
            if n and n.dominant_tone in ("dovish", "dovish_modere")
        )

        if hawkish_count >= 4:
            return "Semaine dominée par les signaux hawkish — taux directeurs sous pression"
        if dovish_count >= 4:
            return "Semaine dovish — ralentissement généralisé, banques centrales accommodantes"
        return "Semaine mixte — divergences entre blocs monétaires"


# ─── Fallback RiskEnvironment ──────────────────────────────────────────────────

def _neutral_risk_environment() -> RiskEnvironment:
    return RiskEnvironment(
        label="neutral", label_fr="Neutre", score=0.0,
        intensity="faible", intensity_fr="Faible",
        explanation="Données insuffisantes pour évaluer le climat",
        emoji="⚪", conditional_currencies=[],
    )

# Description Technique — Bot d'Analyse Fondamentale Forex (V6)

## Vue d'ensemble

Bot Discord Python d'analyse macro-fondamentale Forex, conçu pour générer des rapports hebdomadaires structurés sur 8 devises majeures (USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD). Le système utilise une architecture modulaire par couches : collecte de données → analyse → génération de narratifs → scoring de paires → rapport Discord.

**Stack technique :** Python 3.10+, discord.py, SQLite, fichiers JSON (stockage événements/narratifs), urllib (pas de requests externe), APScheduler.

---

## Structure du projet

```
macro-scenarios-bot/
├── bot.py                        # Point d'entrée Discord
├── config.py                     # Devises, constantes globales
├── scheduler.py                  # Tâches planifiées (APScheduler)
├── storage_manager.py            # Lecture/écriture fichiers JSON de stockage
│
├── commands/                     # Commandes slash Discord
│   ├── macro_weekly.py           # /macro_weekly — rapport hebdomadaire complet
│   ├── refresh.py                # /refresh — mise à jour des données (+ FF scraper V7)
│   ├── currency_macro.py         # /currency_macro <devise> — analyse par devise
│   ├── narrative.py              # /narrative <devise> — narratif détaillé
│   ├── upcoming_events.py        # /upcoming_events — calendrier à venir
│   └── scenario.py               # /scenario <devise> — scénarios haussier/baissier
│
├── core/                         # Logique métier centrale
│   ├── weekly_macro_analysis.py  # Orchestrateur principal (WeeklyMacroAnalysis)
│   ├── narrative_tracker.py      # Coordination narratifs par devise
│   ├── scenario_service.py       # Génération scénarios probabilisés
│   ├── event_memory_service.py   # Service événements (passés + à venir)
│   └── narrative_history.py      # Historique SQLite + détection retournements
│
├── modules/                      # Moteurs d'analyse et providers
│   ├── narrative_engine.py       # Moteur narratif à règles (scoring composite)
│   ├── currency_pairs.py         # V6 — Engine setups paires + carry differentials
│   ├── forexfactory_scraper.py   # V7 — Scraper ForexFactory JSON
│   ├── data_surprise_index.py    # V5 — DSI (Data Surprise Index)
│   ├── cb_calendar_validator.py  # V5 — Validation dates réunions CB
│   ├── risk_engine.py            # Calcul RiskEnvironment (risk-on/off/neutral)
│   ├── report.py                 # Génération embeds Discord
│   ├── scenario_engine.py        # Scénarios par règles avec probabilités
│   ├── probability_engine.py     # Moteur probabilités
│   ├── event_parser.py           # Parser événements depuis JSON
│   ├── fundamentals_provider.py  # Interface provider fondamentaux
│   ├── real_fundamentals_provider.py  # Provider fondamentaux depuis JSON
│   ├── calendar_provider.py      # Interface provider calendrier
│   ├── real_calendar_provider.py # Provider calendrier depuis JSON
│   ├── news_provider.py          # Interface provider news
│   ├── trusted_news_provider.py  # Reuters RSS + NewsAPI optionnel
│   └── provider_factory.py       # Factory — sélection automatique real/mock
│
└── storage/
    ├── events/                   # {DEVISE}_events.json (8 fichiers)
    ├── narratives/               # {DEVISE}_narrative.json (8 fichiers)
    ├── scenarios/                # Scénarios en cache
    ├── narrative_history.db      # SQLite — historique narratifs
    ├── cache/                    # Cache général
    └── logs/bot.log
```

---

## Pipeline d'analyse principal

### Déclenchement
La commande `/macro_weekly` appelle `WeeklyMacroAnalysis.build_weekly_summary()` qui orchestre l'ensemble du pipeline en 11 étapes séquentielles :

```
Étape 0  → Audit calendrier CB (non-bloquant)
Étape 1  → Calcul RiskEnvironment global
Étape 2  → Génération narratifs par devise (8 devises)
Étape 2b → Sauvegarde + détection retournements narratifs (SQLite)
Étape 3  → Événements à venir (calendrier)
Étape 4  → Scénarios dominants (max probabilité par devise)
Étape 5  → Classification devises (soutenu / neutre / fragile)
Étape 6  → Thème global de la semaine
Étape 7  → Notes conditionnelles (refuges + cycliques)
Étape 8  → Data Surprise Index (DSI) pour 8 devises
Étape 9  → Headlines Reuters (RSS, non-bloquant)
Étape 10 → Collecte fundamentals pour moteur paires
Étape 11 → Génération setups paires de devises (20 paires)
```

Chaque étape est wrappée dans un try/except — une erreur n'arrête pas le pipeline.

### Résultat : WeeklyMacroSummary
```python
@dataclass
class WeeklyMacroSummary:
    supported_currencies:   List[str]           # devises "soutenu"
    fragile_currencies:     List[str]           # devises "fragile"
    neutral_currencies:     List[str]           # devises "neutre"
    narratives:             Dict[str, Narrative]
    upcoming_events:        List[UpcomingEvent]
    dominant_scenarios:     Dict[str, Scenario]
    global_theme:           str
    risk_environment:       Optional[RiskEnvironment]
    conditional_currencies: Optional[Dict[str, str]]
    news_headlines:         List[TrustedHeadline]
    narrative_changes:      Dict[str, NarrativeChange]  # V4
    dsi_scores:             Dict[str, DSIScore]          # V5
    pair_setups:            List[PairSetup]              # V6
    fundamentals_all:       Dict                         # V6
```

---

## Module 1 — Moteur Narratif (narrative_engine.py)

### Principe
Approche par règles exclusivement — pas de NLP, pas de ML. Chaque événement macro contribue un score pondéré selon :

```
score_événement = TONE_WEIGHTS[tone] × EVENT_TYPE_WEIGHT × SURPRISE_MULTIPLIER
score_composite = moyenne(tous les scores) avec fondamentaux × 2.5
```

### Pondérations événements
| Type | Poids | Type | Poids |
|------|-------|------|-------|
| INTEREST_RATE / CB meetings | 2.0 | CPI | 1.8 |
| NFP | 1.6 | GDP | 1.4 |
| UNEMPLOYMENT | 1.2 | PMI_COMPOSITE | 1.2 |
| PMI_MFG/SERVICES | 1.0 | RETAIL_SALES | 1.0 |
| TRADE_BALANCE | 0.8 | OIL_INVENTORY | 0.8 |

### Mapping score → ton
| Score | Ton |
|-------|-----|
| ≥ 1.4 | hawkish |
| ≥ 0.5 | hawkish_modere |
| ≥ -0.5 | neutre |
| ≥ -1.4 | dovish_modere |
| < -1.4 | dovish |

### Ton → Biais devise
- hawkish / hawkish_modere → **soutenu**
- neutre → **neutre**
- dovish_modere / dovish → **fragile**

### Logique conditionnelle
Devises refuges (JPY, CHF) et cycliques (AUD, NZD, CAD) reçoivent des notes additionnelles selon le `RiskEnvironment` : "faible structurellement mais refuge activé si risk-off", "corrélé pétrole + cycle US", etc.

### Output : Narrative
```python
@dataclass
class Narrative:
    currency: str
    dominant_narrative: str      # Phrase résumant le narratif
    dominant_tone: str           # hawkish | hawkish_modere | neutre | dovish_modere | dovish
    tone_label: str              # Version française
    reasons: List[str]           # 2-3 raisons courtes
    coherence: str               # forte | moderee | faible
    coherence_score: float       # 0.0 → 1.0
    signal_summary: str          # "Signal positif (5 événements, signaux cohérents)"
    currency_bias: str           # soutenu | neutre | fragile
    conditional_note: Optional[str]
    risk_context: Optional[str]
```

---

## Module 2 — RiskEnvironment (risk_engine.py)

Calcule le climat de marché global à partir des narratifs et fondamentaux.

**Output :**
```python
@dataclass
class RiskEnvironment:
    label: str          # "risk_on" | "risk_off" | "neutral"
    label_fr: str
    score: float        # -1.0 → +1.0
    intensity: str      # "fort" | "modere" | "faible"
    intensity_fr: str
    explanation: str
    emoji: str
    conditional_currencies: List[str]
```

**Logique :** Compte les devises hawkish/dovish, refuge/cyclique, puis calcule un score composite pour déterminer le label.

---

## Module 3 — Currency Pairs Engine (currency_pairs.py) — V6

### Devises et taux directeurs
```python
POLICY_RATES = {
    "USD": 4.50, "EUR": 2.50, "GBP": 4.25,
    "JPY": 0.50, "CHF": 0.25, "CAD": 3.25,
    "AUD": 4.10, "NZD": 3.50
}
```

### 20 paires analysées
EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/GBP, EUR/JPY, GBP/JPY, EUR/AUD, EUR/CAD, GBP/AUD, AUD/JPY, NZD/JPY, GBP/CHF, EUR/CHF, AUD/NZD, CAD/JPY, GBP/NZD

### Algorithme de scoring
```
score_brut = biais_narratif(50%) + divergence_DSI(30%) + carry_differential(20%)

biais_narratif = BIAS_SCORES[base_bias] - BIAS_SCORES[quote_bias]
  BIAS_SCORES = {hawkish: +0.85, hawkish_modere: +0.40, neutre: 0.0,
                 dovish_modere: -0.40, dovish: -0.80}

divergence_DSI = (dsi_base - dsi_quote) / 5.0  # normalisé [-1, +1]

carry = (rate_base - rate_quote) / max_carry    # max_carry ≈ 4.25

score_final = max(-1.0, min(1.0, score_brut))
conviction = |score_final|  # [0, 1]
direction = "long" si score > +0.1, "short" si < -0.1, "neutre" sinon
```

### Output : PairSetup
```python
@dataclass
class PairSetup:
    pair: str                  # "EUR/USD"
    base: str                  # "EUR"
    quote: str                 # "USD"
    direction: str             # "long" | "short" | "neutre"
    direction_emoji: str
    conviction: float          # [0.0, 1.0]
    conviction_label: str      # "Forte" | "Modérée" | "Faible"
    conviction_emoji: str
    rate_diff: RateDifferential
    key_drivers: List[str]
    base_bias: str
    quote_bias: str

    def one_liner(self) -> str
    def is_tradable(self) -> bool  # conviction >= 0.25
```

### Niveaux de conviction
| Seuil | Label | Emoji |
|-------|-------|-------|
| ≥ 0.70 | Forte | 🟢🟢 |
| ≥ 0.45 | Modérée | 🟢 |
| ≥ 0.25 | Faible | 🟡 |
| < 0.25 | Non tradable | 🔴 |

---

## Module 4 — Data Surprise Index (data_surprise_index.py) — V5

Calcule un score de surprise économique cumulatif par devise sur une fenêtre glissante.

**Principe :** Pour chaque événement avec `actual` et `expected`, calcule la surprise normalisée. Un score positif = données meilleures qu'attendu = signal hawkish potentiel.

**Output : DSIScore**
```python
@dataclass
class DSIScore:
    currency: str
    score: float          # Score composite [-5, +5] typiquement
    label: str            # "Positif fort" | "Légèrement positif" | etc.
    trend: str            # "amélioration" | "détérioration" | "stable"
    n_events: int         # Nombre d'événements dans le calcul
    last_updated: str
```

---

## Module 5 — CB Calendar Validator (cb_calendar_validator.py) — V5

### Calendrier officiel 2026 (hardcodé)
Dates officielles pour 8 banques centrales : ECB, FOMC, BOE, BOJ, SNB, BOC, RBA, RBNZ.

### Règles de validation
- Gap minimum entre deux réunions : 35 jours (configurable par CB)
- Tolérance date officielle : ±5 jours
- Résolution INTEREST_RATE → CB par devise (pas FOMC par défaut)

### Output : CBValidationReport
```python
@dataclass
class CBValidationReport:
    currency: str
    cb_name: str
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    checked_events: int
```

---

## Module 6 — ForexFactory Scraper (forexfactory_scraper.py) — V7

### Source de données
API JSON publique (non documentée) : `https://nfs.faireconomy.media/ff_calendar_thisweek.json`

### Pipeline de transformation
```
FF JSON brut → FFEvent → filtre impact + currency → NormalizedEvent → merge storage JSON
```

### Résolution event_type (ordre de priorité)
1. CB spécifiques (fomc, ecb, boe, boj, snb, boc, rba, rbnz)
2. "interest rate" générique
3. CPI, PPI, NFP, UNEMPLOYMENT, GDP
4. ISM (avant PMI)
5. PMI spécifiques (manufacturing, services, composite)
6. PMI générique
7. Autres (retail sales, trade balance, etc.)

### Filtres actifs
- `IMPACT_FILTER = {"High", "Medium"}` (Low ignoré)
- `COUNTRY_TO_CURRENCY` : 8 devises majeures uniquement
- Déduplication par `(date, event_type)` — préfère les données avec `actual`

### Fix V7.1 (appliqué)
Impact normalisé en `.title()` dans `_parse_raw` pour gérer la casse variable de l'API FF.

### Output : NormalizedEvent
```python
@dataclass
class NormalizedEvent:
    date: str              # "2026-03-07"
    currency: str          # "USD"
    event_type: str        # "NFP"
    title: str
    expected: Optional[float]
    actual: Optional[float]
    surprise: str          # "positive" | "negative" | "neutre"
    tone: str              # "hawkish" | "dovish" | etc.
    impact: str            # "fort_positif" | "positif" | "negatif" | etc.
    theme: str             # "emploi" | "inflation" | "politique_monetaire" | etc.
    summary: str
    is_upcoming: bool
```

---

## Module 7 — Rapport Discord (report.py) — V6

### Structure de l'embed hebdomadaire
| Bloc | Titre | Contenu |
|------|-------|---------|
| 1 | 📅 Semaine | Date + thème global |
| 2 | 🌍 Climat | RiskEnvironment label + score |
| 3 | 💚 Soutenues | Devises hawkish + narratif |
| 4 | 🔴 Fragiles | Devises dovish + narratif |
| 5 | ⚪ Neutres | Devises neutres |
| 6 | 🔄 Retournements | Narratifs ayant changé de ton (V4) |
| 7 | 📅 Prochains événements | Top 5 CB + macro à venir |
| 8 | 🏦 Taux directeurs | Tableau carry différentiel (V6) |
| 9 | 🎯 Setups paires | Top 8 paires tradables par conviction (V6) |
| 10 | 📊 DSI | Data Surprise Index par devise (V5) |
| 11 | 📈 Évolutions | Scénarios et probabilités |
| 12 | 📰 News | Headlines Reuters/Bloomberg |
| Footer | — | Version V6 |

---

## Stockage des données

### Format {DEVISE}_events.json
```json
{
  "currency": "USD",
  "updated_at": "2026-03-19T00:00:00",
  "events": [
    {
      "date": "2026-03-19",
      "currency": "USD",
      "event_type": "FOMC",
      "title": "FOMC Rate Decision",
      "expected": 4.5,
      "actual": 4.5,
      "surprise": "neutre",
      "tone": "neutre",
      "impact": "neutre",
      "theme": "politique_monetaire",
      "summary": "...",
      "is_upcoming": false
    }
  ]
}
```

### Format {DEVISE}_narrative.json
```json
{
  "currency": "USD",
  "central_bank": "Fed",
  "cb_tone": "hawkish",
  "interest_rate": 4.50,
  "inflation_level": "moderee",
  "inflation_trend": "baisse",
  "growth": "modere",
  "employment": "solide",
  "key_themes": ["taux_directeurs", "inflation"]
}
```

### SQLite — narrative_history.db
Table `narrative_history` : (date, currency, dominant_tone, currency_bias, coherence_score).
Utilisée pour détecter les retournements de narratif semaine sur semaine.

---

## Commandes Discord disponibles

| Commande | Description |
|----------|-------------|
| `/macro_weekly` | Rapport hebdomadaire complet (12 blocs) |
| `/refresh [live_data]` | Mise à jour données + scrape ForexFactory si live_data=True |
| `/currency_macro <devise>` | Analyse détaillée d'une devise |
| `/narrative <devise>` | Narratif + raisons + cohérence |
| `/upcoming_events` | Calendrier macro à venir |
| `/scenario <devise>` | Scénarios haussier/baissier avec probabilités |

---

## Variables d'environnement (.env)

```
DISCORD_TOKEN=...
DISCORD_GUILD_ID=...
DISCORD_CHANNEL_ID=...
NEWS_API_KEY=...           # Optionnel — Reuters RSS fonctionne sans
```

---

## Dépendances (requirements.txt)

```
discord.py >= 2.3.0
aiohttp
apscheduler
python-dotenv
feedparser       # Reuters RSS
```
Pas de pandas, numpy, ou bibliothèques ML. Tout est en stdlib Python + discord.py.

---

## État actuel du projet

### Fonctionnel ✅
- Pipeline complet 11 étapes
- 8 devises, 20 paires analysées
- Moteur narratif à règles
- RiskEnvironment (risk-on/off/neutral)
- DSI (Data Surprise Index)
- CBCalendarValidator avec calendrier officiel 2026
- Currency Pairs Engine avec carry scoring
- Rapport Discord V6 (12 blocs)
- ForexFactory Scraper (thisweek — lastweek/nextweek en 404)

### Bug en cours de résolution 🔧
ForexFactory scraper : 85 événements bruts récupérés, 0 normalisés. Fix appliqué (`.title()` sur le champ impact). Résultat non encore confirmé.

### Données
Actuellement les fichiers `{DEVISE}_events.json` contiennent des données partiellement mockées. Le scraper ForexFactory a pour objectif de les remplacer par des données réelles.

---

## Architecture cible — Application Web "Lucid"

Le bot est conçu pour alimenter une future application web pour traders retail avec :
- **Movements** : Top paires → `pair_setups` du moteur V6
- **Overview** : Currency strength → `currency_bias` des narratifs
- **Calendar** : Événements à venir → `ForexFactory scraper` (nextweek)
- **Learn** : Explications macro simplifiées, sans jargon

Données disponibles en sortie du pipeline pour exposition via API REST ou JSON statique.

# Lucid Events — Module Standalone

Transforme des événements macro bruts en alertes pédagogiques pour l'application **Lucid**.

> "Moins d'informations, plus de compréhension."

---

## Lancer le test

```bash
cd lucid_events_dev/
python test_lucid_events.py
```

Sortie attendue :

```
=======================================================
  LUCID EVENTS — 2026-05-02
=======================================================

── TODAY  (3 events) ──────────────────────────────────
  1. US CPI  🔴 HIGH
     USD · Today
     Expected: 0.2%
     Why it matters:  US inflation drives Fed rate expectations...
     Market focus:    Core CPI vs headline — the Fed excludes food...
     Lucid insight:   Markets react to the surprise vs expectations...

  2. Powell Speech  🔴 HIGH  [CB Speech — Powell]
     USD · Today
     Why it matters:  Fed Chair Powell's comments shape global rate...
     ...
```

### Options

| Commande | Description |
|----------|-------------|
| `python test_lucid_events.py` | Sortie console par défaut |
| `python test_lucid_events.py --verbose` | Logs détaillés + stats |
| `python test_lucid_events.py --json` | Sortie JSON brute |
| `python test_lucid_events.py --tests-only` | Tests unitaires uniquement |
| `python test_lucid_events.py --file events.json` | Fichier JSON custom |

---

## Structure du module

```
lucid_events_dev/
├── lucid_event_engine.py   # Moteur principal (standalone, stdlib only)
├── test_lucid_events.py    # Script de test + tests unitaires intégrés
├── sample_events.json      # 16 événements réalistes (dont 3 filtrés)
└── README.md               # Ce fichier
```

### lucid_event_engine.py — Architecture interne

```
Section 1  — Dataclasses   : RawEvent, LucidEvent
Section 2  — Importance    : normalize_importance() — gère High/high/HIGH/haute/moyen/3...
Section 3  — CB Speeches   : detect_cb_speech() — 15 speakers + mots-clés génériques
Section 4  — Priorité      : _EVENT_PRIORITY — tri des types d'événements
Section 5  — Contenu       : CONTENT + SPEAKER_CONTENT — 20 types × devise
Section 6  — Titres courts : make_short_title() — "Non-Farm Employment Change" → "US NFP"
Section 7  — Timing        : compute_timing() — "Today" | "Tomorrow" | "Wednesday" | "Fri May 9"
Section 8  — Résolution    : resolve_content() — waterfall de fallback
Section 9  — Parser JSON   : load_events_from_json() + parse_raw_event()
Section 10 — Moteur        : LucidEventEngine.build() → (today_events, weekly_events)
```

---

## Format du JSON d'entrée

```json
[
  {
    "date":       "2026-05-07",
    "currency":   "USD",
    "event_type": "CPI",
    "title":      "Consumer Price Index (CPI) m/m",
    "importance": "High",
    "expected":   "0.2%",
    "previous":   "0.3%",
    "actual":     null,
    "note":       "Most watched release of the week"
  }
]
```

**Champs requis** : `date`, `currency`, `title`  
**Champs optionnels** : `event_type` (défaut `OTHER`), `importance` (défaut `low`), `expected`, `previous`, `actual`, `note`

### Valeurs d'importance acceptées (case-insensitive)

| Valeur | Résultat |
|--------|----------|
| `High`, `HIGH`, `haute`, `fort`, `3` | `high` |
| `Medium`, `medium`, `moyenne`, `moyen`, `2` | `medium` |
| `Low`, `low`, `faible`, `1`, *(vide/inconnu)* | `low` |

### event_type supportés

`FOMC` `ECB` `BOE` `BOJ` `SNB` `BOC` `RBA` `RBNZ` `INTEREST_RATE`  
`CPI` `PPI` `NFP` `UNEMPLOYMENT` `GDP`  
`ISM` `PMI_COMPOSITE` `PMI_MFG` `PMI_SERVICES`  
`RETAIL_SALES` `CONSUMER_CONFIDENCE` `TRADE_BALANCE` `INDUSTRIAL_PRODUCTION`  
`OTHER` *(fallback pour tout type inconnu)*

### Détection automatique des speeches CB

Le moteur détecte les discours CB depuis le titre, même si `event_type = OTHER` :

| Titre | Speaker détecté |
|-------|----------------|
| `Powell Speaks — Testimony` | `Powell` |
| `Lagarde Speech — ECB Forum` | `Lagarde` |
| `Ueda Press Conference` | `Ueda` |
| `Fed Governor Waller Remarks` | `Fed Official` |
| `BOE Bailey Economic Outlook` | `Bailey` |
| `Central Bank Statement` | *(speech générique)* |

---

## Logique de filtrage

```
RawEvent → Filtre → LucidEvent

Règle INCLURE si :
  importance == "high"
  OU importance == "medium"
  OU event_type in CB_MEETINGS (FOMC, ECB, BOE…)
  OU is_cb_speech == True

Règle EXCLURE si :
  importance == "low" ET pas réunion CB ET pas speech CB
  OU date dans le passé

Limites :
  MAX 5 événements TODAY
  MAX 7 événements THIS WEEK
```

---

## Intégration dans macro-scenarios-bot

Une fois validé, ce module s'intègre en 3 étapes :

### Étape 1 — Copier le moteur

```bash
cp lucid_events_dev/lucid_event_engine.py macro-scenarios-bot/modules/
```

### Étape 2 — Adapter les imports

Dans `lucid_event_engine.py`, remplacer le parser JSON standalone par l'input `List[UpcomingEvent]` existant :

```python
# Remplacer RawEvent par UpcomingEvent (même structure)
from modules.calendar_provider import UpcomingEvent

# Dans _convert(), changer la signature :
def _convert(self, ev: UpcomingEvent) -> Optional[LucidEvent]:
    importance = normalize_importance(ev.importance)
    # ... reste identique
```

### Étape 3 — Ajouter à WeeklyMacroSummary

```python
# Dans core/weekly_macro_analysis.py

from modules.lucid_event_engine import LucidEvent, LucidEventEngine

@dataclass
class WeeklyMacroSummary:
    # ... champs existants ...
    lucid_events: List[LucidEvent] = field(default_factory=list)

# Dans build_weekly_summary(), après l'étape upcoming_events :
lucid_events = LucidEventEngine().build(upcoming_events)
```

---

## Philosophie produit

Lucid Events n'est **pas** un calendrier.  
Lucid Events est un **filtre d'attention** :

- Il écarte le bruit (PMI final, confidence secondaire, données low-impact)
- Il explique **pourquoi** l'événement compte dans le contexte actuel
- Il dit **sur quoi le marché va se concentrer** — pas sur quoi trader
- Il donne **une leçon macro** durable, pas un signal éphémère

Aucun LucidEvent ne contient :
- de direction (long/short/haussier/baissier)
- de cible de prix ou de niveau
- de conseil d'investissement
- de signal de trading

---

## Dépendances

**Aucune dépendance externe.**  
Python standard library uniquement : `json`, `dataclasses`, `datetime`, `pathlib`, `typing`, `logging`, `argparse`.

Compatible Python 3.10+.

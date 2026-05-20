"""
test_lucid_events.py — Script de test standalone Lucid Events
=============================================================

Lance :
    python test_lucid_events.py

Résultat attendu :
    =======================================================
    LUCID EVENTS — 2026-05-02
    =======================================================

    === TODAY (3 events) ===
    ...

    === THIS WEEK (4 events) ===
    ...

Options :
    python test_lucid_events.py --verbose     # logs détaillés
    python test_lucid_events.py --json        # sortie brute JSON
    python test_lucid_events.py --file <path> # fichier JSON custom
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List

# Rendre le module importable même si lancé depuis un autre répertoire
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lucid_event_engine import (
    LucidEvent,
    LucidEventEngine,
    load_events_from_json,
)


# ─── Formatage console ─────────────────────────────────────────────────────────

IMPORTANCE_COLORS = {
    "high":   "\033[91m",   # Rouge
    "medium": "\033[93m",   # Jaune
    "low":    "\033[37m",   # Gris
}
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[96m"
GREEN = "\033[92m"


def _importance_badge(importance: str) -> str:
    icons = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    return icons.get(importance, "⚪")


def _cb_badge(ev: LucidEvent) -> str:
    if ev.is_cb_speech and ev.speaker:
        return f"  [CB Speech — {ev.speaker}]"
    if ev.is_cb_speech:
        return "  [CB Speech]"
    return ""


def print_event(ev: LucidEvent, index: int) -> None:
    """Affiche un LucidEvent formaté dans la console."""
    badge   = _importance_badge(ev.importance)
    cb_note = _cb_badge(ev)

    # Ligne titre
    print(f"  {BOLD}{index}. {ev.title}{RESET}  {badge} {ev.importance.upper()}{cb_note}")
    print(f"     {DIM}{ev.currency} · {ev.timing_label}{RESET}")

    # Contexte optionnel
    if ev.expected:
        print(f"     {DIM}Expected: {ev.expected}{RESET}")

    # Contenu pédagogique
    print(f"     {CYAN}Why it matters:{RESET}  {ev.why_it_matters}")
    print(f"     {CYAN}Market focus:  {RESET}  {ev.market_focus}")
    print(f"     {CYAN}Lucid insight: {RESET}  {ev.insight}")
    print()


def print_section(title: str, events: List[LucidEvent], color: str = GREEN) -> None:
    """Affiche une section (TODAY ou THIS WEEK) avec ses événements."""
    count = len(events)
    print(f"{BOLD}{color}{'─' * 55}{RESET}")
    print(f"{BOLD}{color}  {title}  ({count} event{'s' if count != 1 else ''}){RESET}")
    print(f"{BOLD}{color}{'─' * 55}{RESET}")
    print()

    if not events:
        print(f"  {DIM}No events.{RESET}\n")
        return

    for i, ev in enumerate(events, start=1):
        print_event(ev, i)


def print_summary(today: List[LucidEvent], weekly: List[LucidEvent]) -> None:
    """Affiche le résumé compact des événements Lucid."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print()
    print(f"{BOLD}{'=' * 55}{RESET}")
    print(f"{BOLD}  LUCID EVENTS — {today_str}{RESET}")
    print(f"{BOLD}{'=' * 55}{RESET}")
    print()

    print_section("TODAY", today, color="\033[92m")
    print_section("THIS WEEK", weekly, color="\033[96m")

    # Stats finales
    total = len(today) + len(weekly)
    cb_count  = sum(1 for e in today + weekly if e.is_cb_speech)
    high_count = sum(1 for e in today + weekly if e.importance == "high")

    print(f"{DIM}{'─' * 55}{RESET}")
    print(f"{DIM}  Total: {total} events  |  High: {high_count}  |  CB Speeches: {cb_count}{RESET}")
    print(f"{DIM}{'─' * 55}{RESET}")
    print()


# ─── Sortie JSON ───────────────────────────────────────────────────────────────

def to_dict(ev: LucidEvent) -> dict:
    """Convertit un LucidEvent en dict JSON-sérialisable."""
    return {
        "date":           ev.date,
        "currency":       ev.currency,
        "title":          ev.title,
        "event_type":     ev.event_type,
        "importance":     ev.importance,
        "timing_label":   ev.timing_label,
        "is_today":       ev.is_today,
        "is_cb_speech":   ev.is_cb_speech,
        "speaker":        ev.speaker,
        "expected":       ev.expected,
        "why_it_matters": ev.why_it_matters,
        "market_focus":   ev.market_focus,
        "insight":        ev.insight,
        "note":           ev.note,
    }


def print_json_output(today: List[LucidEvent], weekly: List[LucidEvent]) -> None:
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today_events":  [to_dict(e) for e in today],
        "weekly_events": [to_dict(e) for e in weekly],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


# ─── Tests unitaires intégrés ──────────────────────────────────────────────────

def run_unit_tests() -> bool:
    """
    Tests rapides intégrés — vérifie les fonctions clés du moteur.
    Retourne True si tout passe.
    """
    from lucid_event_engine import (
        normalize_importance,
        detect_cb_speech,
        compute_timing,
        resolve_content,
        make_short_title,
        parse_raw_event,
    )

    errors = []

    # Test normalize_importance
    cases_imp = [
        ("High", "high"), ("HIGH", "high"), ("haute", "high"),
        ("medium", "medium"), ("moyenne", "medium"),
        ("Low", "low"), ("faible", "low"), (None, "low"), ("", "low"),
        ("fort_positif", "high"), ("3", "high"), ("2", "medium"), ("1", "low"),
    ]
    for raw, expected in cases_imp:
        result = normalize_importance(raw)
        if result != expected:
            errors.append(f"normalize_importance({raw!r}) → {result!r} (expected {expected!r})")

    # Test detect_cb_speech
    speech_cases = [
        ("Powell Speaks — Semi-Annual Testimony", "OTHER",       True,  "Powell"),
        ("Lagarde Press Conference",              "OTHER",       True,  "Lagarde"),
        ("ECB Rate Decision",                    "ECB",         False, None),
        ("FOMC Rate Decision",                   "FOMC",        False, None),
        ("Ueda Press Conference",                "OTHER",       True,  "Ueda"),
        ("Fed Governor Waller Remarks",          "OTHER",       True,  "Fed Official"),
        ("US CPI Release",                       "CPI",         False, None),
        ("Lagarde Speaks at ECB Forum",          "OTHER",       True,  "Lagarde"),
    ]
    for title, etype, exp_speech, exp_speaker in speech_cases:
        is_speech, speaker = detect_cb_speech(title, etype)
        if is_speech != exp_speech or speaker != exp_speaker:
            errors.append(
                f"detect_cb_speech({title!r}, {etype!r}) → "
                f"({is_speech}, {speaker!r}) expected ({exp_speech}, {exp_speaker!r})"
            )

    # Test compute_timing
    timing_cases = [
        ("2026-05-02", "2026-05-02", "Today",     True),
        ("2026-05-03", "2026-05-02", "Tomorrow",  False),
        ("2026-05-06", "2026-05-02", "Wednesday", False),
        ("2026-05-15", "2026-05-02", "Fri May 15", False),
        ("2026-04-30", "2026-05-02", "Past",      False),
    ]
    for ev_date, today, exp_label, exp_today in timing_cases:
        label, is_today = compute_timing(ev_date, today)
        if label != exp_label or is_today != exp_today:
            errors.append(
                f"compute_timing({ev_date!r}, {today!r}) → "
                f"({label!r}, {is_today}) expected ({exp_label!r}, {exp_today})"
            )

    # Test resolve_content — ne plante pas sur des types inconnus
    try:
        why, focus, insight = resolve_content("UNKNOWN_TYPE", "USD", False, None)
        assert why and focus and insight, "Contenu vide sur type inconnu"
    except Exception as exc:
        errors.append(f"resolve_content(UNKNOWN_TYPE) raised: {exc}")

    # Test parse_raw_event — champs manquants
    incomplete = parse_raw_event({"date": "2026-05-02"})  # sans currency ni title
    if incomplete is not None:
        errors.append("parse_raw_event devrait retourner None pour données incomplètes")

    valid = parse_raw_event({
        "date": "2026-05-02", "currency": "USD",
        "event_type": "CPI", "title": "US CPI", "importance": "High"
    })
    if valid is None:
        errors.append("parse_raw_event devrait réussir avec données valides")

    # Test short title CB speech
    title = make_short_title("Powell Speaks", "USD", "OTHER", True, "Powell")
    if title != "Powell Speech":
        errors.append(f"make_short_title Powell → {title!r} (expected 'Powell Speech')")

    # Rapport
    if errors:
        print(f"\n{BOLD}\033[91m✗ TESTS ÉCHOUÉS ({len(errors)}):{RESET}")
        for err in errors:
            print(f"  • {err}")
        return False
    else:
        print(f"{GREEN}{BOLD}✓ Tous les tests passent ({len(cases_imp) + len(speech_cases) + len(timing_cases) + 3} assertions){RESET}")
        return True


# ─── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Lucid Events Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python test_lucid_events.py
  python test_lucid_events.py --verbose
  python test_lucid_events.py --json
  python test_lucid_events.py --file my_events.json
  python test_lucid_events.py --tests-only
        """,
    )
    parser.add_argument("--verbose",    action="store_true", help="Active les logs DEBUG")
    parser.add_argument("--json",       action="store_true", help="Sortie JSON brute")
    parser.add_argument("--tests-only", action="store_true", help="Lance uniquement les tests unitaires")
    parser.add_argument("--file",       default="sample_events.json", help="Fichier JSON source")
    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s  %(name)s : %(message)s",
    )

    # Tests unitaires
    print()
    print(f"{BOLD}{'─' * 55}{RESET}")
    print(f"{BOLD}  UNIT TESTS{RESET}")
    print(f"{BOLD}{'─' * 55}{RESET}")
    ok = run_unit_tests()
    print()

    if args.tests_only:
        sys.exit(0 if ok else 1)

    # Chargement des événements
    script_dir = os.path.dirname(os.path.abspath(__file__))
    events_path = args.file if os.path.isabs(args.file) else os.path.join(script_dir, args.file)

    raw_events = load_events_from_json(events_path)
    if not raw_events:
        print(f"\033[91mErreur : aucun événement chargé depuis {events_path}{RESET}")
        sys.exit(1)

    if args.verbose:
        print(f"{DIM}Chargé {len(raw_events)} événements bruts depuis {events_path}{RESET}\n")

    # Moteur
    engine = LucidEventEngine()
    today_events, weekly_events = engine.build(raw_events)

    # Sortie
    if args.json:
        print_json_output(today_events, weekly_events)
    else:
        print_summary(today_events, weekly_events)

    if args.verbose:
        # Stats détaillées
        print(f"\n{DIM}─── Stats détaillées ───{RESET}")
        all_evs = today_events + weekly_events
        filtered = len(raw_events) - len(all_evs)
        print(f"{DIM}  Bruts chargés : {len(raw_events)}{RESET}")
        print(f"{DIM}  Filtrés (low importance, non-CB) : {filtered}{RESET}")
        print(f"{DIM}  Today : {len(today_events)}{RESET}")
        print(f"{DIM}  Weekly : {len(weekly_events)}{RESET}")
        for ev in all_evs:
            print(f"{DIM}  [{ev.timing_label:12}] {ev.title:30} {ev.event_type:18} speech={ev.is_cb_speech}{RESET}")


if __name__ == "__main__":
    main()

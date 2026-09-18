"""Konnektoren: holen aus einem Quellsystem des Hauses, bilden auf den Feed-Vertrag ab, schieben an die Schnittstelle.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Ein Konnektor liegt hier und nie in ``restwert/``: er darf ``httpx`` importieren,
der Motor nicht (``tests/test_no_side_effects.py``). Jeder Konnektor hat eine
Mapping-Datei unter ``connectors/mappings/`` (Spalte des Hauses auf Spalte des
Feeds), eine Wasserzeichen-Datei unter ``connectors/state/`` (was zuletzt
verarbeitet wurde; per ``.gitignore`` ausgeschlossen) und einen ``--dry-run``.
Wie ein zweiter Konnektor entsteht: ``connectors/README.md``.
"""

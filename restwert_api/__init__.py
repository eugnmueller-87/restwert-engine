"""Restwert Engine: die Schnittstelle (v0.4).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Ein eigenes Paket neben ``restwert/``, mit Absicht: der Motor bleibt frei von
Netzwerkbibliotheken (``tests/test_no_side_effects.py`` greppt ``restwert/`` auf
``smtplib``, ``requests``, ``httpx``, ``urllib.request``, ``boto3``, ``paramiko``
und ``subprocess``). Die Schnittstelle importiert den Motor, nie umgekehrt.

Was sie tut: Quellsysteme des Hauses liefern ihre Feeds per HTTP an
``POST /v1/feeds/<system>/<feed>``; jede Lieferung landet als Datei unter
``data/lake/raw/<system>/<feed>/<YYYY-MM-DD>_<feed>_<seq>.csv`` mit
``is_synthetic=false`` und geht durch denselben Import wie eine Handlieferung
(``restwert.lake.ingest.ingest_file``: Typisieren, Schlüssel auflösen,
Deduplizieren per SHA-256, ``bronze.deliveries``). ``POST /v1/runs`` stößt die
Kette ``ingest`` bis ``export`` zum Stichtag an, seriell in einem Arbeitsfaden.
``GET /v1/kpis/latest`` liest ``gold.kpi_values`` zum jüngsten Stichtag.

Was sie nicht tut: nichts nach außen rufen, nichts generieren, nie ``all``
(das löscht Datenbank und Landung), nie eine gelandete Datei ändern, nie eine
Kennzeichnungszeile (``# SYNTHETIC DATA``) schreiben, nie einen Grundcode
erfinden. Jeder Aufruf steht im Audit-Log ``data/api_audit.jsonl`` mit der
Schlüsselkennung, nie mit dem Geheimnis.
"""

from restwert import GOVERNANCE_PRINCIPLE, __version__

API_VERSION = "0.4.0"

__all__ = ["API_VERSION", "GOVERNANCE_PRINCIPLE", "__version__"]

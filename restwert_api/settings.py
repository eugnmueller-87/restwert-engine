"""Einstellungen der Schnittstelle: Pfade, Adresse, Schlüsselquelle. Alles aus der Umgebung, alles mit Standard.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

| Umgebungsvariable          | Standard                          | Bedeutung |
|----------------------------|-----------------------------------|-----------|
| ``RESTWERT_API_DB``        | ``data/restwert.duckdb``          | die eine Datenbankdatei |
| ``RESTWERT_API_LAKE_DIR``  | ``data/lake``                     | Landung liegt unter ``<lake>/raw`` |
| ``RESTWERT_API_OUT``       | ``outputs``                       | Ziel des Schritts ``export`` |
| ``RESTWERT_API_AUDIT``     | ``data/api_audit.jsonl``          | Audit-Log, eine JSON-Zeile je Aufruf |
| ``RESTWERT_API_RUNS_DIR``  | ``data/api_runs``                 | Zustand je Lauf als JSON-Datei |
| ``RESTWERT_API_KEYS_FILE`` | ``config/api_keys.yaml``          | Schlüsseldatei (nicht im Repo; Vorlage ``config/api_keys.example.yaml``) |
| ``RESTWERT_API_KEYS``      | leer                              | Schlüssel als Text, Alternative zur Datei (``auth.py``) |
| ``RESTWERT_API_HOST``      | ``127.0.0.1``                     | Adresse des Servers |
| ``RESTWERT_API_PORT``      | ``8420``                          | Port des Servers |
| ``RESTWERT_API_MAX_BODY_MB`` | ``25``                          | Obergrenze je Request-Körper in MiB; darüber 413, bevor eine Zeile gelesen wird |
| ``RESTWERT_API_MAX_ROWS``  | ``50000``                         | Obergrenze je Lieferung in Zeilen (JSON wie CSV); darüber 413, höchstens ``MAX_ROWS_CEILING`` |

Die Grenzen sind eine Entscheidung gegen den offenen Schlauch: ohne sie liest die
Schnittstelle jeden Körper vollständig in den Speicher, bevor sie ihn prüft. Wer mehr
Zeilen hat, liefert in Teilen (der Konnektor teilt selbst, ``api.batch_size``); die
Deduplizierung per SHA-256 und ``row_hash`` macht Teile gefahrlos.

Die Ablageorte sind eine Entscheidung, nicht ein Zufall: ``data/`` ist der Ort der
Laufzeitdaten, ``outputs/`` ist per ``.gitignore`` komplett ignoriert. Audit-Log und
Laufzustand liegen deshalb unter ``data/`` und sind dort eigens ignoriert
(``data/api_audit.jsonl``, ``data/api_runs/``): sie sind Betriebsdaten des Hauses und
gehören nie in Git.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from restwert.paths import CONFIG_DIR, DATA_DIR, DEFAULT_DB, LAKE_DIR, OUTPUTS_DIR

DEFAULT_PORT = 8420
DEFAULT_HOST = "127.0.0.1"
DEFAULT_MAX_BODY_MB = 25
DEFAULT_MAX_ROWS = 50_000
MAX_ROWS_CEILING = 200_000  # auch die Umgebung hebt die Zeilengrenze nicht darüber; zugleich max_length im Schema


@dataclass(frozen=True)
class Settings:
    """Alles, was die App zum Start braucht; unveränderlich, damit ein Test seine Kopie bekommt."""

    db_path: Path = DEFAULT_DB
    lake_dir: Path = LAKE_DIR
    out_dir: Path = OUTPUTS_DIR
    audit_path: Path = DATA_DIR / "api_audit.jsonl"
    runs_dir: Path = DATA_DIR / "api_runs"
    keys_file: Path = CONFIG_DIR / "api_keys.yaml"
    keys_env: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_body_bytes: int = DEFAULT_MAX_BODY_MB * 1024 * 1024
    max_rows: int = DEFAULT_MAX_ROWS

    def __post_init__(self) -> None:
        if self.max_body_bytes < 1:
            raise ValueError(f"max_body_bytes muss mindestens 1 sein, ist {self.max_body_bytes}")
        if not 1 <= self.max_rows <= MAX_ROWS_CEILING:
            raise ValueError(f"max_rows muss zwischen 1 und {MAX_ROWS_CEILING} liegen, ist {self.max_rows}")

    @property
    def raw_dir(self) -> Path:
        return Path(self.lake_dir) / "raw"

    def with_paths(self, base: Path) -> "Settings":
        """Alle Laufzeitpfade unter ``base`` (für Tests und Rauchtests, nie das Repo berühren)."""
        base = Path(base)
        return replace(
            self,
            db_path=base / "restwert.duckdb",
            lake_dir=base / "lake",
            out_dir=base / "outputs",
            audit_path=base / "api_audit.jsonl",
            runs_dir=base / "api_runs",
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        base = cls()

        def _p(name: str, default: Path) -> Path:
            v = e.get(name)
            return Path(v) if v else default

        def _int(name: str, default: int) -> int:
            text = e.get(name, "").strip()
            if not text:
                return default
            try:
                return int(text)
            except ValueError as exc:
                raise ValueError(f"{name} ist keine ganze Zahl: {text!r}") from exc

        port = _int("RESTWERT_API_PORT", DEFAULT_PORT)
        max_body_mb = _int("RESTWERT_API_MAX_BODY_MB", DEFAULT_MAX_BODY_MB)
        if max_body_mb < 1:
            raise ValueError(f"RESTWERT_API_MAX_BODY_MB muss mindestens 1 sein, ist {max_body_mb}")
        max_rows = _int("RESTWERT_API_MAX_ROWS", DEFAULT_MAX_ROWS)
        if not 1 <= max_rows <= MAX_ROWS_CEILING:
            raise ValueError(f"RESTWERT_API_MAX_ROWS muss zwischen 1 und {MAX_ROWS_CEILING} liegen, ist {max_rows}")
        return cls(
            db_path=_p("RESTWERT_API_DB", base.db_path),
            lake_dir=_p("RESTWERT_API_LAKE_DIR", base.lake_dir),
            out_dir=_p("RESTWERT_API_OUT", base.out_dir),
            audit_path=_p("RESTWERT_API_AUDIT", base.audit_path),
            runs_dir=_p("RESTWERT_API_RUNS_DIR", base.runs_dir),
            keys_file=_p("RESTWERT_API_KEYS_FILE", base.keys_file),
            keys_env=(e.get("RESTWERT_API_KEYS") or None),
            host=e.get("RESTWERT_API_HOST", DEFAULT_HOST) or DEFAULT_HOST,
            port=port,
            max_body_bytes=max_body_mb * 1024 * 1024,
            max_rows=max_rows,
        )


__all__ = ["Settings", "DEFAULT_PORT", "DEFAULT_HOST", "DEFAULT_MAX_BODY_MB", "DEFAULT_MAX_ROWS", "MAX_ROWS_CEILING"]

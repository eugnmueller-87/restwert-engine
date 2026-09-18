"""API-Schlüssel je Quellsystem: Datei oder Umgebungsvariable, nie im Repo, fail closed.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Ein Schlüssel hat eine Kennung (``key_id``, steht im Audit-Log), ein Geheimnis
(steht nirgends außer in der Schlüsseldatei oder der Umgebung), die Quellsysteme,
für die er liefern darf (``source_systems``, ``*`` für alle), und ``may_run``,
ob er Läufe auslösen darf. Der Aufrufer schickt das Geheimnis im Header
``X-API-Key``.

Quellen, beide erlaubt und vereinigt:

* ``config/api_keys.yaml`` (Vorlage: ``config/api_keys.example.yaml``; die echte
  Datei ist per ``.gitignore`` ausgeschlossen)::

      keys:
        erp-export:
          secret: "..."
          source_systems: [erp]
        scheduler:
          secret: "..."
          source_systems: ["*"]
          may_run: true

* ``RESTWERT_API_KEYS`` als Text, ein Eintrag je ``;``, Form
  ``<key_id>:<secret>:<system>+<system>[:run]``, zum Beispiel
  ``erp-export:GEHEIM1:erp;scheduler:GEHEIM2:*:run``.

Dieselbe Härte wie ``restwert/config.py`` bei Schwellen ohne Owner: ein Schlüssel
ohne Geheimnis, ohne Quellsystem oder mit einem Quellsystem, das kein Feed kennt,
bricht den Start ab. Ohne einen einzigen Schlüssel antwortet jede geschützte Route
mit 503, nie mit einem stillen Durchlass.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from pathlib import Path

import yaml

from restwert.lake.feeds import FEEDS

SOURCE_SYSTEMS: frozenset[str] = frozenset(spec.source_system for spec in FEEDS.values())
HEADER_NAME = "X-API-Key"
ANY_SYSTEM = "*"


@dataclass(frozen=True)
class ApiKey:
    key_id: str
    secret: str
    source_systems: frozenset[str]
    may_run: bool = False

    def allows_system(self, source_system: str) -> bool:
        return ANY_SYSTEM in self.source_systems or source_system in self.source_systems


class KeyRing:
    """Alle bekannten Schlüssel; ``authenticate`` vergleicht in konstanter Zeit."""

    def __init__(self, keys: list[ApiKey] | None = None) -> None:
        self._keys: dict[str, ApiKey] = {}
        for k in keys or []:
            self.add(k)

    def add(self, key: ApiKey) -> None:
        _validate(key)
        if key.key_id in self._keys:
            raise ValueError(f"API-Schlüssel {key.key_id!r} ist doppelt definiert")
        self._keys[key.key_id] = key

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def key_ids(self) -> list[str]:
        return sorted(self._keys)

    def authenticate(self, secret: str | None) -> ApiKey | None:
        if not secret:
            return None
        found: ApiKey | None = None
        for key in self._keys.values():
            # jeder Schlüssel wird verglichen, damit die Antwortzeit nichts über den Treffer verrät
            if hmac.compare_digest(key.secret.encode("utf-8"), secret.encode("utf-8")):
                found = key
        return found

    @classmethod
    def load(cls, keys_file: Path | None, keys_env: str | None) -> "KeyRing":
        ring = cls()
        if keys_file is not None and Path(keys_file).exists():
            for key in parse_keys_file(Path(keys_file)):
                ring.add(key)
        if keys_env:
            for key in parse_keys_env(keys_env):
                ring.add(key)
        return ring


def _validate(key: ApiKey) -> None:
    if not key.key_id or not key.key_id.strip():
        raise ValueError("API-Schlüssel ohne Kennung (key_id)")
    if not key.secret or len(key.secret) < 8:
        raise ValueError(f"API-Schlüssel {key.key_id!r}: das Geheimnis fehlt oder ist kürzer als 8 Zeichen")
    if not key.source_systems:
        raise ValueError(f"API-Schlüssel {key.key_id!r}: kein Quellsystem (source_systems)")
    unknown = sorted(s for s in key.source_systems if s != ANY_SYSTEM and s not in SOURCE_SYSTEMS)
    if unknown:
        raise ValueError(
            f"API-Schlüssel {key.key_id!r}: unbekannte Quellsysteme {unknown}; "
            f"bekannt: {', '.join(sorted(SOURCE_SYSTEMS))} oder *"
        )


def parse_keys_file(path: Path) -> list[ApiKey]:
    """``config/api_keys.yaml`` lesen (Form siehe Moduldokumentation)."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or not isinstance(raw.get("keys"), dict):
        raise ValueError(f"{path}: oberste Ebene muss ein Mapping mit dem Schlüssel 'keys' sein")
    out: list[ApiKey] = []
    for key_id, spec in raw["keys"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"{path}: Schlüssel {key_id!r} ist kein Mapping")
        systems = spec.get("source_systems") or []
        if isinstance(systems, str):
            systems = [systems]
        out.append(
            ApiKey(
                key_id=str(key_id),
                secret=str(spec.get("secret") or ""),
                source_systems=frozenset(str(s) for s in systems),
                may_run=bool(spec.get("may_run", False)),
            )
        )
    return out


def parse_keys_env(text: str) -> list[ApiKey]:
    """``RESTWERT_API_KEYS`` lesen: ``id:secret:sys+sys[:run];...``."""
    out: list[ApiKey] = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) not in (3, 4):
            raise ValueError(
                "RESTWERT_API_KEYS: jeder Eintrag hat die Form <key_id>:<secret>:<system>+<system>[:run]"
            )
        key_id, secret, systems = parts[0].strip(), parts[1], parts[2]
        may_run = len(parts) == 4 and parts[3].strip().lower() == "run"
        out.append(
            ApiKey(
                key_id=key_id,
                secret=secret,
                source_systems=frozenset(s.strip() for s in systems.split("+") if s.strip()),
                may_run=may_run,
            )
        )
    return out


__all__ = ["ApiKey", "KeyRing", "HEADER_NAME", "ANY_SYSTEM", "SOURCE_SYSTEMS", "parse_keys_file", "parse_keys_env"]

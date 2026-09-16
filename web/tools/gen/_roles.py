# -*- coding: utf-8 -*-
"""One German spelling per role on every tab. The configuration files carry English placeholders
("Head of Recommerce (name)"); the page speaks German. Every generator passes its serialised JSON
through ``de_roles`` before writing, so a role reads the same on Bericht, TCO, Kreislauf and Stellschrauben.
The motors carry the same table in web/engine/_helpers.js (E.fmt.role) for texts they compose themselves."""
ROLE_DE = [
    ("Head of Service Operations", "Leitung Service"),
    ("Head of Recommerce", "Leitung Recommerce"),
    ("Head of Indirect Procurement", "Leitung Indirekter Einkauf"),
    ("Head of Procurement", "Einkaufsleitung"),
    ("Head of Customer Success", "Leitung Customer Success"),
    ("Data owner", "Dateneigner"),
]


def de_roles(text: str) -> str:
    text = text.replace(" (name)", "")
    for en, de in ROLE_DE:
        text = text.replace(en, de)
    return text

"""Module entry point: ``python -m restwert <command>``.

Implements SPEC section 8.1 (module 6, app). The governance sentence that every
command prints once before doing anything:

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from restwert.cli import main

raise SystemExit(main())

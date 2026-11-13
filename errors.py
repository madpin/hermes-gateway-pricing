"""Exception -> HTTP status mapping for the dashboard backend."""

from __future__ import annotations

from .backfill import BackfillError
from .hermes_compat import CompatError
from .statedb import SchemaError

HTTP_ERRORS = (ValueError, BackfillError, SchemaError, CompatError, FileNotFoundError)


def status_for(exc: BaseException) -> int:
    if isinstance(exc, CompatError):
        return 503  # the pricing wrap is not active in this process
    if isinstance(exc, FileNotFoundError):
        return 404  # no state.db for this profile yet
    if isinstance(exc, (SchemaError, BackfillError)):
        return 409  # refused: unexpected schema, missing confirm, busy database
    return 400

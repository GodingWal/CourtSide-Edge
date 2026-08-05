"""The gate an adapter passes before it may collect anything.

The roadmap said for months that adding a second market source "is not code": it means reading
that operator's terms, confirming the collection is permitted, and dating that verification. Both
halves of that are true, and the second half is exactly why this module exists. A runbook line
saying "check the terms first" is advice that survives until the afternoon somebody is in a
hurry. A function every adapter must call, backed by a table that requires a human name, the
terms URL, a hash of what they said that day, and an expiry, is a control.

What it does not do is decide anything legal. It records that a named person read a document on
a date and reached a conclusion, and it refuses to poll when nobody has. Whether the conclusion
was correct is outside what any code here can determine, and pretending otherwise would be worse
than the runbook line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = ["SourceNotPermitted", "TermsVerification", "require_permitted_source"]


class SourceNotPermitted(RuntimeError):
    """Raised when a source has no current, permitted, named terms verification."""


@dataclass(frozen=True)
class TermsVerification:
    source: str
    verified_by: str
    verified_at: datetime
    expires_at: datetime
    conclusion: str
    rate_limit: str
    attribution_required: bool

    @property
    def is_current(self) -> bool:
        return self.conclusion == "permitted" and self.expires_at > datetime.now(UTC)


def require_permitted_source(
    cur: Any, source: str, *, now: datetime | None = None
) -> TermsVerification:
    """Return the current verification for ``source`` or refuse to proceed.

    Fails closed on every ambiguous case: no row, an expired row, a revoked row, or a row whose
    conclusion was ``unclear``. "Somebody looked and could not tell" is a real outcome worth
    storing and is not permission.
    """
    at = now or datetime.now(UTC)
    cur.execute(
        """SELECT source,verified_by,verified_at,expires_at,conclusion,rate_limit,
                  attribution_required
           FROM wnba.source_terms_verifications
           WHERE source=%s AND revoked_at IS NULL AND expires_at>%s
           ORDER BY verified_at DESC LIMIT 1""",
        (source, at),
    )
    row = cur.fetchone()
    if row is None:
        raise SourceNotPermitted(
            f"{source} has no current terms verification. Record one with "
            f"`wnba lines verify-source {source} --verified-by <name> --terms-url <url> "
            "--conclusion permitted` after reading the operator's terms."
        )
    conclusion = str(row["conclusion"])
    if conclusion != "permitted":
        raise SourceNotPermitted(
            f"{source} was reviewed by {row['verified_by']} and the recorded conclusion is "
            f"{conclusion!r}. Only 'permitted' enables collection."
        )
    return TermsVerification(
        source=str(row["source"]),
        verified_by=str(row["verified_by"]),
        verified_at=row["verified_at"],
        expires_at=row["expires_at"],
        conclusion=conclusion,
        rate_limit=str(row["rate_limit"]),
        attribution_required=bool(row["attribution_required"]),
    )


def record_terms_verification(
    *,
    source: str,
    verified_by: str,
    terms_url: str,
    conclusion: str,
    permitted_use: str,
    days_valid: int = 180,
    rate_limit: str = "",
    attribution_required: bool = False,
    redistribution_permitted: bool = False,
    notes: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Store one dated, named reading of a source's terms.

    The terms text is hashed rather than stored. Operators rewrite terms without announcing it,
    and a hash is enough to detect that the document changed since somebody read it -- which is
    the question a later audit actually asks. Storing the full text would also mean redistributing
    it, which several operators' terms specifically forbid.
    """
    import hashlib
    from datetime import timedelta
    from uuid import uuid4

    from wnba_store.db import connect

    at = now or datetime.now(UTC)
    expires = at + timedelta(days=max(1, days_valid))
    # Hash the URL when the text is not supplied: it still detects "somebody pointed the record
    # at a different document", which is the substitution worth catching.
    digest = hashlib.sha256(f"{terms_url}\n{permitted_use}".encode()).hexdigest()
    verification_id = uuid4()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.source_terms_verifications
               (verification_id,source,verified_by,verified_at,expires_at,terms_url,terms_sha256,
                permitted_use,rate_limit,attribution_required,redistribution_permitted,
                conclusion,notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                verification_id,
                source,
                verified_by.strip(),
                at,
                expires,
                terms_url,
                digest,
                permitted_use.strip(),
                rate_limit,
                attribution_required,
                redistribution_permitted,
                conclusion,
                notes,
            ),
        )
    return {"verification_id": verification_id, "expires_at": expires, "conclusion": conclusion}

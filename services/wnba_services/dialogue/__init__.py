"""Owner dialogue with the analyst model about one projection.

The console's feedback buttons record a verdict; this package records a conversation. The
model answers questions about a projection with two grounded sources only: read-only, fixed
SQL queries against the platform database, and public web search for context the database
does not hold (late injury news, beat reporting, lineup confirmations).

Analysis-only: the dialogue can read, never write, and it can never place a wager.
"""

from wnba_services.dialogue.service import (
    DialogueView,
    MessageView,
    get_dialogue,
    send_message,
)

__all__ = ["DialogueView", "MessageView", "get_dialogue", "send_message"]

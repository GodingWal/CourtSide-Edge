"""Dialogue turns for one projection: grounded model replies, persisted verbatim.

The model answers with tool calls, never from memory alone. Two tool families exist:

- ``search_web`` -- public web results for news the database does not hold;
- fixed, parameterized database reads -- projection detail, recent box scores, quote
  history, and the research desk's findings for this exact projection.

There is deliberately no free-SQL tool. A model that composes its own queries can
hallucinate a table and "read" from it; a model choosing among five fixed reads cannot.

Every message -- including the model's tool-call envelopes -- is stored with the exact
payload sent to or received from the provider, so the next turn replays the transcript
verbatim. The transcript is the audit trail: an answer that cites a tool result can be
checked against the row that produced it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from wnba_store.db import connect

from wnba_services.dialogue.web_search import search_web
from wnba_services.research_agents.deepseek import DeepSeekResearchClient

# Tool rounds are bounded so a model that keeps asking for more context cannot hold the
# request open past the owner's patience; four reads plus a web search answer nearly every
# question a single projection raises.
MAX_TOOL_ROUNDS = 4
# Transcript replay is bounded so a long conversation cannot blow the context window; the
# oldest turns drop out of the provider request but stay stored for audit.
MAX_REPLAY_MESSAGES = 40
REPLY_MAX_TOKENS = 2000
MAX_MESSAGE_CHARS = 4000
MAX_TOOL_RESULT_CHARS = 6000

SYSTEM_PROMPT = """You are the resident analyst for CourtSide Edge, a WNBA player-prop
decision-support console, discussing one specific forecast with its owner.

Rules you do not break:
- Analysis only. This system never logs into a sportsbook, never places a wager, and never
  moves money. Do not frame answers as betting instructions; frame them as evidence.
- Ground every factual claim in a tool result or in the projection context below. If neither
  supports it, say what you do not know instead of filling the gap.
- Use search_web for context the database cannot hold: late injury news, beat reporting,
  lineup confirmations, roster moves. Prefer recent, specific queries.
- Quote tool data with its source ("the quote history shows...", "recent games show...") so
  the owner can check the transcript against the claim.
- Be direct and concise. The owner reads every reply on a console panel, not in a report.

Current projection context (authoritative; do not contradict it):
"""


@dataclass(frozen=True)
class MessageView:
    role: str
    content: str
    created_at: datetime
    tool_name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True)
class DialogueView:
    dialogue_id: UUID
    projection_id: UUID
    messages: list[MessageView] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "dialogue_id": str(self.dialogue_id),
            "projection_id": str(self.projection_id),
            "messages": [message.to_payload() for message in self.messages],
        }


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    return str(obj)


def _load_projection_context(cur: Any, projection_id: UUID) -> dict[str, Any]:
    """The projection's identity and headline numbers, as the model must see them."""
    cur.execute(
        """SELECT f.projection_id,f.player_id,f.game_id,f.quote_id,f.model_run_id,
                  f.prop_type,f.line,f.mean,f.stddev,f.probability_over,f.probability_under,
                  f.projected_minutes,f.data_quality_score,f.confidence,f.generated_at,
                  f.expires_at,p.full_name,
                  d.side,d.predicted_probability,d.shrunk_probability,
                  d.breakeven_probability,d.system_recommendation,d.decision_reason,
                  q.source,q.over_american_odds,q.under_american_odds,
                  g.scheduled_tipoff,ht.abbreviation AS home_team,at.abbreviation AS away_team
           FROM wnba.stat_forecasts f
           JOIN wnba.players p ON p.player_id=f.player_id
           LEFT JOIN wnba.decision_episodes d ON d.model_run_id=f.model_run_id
             AND d.quote_id=f.quote_id
           LEFT JOIN wnba.prop_quotes q ON q.quote_id=f.quote_id
           JOIN wnba.games g ON g.game_id=f.game_id
           JOIN wnba.teams ht ON ht.team_id=g.home_team_id
           JOIN wnba.teams at ON at.team_id=g.away_team_id
           WHERE f.projection_id=%s""",
        (projection_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"Unknown projection {projection_id}")
    return dict(row)


# --------------------------------------------------------------------------------------
# Tools. Each database tool is one fixed, parameterized read scoped to this projection.
# --------------------------------------------------------------------------------------
def _tool_projection_detail(context: dict[str, Any]) -> dict[str, Any]:
    return {"projection": context}


def _tool_player_recent_games(context: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(arguments.get("limit", 10)), 1), 20)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT g.scheduled_tipoff,t.abbreviation AS team,l.started,
                      l.minutes,l.points,l.rebounds_offensive+l.rebounds_defensive AS rebounds,
                      l.assists,l.three_pointers_made
               FROM wnba.player_game_lines l
               JOIN wnba.games g ON g.game_id=l.game_id
               JOIN wnba.teams t ON t.team_id=l.team_id
               WHERE l.player_id=%s AND g.scheduled_tipoff<%s AND l.system_to IS NULL
               ORDER BY g.scheduled_tipoff DESC LIMIT %s""",
            (context["player_id"], context["scheduled_tipoff"], limit),
        )
        games = [dict(row) for row in cur.fetchall()]
    return {"player": context["full_name"], "games": games}


def _tool_quote_history(context: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(arguments.get("limit", 12)), 1), 50)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT source,line,over_american_odds,under_american_odds,system_from
               FROM wnba.prop_quotes
               WHERE player_id=%s AND prop_type=%s
               ORDER BY system_from DESC LIMIT %s""",
            (context["player_id"], context["prop_type"], limit),
        )
        quotes = [dict(row) for row in cur.fetchall()]
    return {
        "player": context["full_name"],
        "prop_type": context["prop_type"],
        "snapshots_newest_first": quotes,
    }


def _tool_research_findings(context: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT research_run_id,status,model,started_at,completed_at
               FROM wnba.research_runs WHERE projection_id=%s
               ORDER BY started_at DESC LIMIT 1""",
            (context["projection_id"],),
        )
        run = cur.fetchone()
        if run is None:
            return {"research": None, "note": "No research run exists for this projection yet."}
        cur.execute(
            """SELECT verdict,rationale,confidence FROM wnba.research_verdicts
               WHERE research_run_id=%s""",
            (run["research_run_id"],),
        )
        verdict = cur.fetchone()
        cur.execute(
            """SELECT c.predicate,c.value,c.confidence,c.status,a.agent_role
               FROM wnba.research_claims c
               JOIN wnba.agent_analyses a ON a.analysis_id=c.analysis_id
               WHERE a.research_run_id=%s ORDER BY c.confidence DESC LIMIT 20""",
            (run["research_run_id"],),
        )
        claims = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT status,support_level,notes FROM wnba.research_audits
               WHERE research_run_id=%s ORDER BY audited_at DESC LIMIT 1""",
            (run["research_run_id"],),
        )
        audit = cur.fetchone()
    return {
        "research": dict(run),
        "verdict": None if verdict is None else dict(verdict),
        "audit": None if audit is None else dict(audit),
        "claims": claims,
    }


def _tool_search_web(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    results = search_web(query, limit=5)
    return {"query": query, "results": [result.to_payload() for result in results]}


_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "projection_detail",
            "description": (
                "The forecast's full current numbers: probabilities, line, source, gate "
                "recommendation, and schedule. Already in your context; call again only if you "
                "need to re-read it."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "player_recent_games",
            "description": (
                "The player's most recent completed box-score lines (minutes, points, rebounds, "
                "assists, threes) before this game."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quote_history",
            "description": (
                "Archived market snapshots for this player and prop type, newest first: how the "
                "line and price have moved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_findings",
            "description": (
                "The research desk's run for this projection, if any: agent claims, the audit "
                "gate, and the verdict."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the public web for context the database does not hold: late injury "
                "news, lineup confirmations, beat reporting, roster moves. Returns titles, "
                "URLs, and snippets."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 3, "maxLength": 200}},
                "required": ["query"],
            },
        },
    },
]


def _run_tool(name: str, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if name == "projection_detail":
        return _tool_projection_detail(context)
    if name == "player_recent_games":
        return _tool_player_recent_games(context, arguments)
    if name == "quote_history":
        return _tool_quote_history(context, arguments)
    if name == "research_findings":
        return _tool_research_findings(context)
    if name == "search_web":
        return _tool_search_web(arguments)
    raise ValueError(f"Unknown tool {name!r}")


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------
def _get_or_create_dialogue(cur: Any, projection_id: UUID) -> UUID:
    cur.execute(
        """SELECT dialogue_id FROM wnba.projection_dialogues
           WHERE projection_id=%s AND analyst='owner'""",
        (projection_id,),
    )
    row = cur.fetchone()
    if row is not None:
        return UUID(str(row["dialogue_id"]))
    dialogue_id = uuid4()
    now = datetime.now(UTC)
    cur.execute(
        """INSERT INTO wnba.projection_dialogues
           (dialogue_id,projection_id,analyst,created_at,updated_at)
           VALUES (%s,%s,'owner',%s,%s)""",
        (dialogue_id, projection_id, now, now),
    )
    return dialogue_id


def _append_message(
    cur: Any, dialogue_id: UUID, *, role: str, content: str, payload: dict[str, Any]
) -> datetime:
    created_at = datetime.now(UTC)
    cur.execute(
        """INSERT INTO wnba.dialogue_messages
               (message_id,dialogue_id,role,content,payload,created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
        (uuid4(), dialogue_id, role, content, json.dumps(payload, default=_default), created_at),
    )
    cur.execute(
        "UPDATE wnba.projection_dialogues SET updated_at=%s WHERE dialogue_id=%s",
        (created_at, dialogue_id),
    )
    return created_at


def get_dialogue(projection_id: UUID) -> DialogueView:
    """The persisted transcript for the console, owner and assistant turns with tool notes."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT dialogue_id FROM wnba.projection_dialogues
               WHERE projection_id=%s AND analyst='owner'""",
            (projection_id,),
        )
        row = cur.fetchone()
        if row is None:
            return DialogueView(dialogue_id=UUID(int=0), projection_id=projection_id, messages=[])
        dialogue_id = UUID(str(row["dialogue_id"]))
        cur.execute(
            """SELECT role,content,payload,created_at FROM wnba.dialogue_messages
               WHERE dialogue_id=%s ORDER BY created_at""",
            (dialogue_id,),
        )
        messages: list[MessageView] = []
        for message in cast(list[dict[str, Any]], cur.fetchall()):
            payload = message["payload"] or {}
            messages.append(
                MessageView(
                    role=message["role"],
                    content=message["content"],
                    created_at=message["created_at"],
                    tool_name=payload.get("name"),
                )
            )
    return DialogueView(dialogue_id=dialogue_id, projection_id=projection_id, messages=messages)


def send_message(
    projection_id: UUID,
    content: str,
    *,
    client: DeepSeekResearchClient | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> DialogueView:
    """Run one owner turn end to end and return the full transcript afterwards.

    Raises ``RuntimeError`` when DeepSeek is not configured, and ``ValueError`` for an unknown
    projection or an over-long message. Tool failures are reported to the model as failed tool
    results so it can answer around them rather than the turn dying silently.
    """
    content = content.strip()
    if not content:
        raise ValueError("Message is empty")
    if len(content) > MAX_MESSAGE_CHARS:
        raise ValueError(f"Message exceeds {MAX_MESSAGE_CHARS} characters")

    client = client or DeepSeekResearchClient()
    if not client.api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    with connect() as conn, conn.cursor() as cur:
        context = _load_projection_context(cur, projection_id)
        dialogue_id = _get_or_create_dialogue(cur, projection_id)
        cur.execute(
            """SELECT payload FROM wnba.dialogue_messages
               WHERE dialogue_id=%s ORDER BY created_at DESC LIMIT %s""",
            (dialogue_id, MAX_REPLAY_MESSAGES),
        )
        history = [
            row["payload"] for row in cast(list[dict[str, Any]], cur.fetchall())
        ][::-1]

        _append_message(
            cur,
            dialogue_id,
            role="owner",
            content=content,
            payload={"role": "user", "content": content},
        )

    system = SYSTEM_PROMPT + json.dumps(context, default=_default, indent=2)
    provider_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        *[message for message in history if message],
        {"role": "user", "content": content},
    ]

    with connect() as conn, conn.cursor() as cur:
        for _round in range(max_tool_rounds + 1):
            envelope, _attempts = client._post(
                {
                    "model": client.model,
                    "messages": provider_messages,
                    "tools": _TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                    "max_tokens": REPLY_MAX_TOKENS,
                    "stream": False,
                }
            )
            choice = (envelope.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                reply = (message.get("content") or "").strip()
                if not reply:
                    raise RuntimeError("DeepSeek returned an empty reply")
                _append_message(
                    cur,
                    dialogue_id,
                    role="assistant",
                    content=reply,
                    payload={"role": "assistant", "content": reply},
                )
                break
            # Persist the model's tool-call envelope and each tool result exactly as exchanged,
            # so the transcript replays byte-for-byte on the next turn.
            _append_message(
                cur,
                dialogue_id,
                role="assistant",
                content=message.get("content") or "",
                payload={"role": "assistant", **message},
            )
            provider_messages.append({"role": "assistant", **message})
            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                name = str(function.get("name", ""))
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                try:
                    result = _run_tool(name, arguments, context)
                    result_text = json.dumps(result, default=_default)
                except Exception as exc:  # report to the model; the turn survives a bad tool
                    result_text = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:500]})
                result_text = result_text[:MAX_TOOL_RESULT_CHARS]
                _append_message(
                    cur,
                    dialogue_id,
                    role="tool",
                    content=result_text,
                    payload={
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "name": name,
                        "content": result_text,
                    },
                )
                provider_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "name": name,
                        "content": result_text,
                    }
                )
        else:
            raise RuntimeError(f"Dialogue exceeded {max_tool_rounds} tool rounds without a reply")

    return get_dialogue(projection_id)

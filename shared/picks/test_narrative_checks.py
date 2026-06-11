"""P0-2 / P0-3 unit tests: numeric scan and grounded-claim verification."""
from shared.picks.claims import extract_claims, verify_claims
from shared.picks.narrative import render_template
from shared.picks.numeric_scan import scan_narrative
from shared.picks.reason_codes import ReasonCode
from shared.picks.test_validation import make_payload, make_pick

# ── P0-2: fabricated numerics ────────────────────────────────────────────────

def test_payload_numbers_pass_the_scan():
    narrative = ("Buy Caitlin Clark over 19.5 points: projection 21.5 with a "
                 "65% hit probability; she has averaged 22.4 over her last 5.")
    assert scan_narrative(narrative, make_payload(), make_pick()) == []


def test_fabricated_edge_value_fails():
    """The 2026-06-11 slate bug: narrative said '+0.1 edge' while the payload
    edge was -0.6."""
    pick = make_pick(recommendation="Sell", projection=18.9,
                     hit_probability=0.62)
    payload = make_payload(projection={"mean": 18.9, "std": 5.5,
                                       "hit_probability": 0.62})
    violations = scan_narrative("Slight edge of +0.1 on this one.", payload, pick)
    assert [v["code"] for v in violations] == [ReasonCode.FABRICATED_NUMERIC.value]
    assert violations[0]["span"] == "+0.1"


def test_whole_number_rounding_is_tolerated():
    pick = make_pick(projection=21.5)
    narrative = "She should get to 22 points against this defense."
    assert scan_narrative(narrative, make_payload(), pick) == []


def test_probability_percentage_form_is_tolerated():
    narrative = "The model gives this 65% to hit, well above the 58.5% breakeven."
    assert scan_narrative(narrative, make_payload(), make_pick()) == []


def test_numbers_inside_tokens_are_not_flagged():
    narrative = "Her 3PM volume and L5 form both support the over at 19.5."
    assert scan_narrative(narrative, make_payload(), make_pick()) == []


# ── P0-3: ungrounded claims ──────────────────────────────────────────────────

def test_debut_with_career_games_fails():
    """The Clark 'debut' hallucination: third-year player, 70 career games."""
    narrative = "Caitlin Clark makes her WNBA debut tonight against the Chicago Sky."
    violations = verify_claims(narrative, make_payload())
    assert any(v["code"] == ReasonCode.UNGROUNDED_CLAIM.value
               and "debut" in v["span"] for v in violations)


def test_debut_with_zero_career_games_passes():
    payload = make_payload(player={"name": "Caitlin Clark", "team": "Indiana Fever",
                                   "position": "G", "career_games": 0, "seasons": 1})
    narrative = "Caitlin Clark makes her WNBA debut tonight."
    assert verify_claims(narrative, payload) == []


def test_rookie_claim_for_veteran_fails():
    violations = verify_claims("The rookie keeps scoring.", make_payload())
    assert any(v["code"] == ReasonCode.UNGROUNDED_CLAIM.value for v in violations)


def test_first_game_back_requires_returning_flag():
    narrative = "Caitlin Clark plays her first game back from injury."
    assert verify_claims(narrative, make_payload()) != []

    payload = make_payload(injuries=[{
        "player": "Caitlin Clark", "team": "Indiana Fever", "status": "ACTIVE",
        "last_updated": "2026-06-11T12:00:00+00:00", "returning": True}])
    assert verify_claims(narrative, payload) == []


def test_injury_mention_absent_from_injuries_fails():
    narrative = "Expect extra usage with Courtney Vandersloot out for the Sky."
    violations = verify_claims(narrative, make_payload(injuries=[]))
    assert any(v["code"] == ReasonCode.UNGROUNDED_CLAIM.value
               and "Vandersloot" in v["span"] for v in violations)


def test_injury_mention_present_in_injuries_passes():
    payload = make_payload(injuries=[{
        "player": "Courtney Vandersloot", "team": "Chicago Sky", "status": "OUT",
        "last_updated": "2026-06-11T12:00:00+00:00"}])
    narrative = "Expect extra usage with Courtney Vandersloot out for the Sky."
    assert verify_claims(narrative, payload) == []


def test_unknown_entity_name_fails():
    narrative = "Sabrina Ionescu was unstoppable last night."
    violations = verify_claims(narrative, make_payload())
    assert any(v["span"] == "Sabrina Ionescu" for v in violations)


def test_extractor_finds_all_claim_kinds():
    narrative = ("Caitlin Clark, the rookie, makes her debut and her first "
                 "game back at once.")
    kinds = {c.kind for c in extract_claims(narrative)}
    assert kinds == {"debut", "rookie", "first_game_back", "entity"}


# ── Template mode: numbers interpolated in code never trip the scan ──────────

def test_rendered_template_is_numerically_grounded():
    pick, payload = make_pick(), make_payload()
    narrative = render_template(pick, payload)
    assert scan_narrative(narrative, payload, pick) == []
    assert verify_claims(narrative, payload) == []

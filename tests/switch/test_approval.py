import pytest

from tripmate.switch.approval import parse_approval_decision


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("APPROVE itinerary", {"decision": "approve"}),
        ("@itinerary APPROVE itinerary", {"decision": "approve"}),
        ("  APPROVE itinerary  ", {"decision": "approve"}),
        ("approve ITINERARY", {"decision": "approve"}),
        (
            "REVISE: swap Day 3 to Nara",
            {"decision": "revise", "instruction": "swap Day 3 to Nara"},
        ),
        (
            "@itinerary REVISE: change hotel",
            {"decision": "revise", "instruction": "change hotel"},
        ),
        (
            "  revise:   Keep Day 2 in Kyoto  ",
            {"decision": "revise", "instruction": "Keep Day 2 in Kyoto"},
        ),
        ("REJECT", {"decision": "reject"}),
        ("@itinerary REJECT", {"decision": "reject"}),
        ("reject", {"decision": "reject"}),
    ],
)
def test_parses_exact_decisions(body, expected):
    assert parse_approval_decision(body) == expected


def test_parses_explicit_checkpoint_token():
    assert parse_approval_decision("APPROVE itinerary [thread:trip-42]") == {
        "decision": "approve", "thread_id": "trip-42"
    }
    assert parse_approval_decision("REVISE: add Nara [thread:trip-42]") == {
        "decision": "revise", "instruction": "add Nara", "thread_id": "trip-42"
    }


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   ",
        "maybe",
        "APPROVE",
        "REVISE:",
        "yes please",
        "I APPROVE this",
        "@itinerary REVISE:   ",
        "APPROVE itinerary and reject",
        "@other APPROVE itinerary",
        "@itinerary",
        "REVISE : change hotel",
        "APPROVE\nitinerary",
        "REJECT this",
        "APPROVE itinerary REVISE: change hotel",
    ],
)
def test_invalid_or_ambiguous_input_fails_closed(body):
    result = parse_approval_decision(body)

    assert result["decision"] == "hold"
    assert isinstance(result.get("reason"), str)
    assert result["reason"]


def test_non_text_input_fails_closed():
    result = parse_approval_decision(None)

    assert result["decision"] == "hold"
    assert result["reason"]

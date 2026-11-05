"""Fail-closed parsing for human approval decisions."""

import re


_PREFIX = r"(?:@itinerary[ \t]+)?"
_APPROVE = re.compile(_PREFIX + r"APPROVE[ \t]+itinerary", re.IGNORECASE)
_REVISE = re.compile(_PREFIX + r"REVISE:[ \t]*(.*)", re.IGNORECASE)
_REJECT = re.compile(_PREFIX + r"REJECT", re.IGNORECASE)


def parse_approval_decision(body: str) -> dict:
    """Parse one exact approval command, returning ``hold`` for anything unclear."""
    if not isinstance(body, str):
        return {"decision": "hold", "reason": "Message body must be text."}

    message = body.strip()
    if not message:
        return {"decision": "hold", "reason": "Message body is empty."}

    if _APPROVE.fullmatch(message):
        return {"decision": "approve"}

    revise = _REVISE.fullmatch(message)
    if revise:
        instruction = revise.group(1).strip()
        if instruction:
            return {"decision": "revise", "instruction": instruction}
        return {"decision": "hold", "reason": "Revision instruction is empty."}

    if _REJECT.fullmatch(message):
        return {"decision": "reject"}

    return {"decision": "hold", "reason": "Message does not match an exact approval command."}

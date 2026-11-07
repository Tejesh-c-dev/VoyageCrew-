"""Human approval interruption node."""

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from tripmate.graph.state import TravelState


def human_approval_agent(state: TravelState) -> dict[str, Any]:
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )
    if not isinstance(review, dict):
        review = {}
    rejected = review.get("rejected") is True
    approved = review.get("approved") is True and not rejected
    feedback = review.get("feedback")
    human_feedback = feedback.strip() if isinstance(feedback, str) else ""
    if not approved and not rejected and human_feedback:
        # Keep the trip context and make the revision request part of the next draft.
        query = str(state.get("user_query", ""))
        review_note = f"\n\nRevision requested by room owner: {human_feedback}"
        return {
            "approved": False,
            "rejected": False,
            "human_feedback": human_feedback,
            "user_query": query + review_note,
            "messages": [AIMessage(content="Revision requested; preparing a new draft.")],
        }
    return {
        "approved": approved,
        "rejected": rejected,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }

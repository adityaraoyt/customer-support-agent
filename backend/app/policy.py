from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .models import ReasoningStep


def evaluate_refund_policy(order: dict[str, Any], message: str, today: date) -> dict[str, Any]:
    reasoning: list[ReasoningStep] = []
    total = float(order["total"])
    days_since_delivery = (today - datetime.fromisoformat(order["delivered_at"]).date()).days
    message_lower = message.lower()

    if any(
        term in message_lower
        for term in ["ignore previous", "ignore all previous", "override", "break the rules", "manager told you"]
    ):
        reasoning.append(
            ReasoningStep(
                title="Prompt injection ignored",
                detail="The customer attempted to override policy instructions. Deterministic policy validation remained authoritative.",
                status="warning",
            )
        )

    if order["status"] != "delivered":
        decision = "denied"
        reason = "Only delivered orders are eligible for refund review."
    elif order.get("final_sale"):
        decision = "denied"
        reason = "Final sale items cannot be refunded."
    elif days_since_delivery > 30:
        decision = "denied"
        reason = f"The request is outside the 30-day refund window ({days_since_delivery} days since delivery)."
    elif order.get("opened") and not order.get("defective"):
        decision = "denied"
        reason = "Opened non-defective electronics, beauty, and personal-use items are not refundable."
    elif total > 500:
        decision = "escalated"
        reason = "Refunds over $500 require human review."
    elif order.get("chargeback_open"):
        decision = "escalated"
        reason = "Orders with an open chargeback must be escalated."
    else:
        decision = "approved"
        reason = "The order is delivered, within 30 days, not final sale, and below the escalation threshold."

    reasoning.append(
        ReasoningStep(
            title="Policy evaluation",
            detail=reason,
            status="ok" if decision == "approved" else "warning",
        )
    )
    return {"decision": decision, "reason": reason, "reasoning": reasoning}

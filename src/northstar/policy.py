"""Approved Northstar support policy and prompt preparation.

The live model (Groq, added later) only knows what we tell it. This system
prompt is the "approved policy context" the project asks for: it states the
policies covered by the shared cases and tells the model to decline anything
else. The dry provider ignores it, but the cache key includes its fingerprint
so that editing the policy never serves a stale answer.
"""

import hashlib

from src.northstar.models import ChatMessage

SYSTEM_PROMPT = """You are the Northstar customer-support assistant.
Answer in one or two short sentences, using ONLY these approved policies:

1. Duplicate-payment refunds: collect the receipt and the transaction
   identifier before review.
2. Locked accounts: verify the customer's identity, then issue the approved
   reset flow.
3. Requests to delete personal data: route the privacy request to human review.
4. Active incidents: share only confirmed updates from the status page.

If a question is not covered by these policies, reply exactly:
"I cannot confirm this from approved policy."
Never invent discounts, promises, or policies."""

POLICY_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


def build_messages(prompt: str) -> list[ChatMessage]:
    """Combine the fixed policy with the user's question."""
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]

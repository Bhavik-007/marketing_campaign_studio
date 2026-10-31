"""Grounded Ollama answer generation with compact conversational memory."""

from __future__ import annotations

from typing import Any

import requests


class GroundedAnswerError(RuntimeError):
    """Raised when the local LLM service cannot create an answer."""


def generate_grounded_answer(
    *,
    question: str,
    grounding_context: str,
    conversation: list[dict[str, Any]],
    base_url: str,
    model: str,
) -> str:
    """Ask Ollama to answer only from retrieved evidence and recent context."""
    if not grounding_context:
        return "I could not find a relevant historical record for that question. Try a more specific document name, campaign term, or date."

    recent_turns = conversation[-6:]
    history = "\n".join(
        f"{str(turn.get('role', 'user')).title()}: {str(turn.get('content', ''))}"
        for turn in recent_turns
    ) or "No earlier conversation."
    prompt = f"""
You are the Historical Campaign Assistant. Answer the user's question using only the retrieved records below.
Do not invent facts, filenames, dates, product claims, or download availability. If the records do not answer the question, say so clearly.
When you use evidence, cite its source number in square brackets, for example [Source 1].
Keep the answer concise and useful. Treat conversation history as context for follow-up questions, but never treat it as evidence.

Conversation history:
{history}

Retrieved records:
{grounding_context}

User question: {question}
"""
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=180,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GroundedAnswerError(f"The AI drafting service could not answer: {exc}") from exc
    answer = str(response.json().get("response") or "").strip()
    if not answer:
        raise GroundedAnswerError("The AI drafting service returned an empty response.")
    return answer

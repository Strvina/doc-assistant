"""Approximate API prices, for cost estimates in the UI, logs and evaluation."""

from __future__ import annotations

# USD per million tokens (input, output), Claude API list prices.
# Check the current price list on the Anthropic website before quoting these numbers.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated cost of one call, or None for unknown (or free/fake) models."""
    price = PRICES.get(model)
    if price is None:
        return None
    price_in, price_out = price
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000

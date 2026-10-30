"""Token usage and cost.

Prices are USD per million tokens (Anthropic first-party API, checked October 2026). Cache writes use the
5-minute TTL multiplier (1.25x input); cache reads are listed per model. Update the table when prices change;
unknown models fall back to the default model's price and are flagged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

PRICES: dict[str, dict[str, float]] = {
    #                   input   output  cache_read
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-opus-5":     {"input": 5.00, "output": 25.00, "cache_read": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-haiku-5-5":  {"input": 0.10, "output": 0.50, "cache_read": 0.01},
}
CACHE_WRITE_MULTIPLIER = 1.25
DEFAULT_PRICE_MODEL = "claude-opus-5-5"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.calls + other.calls,
        )

    @property
    def total_input(self) -> int:
        """Prompt size including cached parts (`input_tokens` alone is only the uncached remainder)."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_read_tokens / self.total_input if self.total_input else 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def price_for(model: str) -> tuple[dict[str, float], bool]:
    """Returns (price row, known). Strips provider prefixes such as 'anthropic.'."""
    key = model.split(".", 1)[-1] if model.startswith("anthropic.") else model
    if key in PRICES:
        return PRICES[key], True
    return PRICES[DEFAULT_PRICE_MODEL], False


def cost_usd(usage: Usage, model: str) -> float:
    p, _ = price_for(model)
    total = (
        usage.input_tokens * p["input"]
        + usage.cache_write_tokens * p["input"] * CACHE_WRITE_MULTIPLIER
        + usage.cache_read_tokens * p["cache_read"]
        + usage.output_tokens * p["output"]
    ) / 1_000_000
    return round(total, 6)


def cost_without_cache(usage: Usage, model: str) -> float:
    """What the same calls would have cost with no prompt caching (to show the saving)."""
    p, _ = price_for(model)
    return round((usage.total_input * p["input"] + usage.output_tokens * p["output"]) / 1_000_000, 6)


def estimate_tokens(text: str) -> int:
    """Rough estimate (~4 characters per token) used only for demo-mode numbers and chunk budgeting."""
    return max(1, len(text) // 4)

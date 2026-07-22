from __future__ import annotations

from dataclasses import dataclass


TOKENS_PER_MTOK = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelTokenPrice:
    model_name: str
    platform: str
    input_price: float
    output_price: float
    cache_write_price: float
    cache_read_price: float


@dataclass(frozen=True, slots=True)
class ModelCnyPerMTok:
    model_name: str
    input: float
    output: float
    cache_write: float
    cache_read: float


def token_price_to_cny_per_mtok(
    price: ModelTokenPrice,
    rate_multiplier: float,
) -> ModelCnyPerMTok:
    """Convert Sub2API per-token prices to CNY/MTok at its 1 CNY = 1 USD unit rate."""
    factor = TOKENS_PER_MTOK * rate_multiplier
    return ModelCnyPerMTok(
        model_name=price.model_name,
        input=price.input_price * factor,
        output=price.output_price * factor,
        cache_write=price.cache_write_price * factor,
        cache_read=price.cache_read_price * factor,
    )


def model_prices_for_group(
    prices: list[ModelTokenPrice] | tuple[ModelTokenPrice, ...],
    *,
    platform: str,
    group_name: str,
    group_key: str,
    limit: int = 3,
) -> tuple[ModelTokenPrice, ...]:
    candidates = [item for item in prices if item.platform.casefold() == platform.casefold()]
    if not candidates:
        return ()

    fingerprint = f"{group_name} {group_key}".casefold()
    tier_keywords = tuple(item for item in ("luna", "terra", "sol") if item in fingerprint)
    if tier_keywords:
        matched = [
            item
            for item in candidates
            if any(keyword in item.model_name.casefold() for keyword in tier_keywords)
        ]
        if matched:
            return tuple(sorted(matched, key=lambda item: item.model_name.casefold())[:limit])

    preferred_names = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
    by_name = {item.model_name.casefold(): item for item in candidates}
    preferred = [by_name[name] for name in preferred_names if name in by_name]
    if preferred:
        return tuple(preferred[:limit])
    return tuple(sorted(candidates, key=lambda item: item.model_name.casefold())[:limit])

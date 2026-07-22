import pytest

from backend.app.model_pricing import (
    ModelTokenPrice,
    model_prices_for_group,
    token_price_to_cny_per_mtok,
)


def _price(name: str) -> ModelTokenPrice:
    return ModelTokenPrice(
        model_name=name,
        platform="openai",
        input_price=0.0000025,
        output_price=0.000015,
        cache_write_price=0,
        cache_read_price=0.00000025,
    )


def test_per_token_price_converts_to_cny_per_mtok_with_group_multiplier():
    result = token_price_to_cny_per_mtok(_price("gpt-5.6-sol"), rate_multiplier=0.15)

    assert result.input == pytest.approx(0.375)
    assert result.output == pytest.approx(2.25)
    assert result.cache_write == 0
    assert result.cache_read == pytest.approx(0.0375)


def test_group_keyword_selects_matching_available_model():
    prices = [_price("gpt-5.6-luna"), _price("gpt-5.6-terra"), _price("gpt-5.6-sol")]

    selected = model_prices_for_group(
        prices,
        platform="openai",
        group_name="Sol 高速分组",
        group_key="2",
    )

    assert [item.model_name for item in selected] == ["gpt-5.6-sol"]


def test_generic_group_uses_available_gpt_56_tiers_in_stable_order():
    prices = [_price("gpt-5.6-sol"), _price("gpt-5.6-luna"), _price("gpt-5.6-terra")]

    selected = model_prices_for_group(
        prices,
        platform="openai",
        group_name="Plus",
        group_key="2",
    )

    assert [item.model_name for item in selected] == [
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    ]

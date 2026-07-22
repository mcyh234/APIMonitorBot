from datetime import timedelta
from io import BytesIO

from PIL import Image

from backend.app.model_pricing import ModelTokenPrice
from backend.app.sub2_price_image import Sub2PriceBoard, render_sub2_price_image
from backend.app.sub2_rates import Sub2RateHistoryPoint, Sub2StoredRate
from backend.app.sub2_sentiment import Sub2SentimentSummary, sentiment_date
from backend.app.time_utils import utc_now


def test_price_image_contains_line_candles_sentiment_and_model_prices():
    now = utc_now()
    history = (
        Sub2RateHistoryPoint(now - timedelta(days=2), 0.10),
        Sub2RateHistoryPoint(now - timedelta(days=1, hours=4), 0.14),
        Sub2RateHistoryPoint(now - timedelta(days=1), 0.12),
        Sub2RateHistoryPoint(now, 0.15),
    )
    rate = Sub2StoredRate(
        platform="openai",
        group_key="plus",
        group_name="Plus 测试分组",
        rate_multiplier=0.15,
        last_seen_at=now,
        history=history,
    )
    model_price = ModelTokenPrice(
        model_name="gpt-5.6-luna",
        platform="openai",
        input_price=0.0000025,
        output_price=0.000015,
        cache_write_price=0,
        cache_read_price=0.00000025,
    )
    sentiment = Sub2SentimentSummary(sentiment_date(now), up_count=3, down_count=2)

    image = Image.open(
        BytesIO(
            render_sub2_price_image(
                [Sub2PriceBoard("测试价格板", [rate], model_prices=(model_price,))],
                sentiment=sentiment,
            )
        )
    ).convert("RGB")

    assert image.width == 1240
    assert image.height >= 650
    colors = image.getcolors(maxcolors=image.width * image.height)
    assert colors is not None
    counts = {color: count for count, color in colors}
    assert counts.get((239, 68, 68), 0) > 20
    assert counts.get((34, 197, 94), 0) > 20


def test_price_image_zero_vote_sentiment_uses_neutral_bar():
    image = Image.open(
        BytesIO(
            render_sub2_price_image(
                [],
                sentiment=Sub2SentimentSummary(sentiment_date(), 0, 0),
            )
        )
    ).convert("RGB")

    colors = image.getcolors(maxcolors=image.width * image.height)
    assert colors is not None
    counts = {color: count for count, color in colors}
    assert counts.get((100, 116, 139), 0) >= 1000

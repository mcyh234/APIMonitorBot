"""Read New API model plaza prices without inventing unavailable billing rates."""
from __future__ import annotations

import ast
import math
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.app.model_pricing import ModelTokenPrice
from backend.app.sub2api import Sub2ApiError, Sub2AvailableCatalog, Sub2ChannelRateSnapshot


def normalize_newapi_url(base_url: str, path: str) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise Sub2ApiError("New API BaseURL 必须是有效的 HTTP/HTTPS 地址。")
    prefix = parts.path.rstrip("/")
    for suffix in ("/api/pricing", "/pricing"):
        if prefix.endswith(suffix):
            prefix = prefix[:-len(suffix)]
            break
    return urlunsplit(parts._replace(path=prefix + "/" + path.lstrip("/"), query="", fragment=""))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def display_currency(status: dict) -> tuple[str, float]:
    """Use the site's billing display currency; fall back to explicit USD prices."""
    kind = status.get("quota_display_type")
    if kind == "CNY" or (kind is None and status.get("display_in_currency") is True):
        rate = _number(status.get("usd_exchange_rate"))
        if rate is not None and rate > 0:
            return "CNY", rate
    if kind == "CUSTOM":
        rate = _number(status.get("custom_currency_exchange_rate"))
        symbol = status.get("custom_currency_symbol")
        if rate is not None and rate > 0 and isinstance(symbol, str) and symbol.strip():
            return symbol.strip()[:12], rate
    return "USD", 1.0


def _linear_tier(expression: Any) -> dict[str, float] | None:
    """Accept only a complete, single linear token tier, never execute expressions.

    Conditional, plugin, image/audio and nonlinear billing remains explicitly
    dynamic. Missing cache terms remain unknown rather than being shown as free.
    """
    if not isinstance(expression, str) or len(expression) > 4096:
        return None
    try:
        source = expression.strip().removeprefix("v1:").strip()
        tree = ast.parse(source, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 160:
            return None
        call = tree.body
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "tier" and len(call.args) == 2 and not call.keywords
                and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
            return None
        result = {}

        def collect(node):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                collect(node.left)
                collect(node.right)
                return
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)):
                raise ValueError
            name, value = node.left, node.right
            if isinstance(value, ast.Name):
                name, value = value, name
            if not (isinstance(name, ast.Name) and name.id in {"p", "c", "cr", "cc", "cc1h"}
                    and name.id not in result and isinstance(value, ast.Constant)):
                raise ValueError
            number = _number(value.value)
            if number is None:
                raise ValueError
            result[name.id] = number

        collect(call.args[1])
        return result if "p" in result and "c" in result else None
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return None


def flatten_newapi_catalog(data: dict, status: dict | None = None) -> Sub2AvailableCatalog:
    rows, ratios = data.get("data"), data.get("group_ratio")
    if not isinstance(rows, list) or not isinstance(ratios, dict):
        raise Sub2ApiError("New API 模型广场响应缺少模型列表或分组倍率。")
    usable = data.get("usable_group")
    rates = []
    for key, raw_ratio in ratios.items():
        ratio = _number(raw_ratio)
        if not isinstance(key, str) or ratio is None or (isinstance(usable, dict) and key not in usable):
            continue
        # usable_group values are descriptions, not names; retain stable group keys.
        rates.append(Sub2ChannelRateSnapshot("newapi", key, key, ratio))
    currency, exchange = display_currency(status or {})
    prices = {}
    allowed_groups = {rate.group_key for rate in rates}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("model_name"), str) or not row["model_name"].strip():
            continue
        groups = row.get("enable_groups")
        if not isinstance(groups, list) or not all(isinstance(key, str) for key in groups):
            continue
        groups = tuple(sorted(allowed_groups if "all" in groups else allowed_groups.intersection(groups)))
        if not groups:
            continue
        mode = row.get("billing_mode") or "token"
        price = dict(input_price=0.0, output_price=0.0, cache_write_price=None, cache_read_price=None)
        extra = {}
        if mode == "tiered_expr":
            tier = _linear_tier(row.get("billing_expr"))
            if tier is None:
                mode = "dynamic"
            else:
                mode = "token"
                price = dict(input_price=tier["p"] * exchange / 1_000_000,
                             output_price=tier["c"] * exchange / 1_000_000,
                             cache_write_price=tier["cc"] * exchange / 1_000_000 if "cc" in tier else None,
                             cache_read_price=tier["cr"] * exchange / 1_000_000 if "cr" in tier else None)
                extra["cache_write_1h_price"] = tier["cc1h"] * exchange / 1_000_000 if "cc1h" in tier else None
        elif mode not in {"token", "ratio"}:
            mode = "dynamic"
        elif row.get("quota_type") == 1:
            request_price = _number(row.get("model_price"))
            mode = "request" if request_price is not None else "dynamic"
            extra["request_price"] = request_price * exchange if request_price is not None else None
        elif row.get("quota_type") == 0:
            ratio, completion = _number(row.get("model_ratio")), _number(row.get("completion_ratio"))
            if ratio is None or completion is None:
                mode = "dynamic"
            else:
                mode = "token"
                base = ratio * 2 * exchange / 1_000_000
                cache, write = _number(row.get("cache_ratio")), _number(row.get("create_cache_ratio"))
                price = dict(input_price=base, output_price=base * completion,
                             cache_write_price=base * write if write is not None else None,
                             cache_read_price=base * cache if cache is not None else None)
        else:
            mode = "dynamic"
        if not all(value is None or math.isfinite(value) for value in [*price.values(), *extra.values()]):
            mode, price, extra = "dynamic", dict(input_price=0.0, output_price=0.0, cache_write_price=None, cache_read_price=None), {}
        model = ModelTokenPrice(row["model_name"].strip(), "newapi", **price, group_keys=groups,
                                currency=currency, billing_mode=mode, **extra,
                                price_note="动态计费，详见模型广场" if mode == "dynamic" else "")
        prices[model.model_name.casefold()] = model
    return Sub2AvailableCatalog(tuple(sorted(rates, key=lambda r: r.group_key)),
                                tuple(sorted(prices.values(), key=lambda p: p.model_name.casefold())))

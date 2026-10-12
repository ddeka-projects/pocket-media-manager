"""One recommendation engine, using preferences and distinct encounter outcomes."""

import random
from datetime import datetime, timezone
from typing import Any


TOO_SOON_HOURS = 24


def elapsed_hours(value: str | None, now: datetime) -> float:
    if not value:
        return float("inf")
    return max(0.0, (now - datetime.fromisoformat(value)).total_seconds() / 3600)


def compute_weight(media: dict[str, Any], now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    weight = 4.0 if media["favorite"] else 1.0
    weight *= 1.0 + 0.25 * min(media.get("enjoyed_count", 0), 5)
    if media["less_often"]:
        weight *= 0.25
    # A reported enjoyable viewing needs breathing room; a launch is not a view.
    hours = elapsed_hours(media.get("last_enjoyed_at"), now)
    weight *= min(1.0, max(0.08, hours / 24))
    # Unrated offers receive only a brief repeat penalty, never a viewing count.
    hours = elapsed_hours(media.get("last_offered_at"), now)
    weight *= min(1.0, max(0.1, hours / (5 / 60)))
    return max(weight, 0.01)


def pick_weighted(
    media: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    eligible = [
        item for item in media
        if not item["not_for_me"]
        and not item.get("declined_this_session")
        and elapsed_hours(item.get("last_too_soon_at"), now) >= TOO_SOON_HOURS
    ]
    if not eligible:
        raise ValueError("No clips fit right now. Try Explore or start a new session.")
    return random.choices(
        eligible, weights=[compute_weight(item, now) for item in eligible], k=1
    )[0]

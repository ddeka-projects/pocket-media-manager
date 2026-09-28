from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RecommendationProfile:
    """Tuning constants for a recommendation mood."""

    name: str
    label: str
    unseen_bonus: float
    like_bonus: float
    pending_bonus: float
    dislike_penalty: float
    min_weight_floor: float
    recency_tie_breaker_min_multiplier: float
    play_count_bonus: float = 0.0
    pending_priority_bonus: float = 0.0


BALANCED = RecommendationProfile(
    name="balanced",
    label="\u2696\ufe0f Balanced",
    unseen_bonus=2.5,
    like_bonus=1.25,
    pending_bonus=0.35,
    dislike_penalty=1.75,
    min_weight_floor=0.12,
    recency_tie_breaker_min_multiplier=0.9,
)

SURPRISE = RecommendationProfile(
    name="surprise",
    label="\U0001f3b2 Surprise Me",
    unseen_bonus=12.0,
    like_bonus=0.6,
    pending_bonus=0.2,
    dislike_penalty=1.75,
    min_weight_floor=0.12,
    recency_tie_breaker_min_multiplier=0.75,
)

COMFORT = RecommendationProfile(
    name="comfort",
    label="\U0001f6cb\ufe0f Comfort Zone",
    unseen_bonus=0.0,
    like_bonus=2.5,
    pending_bonus=0.15,
    dislike_penalty=2.5,
    min_weight_floor=0.12,
    recency_tie_breaker_min_multiplier=0.9,
    play_count_bonus=0.3,
)

PENDING_REVIEW = RecommendationProfile(
    name="pending",
    label="\u23f3 Pending Review",
    unseen_bonus=0.0,
    like_bonus=0.5,
    pending_bonus=0.35,
    dislike_penalty=1.0,
    min_weight_floor=0.12,
    recency_tie_breaker_min_multiplier=0.9,
    pending_priority_bonus=10.0,
)

PROFILES: dict[str, RecommendationProfile] = {
    p.name: p for p in (BALANCED, SURPRISE, COMFORT, PENDING_REVIEW)
}
DEFAULT_PROFILE = BALANCED

DEFAULT_META = {
    "likes": 0,
    "dislikes": 0,
    "pending": 0,
    "play_count": 0,
    "last_played": None,
    "last_feedback": None,
}

FEEDBACK_ALIASES = {
    "like": "y",
    "dislike": "n",
    "pending": "p",
    "y": "y",
    "n": "n",
    "p": "p",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def empty_prefs() -> dict[str, Any]:
    return {"files": {}}


def load_prefs(prefs_file: Path) -> dict[str, Any]:
    if not prefs_file.exists():
        prefs = empty_prefs()
        save_prefs(prefs, prefs_file)
        return prefs

    try:
        prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
    except Exception:
        return empty_prefs()

    if not isinstance(prefs, dict):
        return empty_prefs()
    files = prefs.get("files")
    if not isinstance(files, dict):
        prefs["files"] = {}
    return prefs


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp_file:
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def save_prefs(prefs: dict[str, Any], prefs_file: Path) -> None:
    write_text_atomic(prefs_file, json.dumps(prefs, indent=2))


def reset_prefs(prefs_file: Path) -> None:
    save_prefs(empty_prefs(), prefs_file)


def find_media_files(
    media_root: Path,
    supported_extensions: set[str],
) -> list[Path]:
    if not media_root.exists() or not media_root.is_dir():
        return []

    files: list[Path] = []
    for path in media_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in supported_extensions:
            continue
        files.append(path)
    files.sort(key=lambda item: str(item).lower())
    return files


def list_top_level_media_folders(
    media_root: Path,
    supported_extensions: set[str],
) -> list[Path]:
    if not media_root.exists() or not media_root.is_dir():
        return []

    folders: list[Path] = []
    for path in media_root.iterdir():
        if not path.is_dir():
            continue
        if find_media_files(path, supported_extensions):
            folders.append(path)
    folders.sort(key=lambda item: item.name.lower())
    return folders


def find_media_files_in_folders(
    folders: list[Path],
    supported_extensions: set[str],
) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files.extend(find_media_files(folder, supported_extensions))
    files.sort(key=lambda item: str(item).lower())
    return files


def ensure_entries(prefs: dict[str, Any], files: list[Path]) -> dict[str, Any]:
    prefs.setdefault("files", {})
    for file_path in files:
        key = str(file_path)
        existing = prefs["files"].setdefault(key, {})
        for field_name, default_value in DEFAULT_META.items():
            existing.setdefault(field_name, default_value)
    return prefs


def last_played_sort_value(meta: dict[str, Any]) -> datetime:
    last_played = meta.get("last_played")
    if not last_played:
        return datetime.min
    try:
        return datetime.fromisoformat(last_played)
    except (TypeError, ValueError):
        return datetime.min


def date_added_sort_value(file_path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(file_path.stat().st_ctime)
    except OSError:
        return datetime.max


def compute_weight(
    meta: dict[str, Any],
    profile: RecommendationProfile = DEFAULT_PROFILE,
) -> float:
    likes = meta.get("likes", 0)
    dislikes = meta.get("dislikes", 0)
    pending = meta.get("pending", 0)
    play_count = meta.get("play_count", 0)

    weight = 1.0
    if play_count == 0:
        weight += profile.unseen_bonus
    else:
        weight += profile.play_count_bonus * play_count

    preference = (
        1.0
        + (profile.like_bonus * likes)
        + (profile.pending_bonus * pending)
        - (profile.dislike_penalty * dislikes)
    )

    if pending > 0:
        preference += profile.pending_priority_bonus

    preference = max(preference, profile.min_weight_floor)

    weight *= preference
    return max(weight, profile.min_weight_floor)


def apply_recency_tie_breakers(
    files: list[Path],
    prefs: dict[str, Any],
    weights: list[float],
    profile: RecommendationProfile = DEFAULT_PROFILE,
) -> list[float]:
    groups: dict[float, list[int]] = {}
    for index, weight in enumerate(weights):
        groups.setdefault(weight, []).append(index)

    adjusted = list(weights)
    for indices in groups.values():
        if len(indices) <= 1:
            continue

        recencies = {
            last_played_sort_value(prefs["files"].get(str(files[index]), {}))
            for index in indices
        }
        if len(recencies) <= 1:
            continue

        sorted_recencies = sorted(recencies)
        recency_rank = {
            recency: rank
            for rank, recency in enumerate(sorted_recencies)
        }
        max_rank = len(sorted_recencies) - 1
        for index in indices:
            meta = prefs["files"].get(str(files[index]), {})
            rank = recency_rank[last_played_sort_value(meta)]
            multiplier = 1.0 - (
                (1.0 - profile.recency_tie_breaker_min_multiplier)
                * (rank / max_rank)
            )
            adjusted[index] = max(
                weights[index] * multiplier, profile.min_weight_floor
            )

    return adjusted


def pick_weighted(
    files: list[Path],
    prefs: dict[str, Any],
    profile: RecommendationProfile = DEFAULT_PROFILE,
) -> Path:
    if not files:
        raise ValueError("No media files available")

    weights = []
    for file_path in files:
        meta = prefs["files"].get(str(file_path), {})
        weights.append(compute_weight(meta, profile))
    adjusted_weights = apply_recency_tie_breakers(files, prefs, weights, profile)
    return random.choices(files, weights=adjusted_weights, k=1)[0]


def record_play(prefs: dict[str, Any], file_path: Path) -> None:
    key = str(file_path)
    meta = prefs["files"][key]
    meta["play_count"] = meta.get("play_count", 0) + 1
    meta["last_played"] = now_iso()


def apply_feedback(prefs: dict[str, Any], file_path: Path, feedback: str) -> str:
    normalized = FEEDBACK_ALIASES.get(feedback)
    if normalized is None:
        raise ValueError(f"Unsupported feedback: {feedback}")

    key = str(file_path)
    meta = prefs["files"][key]
    if normalized == "y":
        meta["likes"] = meta.get("likes", 0) + 1
    elif normalized == "n":
        meta["dislikes"] = meta.get("dislikes", 0) + 1
    elif normalized == "p":
        meta["pending"] = meta.get("pending", 0) + 1
    meta["last_feedback"] = normalized
    return normalized

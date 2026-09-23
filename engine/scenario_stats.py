"""Сравнение с офлайн-перебором и предупреждения о новых критических значениях."""

from bisect import bisect_left
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

STATS_PATH = Path(__file__).resolve().parents[1] / "data" / "scenario_stats.json"


def dataset_fingerprint(city):
    payload = json.dumps(city, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


@lru_cache(maxsize=2)
def _read_stats(path, modified_ns):
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def load_stats(city):
    """Отсутствующие/устаревшие данные не блокируют обычную симуляцию."""
    try:
        stats = _read_stats(STATS_PATH, STATS_PATH.stat().st_mtime_ns)
    except (OSError, ValueError):
        return None
    if stats.get("dataset_sha256") != dataset_fingerprint(city):
        return None
    return stats


def estimate_percentile(score, stats):
    """Доля строго худших сценариев, приближённая по квантилям с шагом 0.1%."""
    quantiles = stats["quantiles"]
    values = [item["score"] for item in quantiles]
    index = bisect_left(values, score)
    count = stats["valid_scenario_count"]
    if index == len(values):
        return 100.0
    if values[index] == score:
        return 100 * quantiles[index]["count_less"] / count
    if index == 0:
        return 0.0
    left, right = quantiles[index - 1], quantiles[index]
    fraction = (score - left["score"]) / (right["score"] - left["score"])
    rank = left["count_less_or_equal"] + fraction * (right["count_less"] - left["count_less_or_equal"])
    return 100 * rank / count


def scenario_comparison(score, city):
    stats = load_stats(city)
    if stats is None:
        return {"best_possible_score": None, "gap_to_best": None, "percentile": None}
    best = stats["best_scenario"]["Score"]
    return {
        "best_possible_score": best,
        "gap_to_best": best - score,
        "percentile": estimate_percentile(score, stats),
    }


def critical_warnings(decisions, baseline, result, city):
    """Указать меры с отрицательным эффектом, участвующие в новом провале."""
    threshold = city["scoring"]["critical_threshold"]
    measures = {item["id"]: item for item in city["measures"]}
    warnings = []
    for decision in decisions:
        measure = measures[decision["measure_id"]]
        targets = city["districts"] if measure["type"] == "city" else [decision["district"]]
        for district in targets:
            for indicator, effect in measure["effects"].items():
                before = baseline["indicators"][district][indicator]
                after = result["indicators"][district][indicator]
                if effect < 0 and before >= threshold and after < threshold:
                    warnings.append({
                        "measure_id": measure["id"], "district": district,
                        "indicator": indicator, "before": before, "after": after,
                    })
    return sorted(warnings, key=lambda item: (item["measure_id"], item["district"], item["indicator"]))

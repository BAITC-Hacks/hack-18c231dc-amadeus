"""Оптимум полным перебором валидных наборов и одиночные замены.

Все данные и правила берутся из load_city(). Перебор ленивый: при импорте
ничего не считается, первый вызов cached_top() запускает его один раз.
"""

from copy import deepcopy
from functools import lru_cache
from itertools import combinations, product

import numpy as np

from .data import load_city
from .simulator import simulate
from .validator import validate


def _prepare(city: dict) -> dict:
    """Массивы для векторного расчёта в тех же единицах, что simulate()."""
    districts = list(city["districts"])
    indicators = list(city["weights"])
    column = {code: index for index, code in enumerate(indicators)}
    realized = {}
    for measure in city["measures"]:
        fraction = (city["horizon"] - measure["lag"]) / city["horizon"]
        vector = np.zeros(len(indicators))
        for code, effect in measure["effects"].items():
            vector[column[code]] = effect * fraction
        realized[measure["id"]] = vector
    return {
        "districts": districts,
        "column": column,
        "base": np.array([
            [city["districts"][name]["indicators"][code] for code in indicators]
            for name in districts
        ], dtype=float),
        "weights": np.array([city["weights"][code] for code in indicators]),
        "population": np.array([city["districts"][name]["population_share"] for name in districts]),
        "realized": realized,
    }


def _is_valid_combo(combo: tuple[dict, ...], city: dict) -> bool:
    """Правила, не зависящие от районов: бюджет, направления, глобальные несовместимости."""
    rules = city["rules"]
    if sum(measure["cost"] for measure in combo) > city["budget"]:
        return False
    directions = [measure["direction"] for measure in combo]
    if any(directions.count(item) > rules["max_per_direction"] for item in directions):
        return False
    ids = {measure["id"] for measure in combo}
    return not any(
        conflict["scope"] == "global" and set(conflict["measures"]) <= ids
        for conflict in city["incompatibilities"]
    )


def _decisions(combo: tuple[dict, ...], assignment, district_ids: list[str], districts: list[str]) -> list[dict]:
    result = []
    for measure in combo:
        if measure["type"] == "district":
            district = districts[int(assignment[district_ids.index(measure["id"])])]
            result.append({"measure_id": measure["id"], "district": district})
        else:
            result.append({"measure_id": measure["id"]})
    return result


def _score_combo(combo: tuple[dict, ...], city: dict, model: dict) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Все допустимые назначения районов для набора мер и их Score.

    Строка assignments — индексы районов для районных мер в порядке district_ids.
    """
    districts = model["districts"]
    scoring = city["scoring"]
    ids = {measure["id"] for measure in combo}
    district_ids = [measure["id"] for measure in combo if measure["type"] == "district"]
    assignments = np.array(
        list(product(range(len(districts)), repeat=len(district_ids))), dtype=int,
    ).reshape(-1, len(district_ids))
    mask = np.ones(len(assignments), dtype=bool)
    for conflict in city["incompatibilities"]:
        first, second = conflict["measures"]
        if conflict["scope"] == "same_district" and first in ids and second in ids:
            mask &= (assignments[:, district_ids.index(first)]
                     != assignments[:, district_ids.index(second)])
    assignments = assignments[mask]
    count = len(assignments)

    rows = np.arange(count)
    values = np.broadcast_to(model["base"], (count, *model["base"].shape)).copy()
    # Порядок сложения как в simulate(): по measure_id, затем синергии.
    for measure in sorted(combo, key=lambda item: item["id"]):
        effect = model["realized"][measure["id"]]
        if measure["type"] == "city":
            values += effect
        else:
            values[rows, assignments[:, district_ids.index(measure["id"])]] += effect
    for synergy in city["synergies"]:
        if not set(synergy["measures"]) <= ids:
            continue
        target = assignments[:, district_ids.index(synergy["target_measure"])]
        for code, bonus in synergy["effects"].items():
            values[rows, target, model["column"][code]] += bonus
    np.clip(values, scoring["minimum"], scoring["maximum"], out=values)

    district_scores = values @ model["weights"]
    critical = (values < scoring["critical_threshold"]).sum(axis=(1, 2))
    scores = (
        scoring["average_weight"] * (district_scores @ model["population"])
        + scoring["minimum_weight"] * district_scores.min(axis=1)
        - scoring["critical_penalty"] * critical
    )
    return district_ids, assignments, scores


def optimize(top_k: int = 10) -> dict:
    """Перебрать все валидные наборы; вернуть их число и top_k лучших по Score.

    При равном Score выше набор дешевле. Score не округляется, как в simulate().
    """
    city = load_city()
    if city["rules"]["allow_repeats"]:
        raise ValueError("Перебор рассчитан на наборы без повторов мер.")
    model = _prepare(city)
    candidates = []
    n_valid = 0

    for combo in combinations(city["measures"], city["rules"]["decision_count"]):
        if not _is_valid_combo(combo, city):
            continue
        district_ids, assignments, scores = _score_combo(combo, city, model)
        n_valid += len(assignments)
        cost = sum(measure["cost"] for measure in combo)
        for index in np.argsort(-scores, kind="stable")[:top_k]:
            candidates.append({
                "decisions": _decisions(combo, assignments[index], district_ids, model["districts"]),
                "Score": float(scores[index]),
                "cost": cost,
            })

    def order(item):
        return -item["Score"], item["cost"], repr(item["decisions"])

    candidates.sort(key=order)
    top = candidates[:top_k]
    # numpy суммирует в другом порядке, чем fsum в simulate(): итоговый Score берём из simulate().
    for item in top:
        item["Score"] = simulate(item["decisions"])["Score"]
    top.sort(key=order)
    return {"n_valid": n_valid, "top": top}


@lru_cache(maxsize=1)
def _cached_optimum() -> dict:
    return optimize(top_k=10)


def cached_top() -> dict:
    """Топ-10 перебора; считается один раз при первом вызове. Возвращает копию."""
    return deepcopy(_cached_optimum())


def suggest_swaps(decisions: list[dict], top_k: int = 3) -> list[dict]:
    """Лучшие одиночные замены, повышающие Score; каждая проверена validate() и simulate().

    Для невалидного набора возвращается пустой список.
    """
    if validate(decisions):
        return []
    city = load_city()
    base = simulate(decisions)["Score"]
    used = {decision["measure_id"] for decision in decisions}
    swaps = []
    for position, old in enumerate(decisions):
        for measure in city["measures"]:
            if measure["id"] in used and measure["id"] != old["measure_id"]:
                continue
            options = (
                [{"measure_id": measure["id"], "district": name} for name in city["districts"]]
                if measure["type"] == "district"
                else [{"measure_id": measure["id"]}]
            )
            for new in options:
                if new == old:
                    continue
                candidate = decisions[:position] + [new] + decisions[position + 1:]
                if validate(candidate):
                    continue
                result = simulate(candidate)
                if result["Score"] > base:
                    swaps.append({
                        "replace": dict(old),
                        "with": new,
                        "decisions": candidate,
                        "Score": result["Score"],
                        "gain": result["Score"] - base,
                        "cost": result["cost"],
                    })
    swaps.sort(key=lambda item: (-item["gain"], item["cost"], repr(item["with"]), repr(item["replace"])))
    return swaps[:top_k]

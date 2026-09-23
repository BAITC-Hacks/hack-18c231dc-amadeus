"""Детерминированная симуляция без вызова валидатора и округления."""

from math import fsum

from .data import load_city


def simulate(decisions: list[dict]) -> dict:
    """Рассчитать сценарий, включая пустой базовый набор.

    Проверку правил вызывающая сторона выполняет отдельно через validate().
    Неизвестные меры/районы и отсутствующий район районной меры вызывают KeyError.
    """
    city = load_city()
    measures = {measure["id"]: measure for measure in city["measures"]}
    indicators = {
        name: district["indicators"].copy()
        for name, district in city["districts"].items()
    }
    selected = {}
    cost = 0

    # Фиксированный порядок суммирования исключает зависимость float от порядка ввода.
    for decision in sorted(decisions, key=lambda item: item["measure_id"]):
        measure = measures[decision["measure_id"]]
        selected[measure["id"]] = decision
        cost += measure["cost"]
        fraction = (city["horizon"] - measure["lag"]) / city["horizon"]
        targets = (
            indicators.keys()
            if measure["type"] == "city"
            else [decision["district"]]
        )
        for district in targets:
            for indicator, effect in measure["effects"].items():
                indicators[district][indicator] += effect * fraction

    for synergy in city["synergies"]:
        if all(measure_id in selected for measure_id in synergy["measures"]):
            district = selected[synergy["target_measure"]]["district"]
            for indicator, bonus in synergy["effects"].items():
                indicators[district][indicator] += bonus

    scoring = city["scoring"]
    for values in indicators.values():
        for indicator, value in values.items():
            values[indicator] = min(scoring["maximum"], max(scoring["minimum"], value))

    district_scores = {
        name: fsum(city["weights"][indicator] * value for indicator, value in values.items())
        for name, values in indicators.items()
    }
    average = fsum(
        city["districts"][name]["population_share"] * score
        for name, score in district_scores.items()
    )
    minimum = min(district_scores.values())
    critical = sum(
        value < scoring["critical_threshold"]
        for values in indicators.values()
        for value in values.values()
    )
    score = (
        scoring["average_weight"] * average
        + scoring["minimum_weight"] * minimum
        - scoring["critical_penalty"] * critical
    )
    return {
        "indicators": indicators,
        "D": district_scores,
        "D_avg": average,
        "D_min": minimum,
        "N_crit": critical,
        "Score": score,
        "cost": cost,
    }

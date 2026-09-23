"""Проверка решений по правилам из data/city.json."""

from collections import Counter

from .data import load_city


def validate(decisions: list[dict]) -> list[str]:
    """Вернуть причины невалидности на русском; пустой список означает успех."""
    city = load_city()
    rules = city["rules"]
    measures = {measure["id"]: measure for measure in city["measures"]}
    errors = []

    if not isinstance(decisions, list):
        return ["Решения должны быть переданы списком."]
    if len(decisions) != rules["decision_count"]:
        errors.append(
            f"Нужно выбрать ровно {rules['decision_count']} мер; выбрано {len(decisions)}."
        )

    selected = {}
    directions = Counter()
    cost = 0
    for index, decision in enumerate(decisions, start=1):
        if not isinstance(decision, dict):
            errors.append(f"Решение №{index} должно быть объектом с measure_id.")
            continue
        measure_id = decision.get("measure_id")
        if not isinstance(measure_id, str) or measure_id not in measures:
            errors.append(f"Решение №{index}: неизвестная мера {measure_id!r}.")
            continue

        measure = measures[measure_id]
        if measure_id in selected:
            errors.append(f"Мера {measure_id} выбрана повторно; повторы запрещены.")
        selected.setdefault(measure_id, []).append(decision)
        directions[measure["direction"]] += 1
        cost += measure["cost"]

        if measure["type"] == "district":
            district = decision.get("district")
            if district is None or district == "":
                errors.append(f"Для районной меры {measure_id} обязательно указать район.")
            elif not isinstance(district, str) or district not in city["districts"]:
                errors.append(f"Для меры {measure_id} указан неизвестный район: {district!r}.")
        elif "district" in decision:
            errors.append(f"Для городской меры {measure_id} нельзя указывать район.")

    if cost > city["budget"]:
        errors.append(f"Стоимость {cost} превышает бюджет {city['budget']}.")
    for direction, count in directions.items():
        if count > rules["max_per_direction"]:
            errors.append(
                f"В направлении «{direction}» выбрано {count} мер; "
                f"допустимо не больше {rules['max_per_direction']}."
            )

    for conflict in city["incompatibilities"]:
        first, second = conflict["measures"]
        if first not in selected or second not in selected:
            continue
        if conflict["scope"] == "global":
            errors.append(
                f"Меры {first} и {second} несовместимы в любых районах: {conflict['reason']}."
            )
        else:
            for district in city["districts"]:
                if all(
                    any(item.get("district") == district for item in selected[measure_id])
                    for measure_id in (first, second)
                ):
                    errors.append(
                        f"Меры {first} и {second} несовместимы в районе «{district}»: "
                        f"{conflict['reason']}."
                    )
    return errors

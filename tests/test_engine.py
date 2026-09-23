"""Контрольные сценарии и пограничные случаи расчётного движка."""

from copy import deepcopy
import json

import pytest

from engine import data as data_module
from engine.data import load_city
from engine.simulator import simulate
from engine.validator import validate


@pytest.fixture
def example():
    return [
        {"measure_id": "M7", "district": "Нура"},
        {"measure_id": "M8", "district": "Нура"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12"},
        {"measure_id": "M5", "district": "Сарыарка"},
    ]


@pytest.fixture
def cheapest():
    return [
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M11", "district": "Нура"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12"},
        {"measure_id": "M4", "district": "Сарыарка"},
    ]


def test_baseline():
    result = simulate([])
    assert result["Score"] == pytest.approx(52.56, abs=0.01, rel=0)
    assert result["N_crit"] == 2
    assert result["cost"] == 0
    assert result["D"] == pytest.approx(
        {"Есиль": 62.99, "Алматы": 57.06, "Сарыарка": 54.65, "Байконур": 56.63, "Нура": 49.18},
        abs=0.01, rel=0,
    )
    assert result["D_avg"] == pytest.approx(56.8624, abs=0.01, rel=0)
    assert result["D_min"] == pytest.approx(49.18, abs=0.01, rel=0)
    values = [value for district in result["indicators"].values() for value in district.values()]
    assert values.count(40) == 3


def test_reference_scenario(example):
    assert validate(example) == []
    result = simulate(example)
    assert result["cost"] == 95
    assert result["N_crit"] == 0
    assert result["Score"] == pytest.approx(56.54, abs=0.01, rel=0)
    assert result["D_avg"] == pytest.approx(58.0776, abs=0.01, rel=0)
    assert result["D_min"] == pytest.approx(52.9625, abs=0.01, rel=0)
    assert result["indicators"]["Нура"] == pytest.approx(
        {"T1": 55, "T2": 40, "E1": 45, "E2": 65, "S1": 48, "S2": 43.75,
         "B1": 67.5, "B2": 51.75, "C1": 60, "C2": 54.375},
        abs=0.01, rel=0,
    )


def test_cheapest_scenario(cheapest):
    assert validate(cheapest) == []
    assert simulate(cheapest)["cost"] == 61


@pytest.mark.parametrize("count", [0, 4, 6])
def test_exactly_five_measures(cheapest, count):
    decisions = cheapest[:count]
    if count == 6:
        decisions.append({"measure_id": "M1", "district": "Есиль"})
    errors = validate(decisions)
    assert len(errors) == 1
    assert "ровно 5" in errors[0]


@pytest.mark.parametrize("district", ["Нура", "Есиль"])
def test_no_repeats_even_in_different_districts(cheapest, district):
    cheapest[1] = {"measure_id": "M10", "district": district}
    errors = validate(cheapest)
    assert len(errors) == 1
    assert "повторы запрещены" in errors[0]


def test_over_budget(example):
    example[1] = {"measure_id": "M3", "district": "Нура"}
    errors = validate(example)
    assert len(errors) == 1
    assert "105 превышает бюджет 100" in errors[0]


def test_exact_budget_is_valid():
    decisions = [
        {"measure_id": "M3", "district": "Нура"},
        {"measure_id": "M7", "district": "Нура"},
        {"measure_id": "M6"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12"},
    ]
    assert simulate(decisions)["cost"] == 100
    assert validate(decisions) == []


def test_global_conflict_in_different_districts(cheapest):
    cheapest[1] = {"measure_id": "M1", "district": "Нура"}
    cheapest[4] = {"measure_id": "M3", "district": "Есиль"}
    errors = validate(cheapest)
    assert len(errors) == 1
    assert "M1 и M3 несовместимы в любых районах" in errors[0]


@pytest.mark.parametrize("pair", [("M4", "M7"), ("M5", "M13")])
@pytest.mark.parametrize("same_district", [True, False])
def test_district_conflicts(pair, same_district):
    first, second = pair
    decisions = [
        {"measure_id": first, "district": "Нура"},
        {"measure_id": second, "district": "Нура" if same_district else "Есиль"},
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12"},
    ]
    errors = validate(decisions)
    if same_district:
        assert len(errors) == 1
        assert f"{first} и {second} несовместимы в районе «Нура»" in errors[0]
    else:
        assert errors == []


def test_three_measures_in_one_direction(example):
    example[4] = {"measure_id": "M9", "district": "Нура"}
    errors = validate(example)
    assert len(errors) == 1
    assert "Соцсфера" in errors[0]
    assert "не больше 2" in errors[0]


@pytest.mark.parametrize("district", ["Нура", None, ""])
def test_city_measure_must_not_have_district(cheapest, district):
    cheapest[3]["district"] = district
    errors = validate(cheapest)
    assert len(errors) == 1
    assert "Для городской меры M12 нельзя указывать район" in errors[0]


def test_district_is_required(cheapest):
    del cheapest[0]["district"]
    errors = validate(cheapest)
    assert len(errors) == 1
    assert "обязательно указать район" in errors[0]


def test_district_must_exist(cheapest):
    cheapest[0]["district"] = "Несуществующий район"
    errors = validate(cheapest)
    assert len(errors) == 1
    assert "неизвестный район" in errors[0]


def test_unknown_measure(cheapest):
    cheapest[0]["measure_id"] = "M404"
    errors = validate(cheapest)
    assert len(errors) == 1
    assert "неизвестная мера" in errors[0]


@pytest.mark.parametrize("decision", [None, {}, {"measure_id": []}])
def test_malformed_decision_returns_error(cheapest, decision):
    cheapest[0] = decision
    assert validate(cheapest)


def test_decisions_must_be_list():
    assert validate(None) == ["Решения должны быть переданы списком."]


def test_reproducibility_and_order_independence(example, cheapest):
    before = deepcopy(example)
    first = simulate(example)
    simulate(cheapest)
    assert first == simulate(example)
    assert first == simulate(list(reversed(example)))
    assert example == before
    assert first["Score"] != pytest.approx(simulate(cheapest)["Score"], abs=0.01, rel=0)


def test_city_effect_and_lag():
    baseline = simulate([])
    result = simulate([{"measure_id": "M12"}])
    for district, values in result["indicators"].items():
        expected = baseline["indicators"][district].copy()
        expected["C2"] += 4.375
        assert values == pytest.approx(expected, abs=0.01, rel=0)


def test_district_effect_and_negative_effect():
    baseline = simulate([])
    result = simulate([{"measure_id": "M11", "district": "Нура"}])
    for district, values in result["indicators"].items():
        expected = baseline["indicators"][district].copy()
        if district == "Нура":
            expected["T1"] = 53.25
            expected["B2"] = 60.5
        assert values == pytest.approx(expected, abs=0.01, rel=0)


@pytest.mark.parametrize("district_measure,city_measure,indicator,expected_target,expected_other", [
    ("M1", "M2", "T1", 64.5, 48.0),
    ("M10", "M12", "B1", 67.5, 78.0),
    ("M5", "M6", "E2", 77.25, 73.5),
])
def test_synergy_is_fixed_and_local(
    district_measure, city_measure, indicator, expected_target, expected_other,
):
    local = {"measure_id": district_measure, "district": "Нура"}
    city = {"measure_id": city_measure}
    both = simulate([local, city])
    assert both["indicators"]["Нура"][indicator] == pytest.approx(expected_target, abs=0.01, rel=0)
    assert both["indicators"]["Есиль"][indicator] == pytest.approx(expected_other, abs=0.01, rel=0)
    baseline = simulate([])
    only_local = simulate([local])
    only_city = simulate([city])
    for district in baseline["indicators"]:
        combined = both["indicators"][district][indicator]
        without_bonus = (
            only_local["indicators"][district][indicator]
            + only_city["indicators"][district][indicator]
            - baseline["indicators"][district][indicator]
        )
        assert combined - without_bonus == pytest.approx(
            2 if district == "Нура" else 0, abs=0.01, rel=0,
        )


def test_clip_only_after_all_effects_and_synergies(tmp_path, monkeypatch):
    city = load_city()
    city["districts"]["Нура"]["indicators"]["B1"] = 85
    city["measures"] = [
        {"id": "A", "type": "district", "cost": 1, "lag": 0,
         "effects": {"T1": 200, "T2": -200, "B1": 20}},
        {"id": "B", "type": "city", "cost": 1, "lag": 0,
         "effects": {"T1": -10, "T2": 10, "B1": -10}},
    ]
    city["synergies"] = [{"measures": ["A", "B"], "target_measure": "A", "effects": {"B1": 2}}]
    path = tmp_path / "city.json"
    path.write_text(json.dumps(city), encoding="utf-8")
    monkeypatch.setattr(data_module, "CITY_PATH", path)
    result = simulate([{"measure_id": "A", "district": "Нура"}, {"measure_id": "B"}])
    assert result["indicators"]["Нура"]["T1"] == 100
    assert result["indicators"]["Нура"]["T2"] == 0
    assert result["indicators"]["Нура"]["B1"] == pytest.approx(97, abs=0.01, rel=0)


def test_measure_ids_and_conflicts_are_loaded_from_json(example, tmp_path, monkeypatch):
    expected = simulate(example)
    city = load_city()
    rename = {measure["id"]: "custom_" + measure["id"] for measure in city["measures"]}
    for measure in city["measures"]:
        measure["id"] = rename[measure["id"]]
    for rule in city["synergies"] + city["incompatibilities"]:
        rule["measures"] = [rename[measure_id] for measure_id in rule["measures"]]
        if "target_measure" in rule:
            rule["target_measure"] = rename[rule["target_measure"]]
    for decision in example:
        decision["measure_id"] = rename[decision["measure_id"]]
    path = tmp_path / "city.json"
    path.write_text(json.dumps(city), encoding="utf-8")
    monkeypatch.setattr(data_module, "CITY_PATH", path)
    assert validate(example) == []
    assert simulate(example) == expected
    example[0] = {"measure_id": rename["M13"], "district": "Сарыарка"}
    errors = validate(example)
    assert len(errors) == 1
    assert "custom_M5 и custom_M13 несовместимы" in errors[0]


def test_json_loaded_independently_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert simulate([])["Score"] == pytest.approx(52.56, abs=0.01, rel=0)


def test_dataset_integrity():
    city = load_city()
    assert city["budget"] == 100
    assert city["horizon"] == 8
    assert len(city["districts"]) == 5
    assert len(city["weights"]) == 10
    assert len(city["measures"]) == 14
    assert len({measure["id"] for measure in city["measures"]}) == 14
    assert sum(city["weights"].values()) == pytest.approx(1, abs=0.01, rel=0)
    assert sum(d["population_share"] for d in city["districts"].values()) == pytest.approx(1, abs=0.01, rel=0)
    for district in city["districts"].values():
        assert district["indicators"].keys() == city["weights"].keys()

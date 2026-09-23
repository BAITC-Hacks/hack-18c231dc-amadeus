"""Полный перебор и одиночные замены сверяются с validate() и simulate()."""

from itertools import combinations
from pathlib import Path
import random
import subprocess
import sys

import pytest

from engine import optimizer
from engine.data import load_city
from engine.optimizer import cached_top, suggest_swaps
from engine.simulator import simulate
from engine.validator import validate


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def example():
    return load_city()["reference_scenarios"]["example"]["decisions"]


def combo_scores(decisions):
    """Score оптимизатора для конкретного набора из векторного расчёта его комбинации."""
    city = load_city()
    model = optimizer._prepare(city)
    ids = {item["measure_id"] for item in decisions}
    combo = tuple(measure for measure in city["measures"] if measure["id"] in ids)
    district_ids, assignments, scores = optimizer._score_combo(combo, city, model)
    wanted = [model["districts"].index(item["district"])
              for measure_id in district_ids
              for item in decisions if item["measure_id"] == measure_id]
    row = [index for index, assignment in enumerate(assignments) if list(assignment) == wanted]
    assert len(row) == 1
    return float(scores[row[0]])


def test_import_does_not_run_search():
    code = "import engine.optimizer as o; print(o._cached_optimum.cache_info().currsize)"
    output = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert output.stdout.strip() == "0"


def test_number_of_valid_sets():
    assert cached_top()["n_valid"] == 694395


def test_best_set():
    best = cached_top()["top"][0]
    assert best["Score"] == pytest.approx(57.24, abs=0.005, rel=0)
    assert best["cost"] == 98
    assert best["decisions"] == [
        {"measure_id": "M2"},
        {"measure_id": "M3", "district": "Нура"},
        {"measure_id": "M8", "district": "Нура"},
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M14"},
    ]


def test_top_sets_match_simulator_and_validator():
    top = cached_top()["top"]
    assert len(top) == 10
    assert [item["Score"] for item in top] == sorted((item["Score"] for item in top), reverse=True)
    for item in top:
        assert validate(item["decisions"]) == []
        result = simulate(item["decisions"])
        assert item["Score"] == result["Score"]
        assert item["cost"] == result["cost"]


def test_optimizer_score_matches_simulate_on_example(example):
    assert combo_scores(example) == pytest.approx(simulate(example)["Score"], abs=1e-9, rel=0)


def test_optimizer_score_matches_simulate_on_optimum():
    best = cached_top()["top"][0]["decisions"]
    assert combo_scores(best) == pytest.approx(simulate(best)["Score"], abs=1e-9, rel=0)


def test_enumerated_sets_agree_with_validator_on_sample():
    city = load_city()
    model = optimizer._prepare(city)
    combos = list(combinations(city["measures"], city["rules"]["decision_count"]))
    rng = random.Random(7)
    for combo in rng.sample(combos, 60):
        decisions = [{"measure_id": measure["id"]} if measure["type"] == "city"
                     else {"measure_id": measure["id"], "district": "Нура"} for measure in combo]
        if not optimizer._is_valid_combo(combo, city):
            assert validate(decisions) != []
            continue
        district_ids, assignments, scores = optimizer._score_combo(combo, city, model)
        for index in rng.sample(range(len(assignments)), min(5, len(assignments))):
            chosen = optimizer._decisions(combo, assignments[index], district_ids, model["districts"])
            assert validate(chosen) == []
            assert scores[index] == pytest.approx(simulate(chosen)["Score"], abs=1e-9, rel=0)


def test_cached_top_returns_copy():
    first = cached_top()
    first["top"][0]["Score"] = 0
    assert cached_top()["top"][0]["Score"] > 57


def test_suggest_swaps_on_example(example):
    base = simulate(example)["Score"]
    swaps = suggest_swaps(example)
    assert 0 < len(swaps) <= 3
    assert [item["gain"] for item in swaps] == sorted((item["gain"] for item in swaps), reverse=True)
    for item in swaps:
        assert validate(item["decisions"]) == []
        result = simulate(item["decisions"])
        assert item["Score"] == result["Score"]
        assert item["gain"] == pytest.approx(result["Score"] - base, abs=1e-12, rel=0)
        assert item["cost"] == result["cost"] <= load_city()["budget"]
        assert item["replace"] in example and item["with"] not in example
    assert swaps[0]["replace"] == {"measure_id": "M5", "district": "Сарыарка"}
    assert swaps[0]["with"] == {"measure_id": "M3", "district": "Нура"}


def test_suggest_swaps_on_optimum_and_invalid_input(example):
    assert suggest_swaps(cached_top()["top"][0]["decisions"]) == []
    assert suggest_swaps(example[:4]) == []
    assert len(suggest_swaps(example, top_k=1)) == 1

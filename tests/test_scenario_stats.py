"""Проверка полного перебора, сохранённого распределения и строгих процентилей."""

import json

import pytest

from engine import scenario_stats
from engine.data import load_city
from engine.simulator import simulate
from engine.validator import validate
from scripts.precompute_scenarios import district_assignments, distribution_quantiles


@pytest.fixture(scope="module")
def saved_stats():
    return json.loads(scenario_stats.STATS_PATH.read_text(encoding="utf-8"))


def test_saved_enumeration_covers_expected_number_of_scenarios(saved_stats):
    assert saved_stats["valid_scenario_count"] == 694395
    assert saved_stats["dataset_sha256"] == scenario_stats.dataset_fingerprint(load_city())
    assert scenario_stats.STATS_PATH.stat().st_size <= 1_000_000


def test_best_scenario_matches_expected_plan(saved_stats):
    best = saved_stats["best_scenario"]
    assert best["Score"] == pytest.approx(57.24, abs=0.01, rel=0)
    assert best["cost"] == 98
    assert {item["measure_id"]: item.get("district") for item in best["decisions"]} == {
        "M2": None,
        "M3": "Нура",
        "M8": "Нура",
        "M9": "Нура",
        "M14": None,
    }
    assert saved_stats["baseline_score"] == pytest.approx(52.56, abs=0.01, rel=0)
    assert saved_stats["baseline_score"] == simulate([])["Score"]


def test_top_five_are_distinct_valid_scenarios_with_reproducible_scores(saved_stats):
    top_five = saved_stats["top_5"]
    assert len(top_five) == 5
    assert saved_stats["best_scenario"] == top_five[0]
    assert [item["Score"] for item in top_five] == sorted(
        (item["Score"] for item in top_five), reverse=True,
    )
    fingerprints = set()
    for entry in top_five:
        assert validate(entry["decisions"]) == []
        result = simulate(entry["decisions"])
        assert result["Score"] == entry["Score"]
        assert result["cost"] == entry["cost"]
        fingerprints.add(tuple(sorted(
            (item["measure_id"], item.get("district")) for item in entry["decisions"]
        )))
    assert len(fingerprints) == 5


def test_saved_quantiles_are_monotonic_and_cover_the_distribution(saved_stats):
    quantiles = saved_stats["quantiles"]
    assert saved_stats["quantile_step_percent"] == 0.1
    assert len(quantiles) == 1001
    assert [item["percentile"] for item in quantiles] == [step / 10 for step in range(1001)]
    assert [item["score"] for item in quantiles] == sorted(item["score"] for item in quantiles)
    assert quantiles[0]["count_less"] == 0
    assert quantiles[-1]["count_less_or_equal"] == saved_stats["valid_scenario_count"]
    assert quantiles[-1]["score"] == saved_stats["best_scenario"]["Score"]
    for item in quantiles:
        assert 0 <= item["count_less"] <= item["count_less_or_equal"] <= saved_stats["valid_scenario_count"]


@pytest.mark.parametrize("score,expected", [(0, 0), (1, 0), (2, 50), (3, 75), (4, 100)])
def test_percentile_counts_only_strictly_lower_scores(score, expected):
    stats = {"valid_scenario_count": 4, "quantiles": distribution_quantiles([1, 1, 2, 3])}
    assert scenario_stats.estimate_percentile(score, stats) == pytest.approx(expected, abs=0.01, rel=0)


def test_all_equal_scores_have_zero_strict_percentile():
    quantiles = distribution_quantiles([7, 7, 7])
    assert len(quantiles) == 1001
    assert all(item["score"] == 7 for item in quantiles)
    assert all(item["count_less"] == 0 for item in quantiles)
    assert all(item["count_less_or_equal"] == 3 for item in quantiles)
    stats = {"valid_scenario_count": 3, "quantiles": quantiles}
    assert scenario_stats.estimate_percentile(7, stats) == 0


def test_missing_snapshot_does_not_block_comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_stats, "STATS_PATH", tmp_path / "absent.json")
    city = load_city()
    assert scenario_stats.load_stats(city) is None
    assert scenario_stats.scenario_comparison(55, city) == {
        "best_possible_score": None, "gap_to_best": None, "percentile": None,
    }


def test_stale_snapshot_does_not_report_outdated_scores(tmp_path, monkeypatch):
    city = load_city()
    stale_city = load_city()
    stale_city["budget"] += 1
    path = tmp_path / "stale.json"
    path.write_text(json.dumps({
        "dataset_sha256": scenario_stats.dataset_fingerprint(stale_city),
        "best_scenario": {"Score": 999},
    }), encoding="utf-8")
    monkeypatch.setattr(scenario_stats, "STATS_PATH", path)
    assert scenario_stats.load_stats(city) is None
    assert scenario_stats.scenario_comparison(55, city) == {
        "best_possible_score": None, "gap_to_best": None, "percentile": None,
    }


def test_district_assignments_include_each_local_choice_once():
    group = [
        {"id": "local_a", "type": "district"},
        {"id": "whole_city", "type": "city"},
        {"id": "local_b", "type": "district"},
    ]
    assignments = list(district_assignments(group, ("Север", "Юг")))
    assert len(assignments) == 4
    district_pairs = set()
    for decisions in assignments:
        assert [item["measure_id"] for item in decisions] == ["local_a", "whole_city", "local_b"]
        assert decisions[1] == {"measure_id": "whole_city"}
        district_pairs.add((decisions[0]["district"], decisions[2]["district"]))
    assert district_pairs == {
        ("Север", "Север"), ("Север", "Юг"), ("Юг", "Север"), ("Юг", "Юг"),
    }

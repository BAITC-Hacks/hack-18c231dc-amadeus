"""Сравнение сценариев и новые критические значения через HTTP API."""

from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from api import agent, explanation, main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    forbidden = Mock(side_effect=AssertionError("Реальные запросы запрещены"))
    monkeypatch.setattr(explanation, "OpenAI", forbidden)
    monkeypatch.setattr(agent, "OpenAI", forbidden)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def example():
    return [
        {"measure_id": "M7", "district": "Нура"},
        {"measure_id": "M8", "district": "Нура"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12", "district": None},
        {"measure_id": "M5", "district": "Сарыарка"},
    ]


@pytest.fixture
def new_critical_scenario():
    return [
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M11", "district": "Алматы"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12", "district": None},
        {"measure_id": "M4", "district": "Сарыарка"},
    ]


def test_example_percentile_and_gap(client, example):
    response = client.post("/api/simulate", json={"decisions": example})
    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is True
    assert report["best_possible_score"] == pytest.approx(57.24, abs=0.01, rel=0)
    assert report["gap_to_best"] == pytest.approx(report["best_possible_score"] - report["Score"])
    assert report["percentile"] == pytest.approx(99.9, abs=0.01, rel=0)
    assert report["warnings"] == []


def test_best_scenario_has_no_gap(client):
    decisions = [
        {"measure_id": "M2", "district": None},
        {"measure_id": "M3", "district": "Нура"},
        {"measure_id": "M8", "district": "Нура"},
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M14", "district": None},
    ]
    report = client.post("/api/simulate", json={"decisions": decisions}).json()
    assert report["valid"] is True
    assert report["cost"] == 98
    assert report["Score"] == pytest.approx(57.24, abs=0.01, rel=0)
    assert report["gap_to_best"] == pytest.approx(0, abs=1e-10)
    assert 99.9 <= report["percentile"] <= 100


def test_warning_identifies_m11_in_almaty(client, new_critical_scenario):
    report = client.post("/api/simulate", json={"decisions": new_critical_scenario}).json()
    assert report["valid"] is True
    assert report["warnings"] == [{
        "measure_id": "M11", "district": "Алматы", "indicator": "T1",
        "before": 40, "after": 38.25,
    }]


def test_compensated_effect_does_not_warn(client, new_critical_scenario):
    new_critical_scenario[3] = {"measure_id": "M2", "district": None}
    report = client.post("/api/simulate", json={"decisions": new_critical_scenario}).json()
    assert report["valid"] is True
    assert report["districts"]["Алматы"]["indicators"]["T1"]["after"] >= 40
    assert report["warnings"] == []


def test_explain_forwards_comparison_and_warnings(client, new_critical_scenario, monkeypatch):
    answer = explanation.Explanation(
        summary="Сценарий рассчитан. Есть новое критическое значение.",
        strengths=[], risks=[], consequences=[], recommendations=[],
    )
    model = Mock(return_value=(answer, True))
    monkeypatch.setattr(main, "explain", model)
    report = client.post("/api/simulate", json={"decisions": new_critical_scenario}).json()
    result = client.post("/api/explain", json={"decisions": new_critical_scenario}).json()
    assert result["valid"] is True
    assert result["ai_generated"] is True
    model.assert_called_once()
    model_data = model.call_args.args[0]
    for key in ("best_possible_score", "gap_to_best", "percentile", "warnings"):
        assert model_data[key] == report[key]
        assert result[key] == report[key]


def test_template_mentions_comparison_and_warning(client, new_critical_scenario):
    result = client.post("/api/explain", json={"decisions": new_critical_scenario}).json()
    assert result["valid"] is True
    assert result["ai_generated"] is False
    assert any("M11" in item and "Алматы" in item and "T1" in item for item in result["risks"])
    assert any("Лучший допустимый Score" in item for item in result["consequences"])
    assert any("приблизительно" in item and "допустимых наборов" in item for item in result["consequences"])


def test_invalid_scenario_does_not_read_statistics(client, example, monkeypatch):
    comparison = Mock(side_effect=AssertionError("Невалидный набор не сравнивается"))
    warnings = Mock(side_effect=AssertionError("Невалидный набор не рассчитывается"))
    monkeypatch.setattr(main, "scenario_comparison", comparison)
    monkeypatch.setattr(main, "critical_warnings", warnings)
    result = client.post("/api/simulate", json={"decisions": example[:4]}).json()
    assert set(result) == {"valid", "errors"}
    assert result["valid"] is False
    comparison.assert_not_called()
    warnings.assert_not_called()

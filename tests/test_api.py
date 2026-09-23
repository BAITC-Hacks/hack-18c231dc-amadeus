"""HTTP-контракт и интеграция с неизменённым расчётным движком."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import main
from engine.data import load_city
from engine.simulator import simulate


@pytest.fixture
def client():
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


def test_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    config = response.json()
    city = load_city()
    assert config["budget"] == 100
    assert len(config["districts"]) == 5
    assert len(config["measures"]) == 14
    assert config["baseline_score"] == pytest.approx(52.56, abs=0.01, rel=0)
    for key in ("districts", "measures", "synergies", "incompatibilities", "rules", "scoring"):
        assert config[key] == city[key]
    assert len(config["indicators"]) == 10
    for indicator in config["indicators"]:
        source = city["indicators"][indicator["id"]]
        assert indicator["name"] == source["name"]
        assert indicator["direction"] == source["direction"]
        assert indicator["weight"] == city["weights"][indicator["id"]]


def test_simulate_example(client, example):
    response = client.post("/api/simulate", json={"decisions": example})
    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    assert result["cost"] == 95
    assert result["remaining_budget"] == 5
    assert result["Score"] == pytest.approx(56.54, abs=0.01, rel=0)
    assert result["baseline_score"] == pytest.approx(52.56, abs=0.01, rel=0)
    assert result["score_delta"] == pytest.approx(3.98539, abs=0.01, rel=0)
    assert result["D_avg"] == pytest.approx(58.0776, abs=0.01, rel=0)
    assert result["D_min"] == pytest.approx(52.9625, abs=0.01, rel=0)
    assert result["N_crit"] == 0
    assert result["synergies"] == [{
        "measures": ["M10", "M12"], "target_measure": "M10",
        "effects": {"B1": 2}, "district": "Нура",
    }]
    nura = result["districts"]["Нура"]
    assert nura["D_before"] == pytest.approx(49.18, abs=0.01, rel=0)
    assert nura["D_after"] == pytest.approx(52.9625, abs=0.01, rel=0)
    assert nura["indicators"]["S1"] == {"before": 38, "after": 48, "delta": 10}
    assert nura["indicators"]["B1"] == {"before": 55, "after": 67.5, "delta": 12.5}


def test_all_district_deltas_match_engine(client, example):
    response = client.post("/api/simulate", json={"decisions": example}).json()
    baseline = simulate([])
    expected = simulate(load_city()["reference_scenarios"]["example"]["decisions"])
    assert response["Score"] == expected["Score"]
    assert response["districts"].keys() == expected["D"].keys()
    for district, report in response["districts"].items():
        assert report["D_before"] == baseline["D"][district]
        assert report["D_after"] == expected["D"][district]
        assert report["indicators"].keys() == expected["indicators"][district].keys()
        for code, values in report["indicators"].items():
            assert values["before"] == baseline["indicators"][district][code]
            assert values["after"] == expected["indicators"][district][code]
            assert values["delta"] == values["after"] - values["before"]


@pytest.mark.parametrize("violation,reason", [
    ("budget", "превышает бюджет"),
    ("four", "ровно 5"),
    ("empty", "ровно 5"),
    ("city_district", "нельзя указывать район"),
    ("district_null", "обязательно указать район"),
    ("unknown_measure", "неизвестная мера"),
    ("unknown_district", "неизвестный район"),
    ("repeat", "повторы запрещены"),
])
def test_invalid_scenario_does_not_call_simulator(client, example, monkeypatch, violation, reason):
    if violation == "budget":
        example[1] = {"measure_id": "M3", "district": "Нура"}
    elif violation == "four":
        example.pop()
    elif violation == "empty":
        example.clear()
    elif violation == "city_district":
        example[3]["district"] = "Нура"
    elif violation == "district_null":
        example[0]["district"] = None
    elif violation == "unknown_measure":
        example[0]["measure_id"] = "M404"
    elif violation == "unknown_district":
        example[0]["district"] = "Неизвестный район"
    elif violation == "repeat":
        example[1] = deepcopy(example[0])
    simulator = Mock(side_effect=AssertionError("Невалидный набор не должен запускать расчёт"))
    monkeypatch.setattr(main, "simulate", simulator)
    response = client.post("/api/simulate", json={"decisions": example})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"valid", "errors"}
    assert body["valid"] is False
    assert any(reason in error for error in body["errors"])
    simulator.assert_not_called()


def test_omitted_city_district_matches_null(client, example):
    with_null = client.post("/api/simulate", json={"decisions": example}).json()
    del example[3]["district"]
    without_district = client.post("/api/simulate", json={"decisions": example}).json()
    assert without_district == with_null


def test_repeated_requests_do_not_change_config(client, example):
    before = client.get("/api/config").json()
    first = client.post("/api/simulate", json={"decisions": example}).json()
    second = client.post("/api/simulate", json={"decisions": list(reversed(example))}).json()
    assert first == second
    assert client.get("/api/config").json() == before


@pytest.mark.parametrize("payload", [{}, {"decisions": None}, {"decisions": [{"district": "Нура"}]}])
def test_invalid_request_shape_returns_422(client, payload):
    assert client.post("/api/simulate", json=payload).status_code == 422


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://localhost:3000"])
def test_cors_for_local_frontends(client, origin):
    response = client.options("/api/simulate", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    response = client.get("/api/config", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_does_not_allow_other_origins(client):
    response = client.options("/api/simulate", headers={
        "Origin": "http://example.com",
        "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_no_active_synergies(client):
    decisions = [
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M11", "district": "Нура"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M14", "district": None},
        {"measure_id": "M4", "district": "Сарыарка"},
    ]
    result = client.post("/api/simulate", json={"decisions": decisions}).json()
    assert result["valid"] is True
    assert result["synergies"] == []

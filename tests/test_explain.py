"""Объяснения и Structured Outputs без реальных запросов к OpenAI."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIStatusError, APITimeoutError
import pytest

from api import agent, explanation, main
from engine.simulator import simulate


@pytest.fixture(autouse=True)
def isolated_openai(monkeypatch):
    # Даже при наличии настоящего .env эти тесты не могут обратиться к сервису.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    factory = MagicMock(side_effect=AssertionError("Реальный клиент OpenAI запрещён в тестах"))
    monkeypatch.setattr(explanation, "OpenAI", factory)
    monkeypatch.setattr(agent, "OpenAI", factory)
    return factory


@pytest.fixture
def client():
    with TestClient(main.app) as client:
        yield client


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
def model_answer():
    return {
        "summary": "Сценарий улучшает положение Нуры. Критических значений не осталось.",
        "strengths": ["Поддержаны школы и первичная медпомощь."],
        "risks": ["Нура остаётся самым слабым районом."],
        "consequences": ["Меры реализуются с лагом."],
        "recommendations": ["Наблюдать за результатами выбранных мер."],
    }


@pytest.fixture
def model_client(monkeypatch, isolated_openai, model_answer):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    isolated_openai.side_effect = None
    client = isolated_openai.return_value.__enter__.return_value
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text=json.dumps(model_answer, ensure_ascii=False),
    )
    return client


def normalize(decisions):
    return [{key: value for key, value in item.items() if value is not None} for item in decisions]


def explanation_data(decisions):
    # Факты для одиночного explain(): тот же путь, что у /api/explain, без эндпоинта.
    report = main.simulate_scenario(main.SimulationRequest.model_validate({"decisions": decisions}))
    return explanation.build_explanation_data(normalize(decisions), report)


def test_each_measure_contribution_is_positive(client, example):
    response = client.post("/api/explain", json={"decisions": example})
    assert response.status_code == 200
    contributions = response.json()["contributions"]
    assert len(contributions) == 5
    assert {item["measure_id"] for item in contributions} == {item["measure_id"] for item in example}
    decisions = normalize(example)
    full = simulate(decisions)
    for item in contributions:
        without = simulate([decision for decision in decisions if decision["measure_id"] != item["measure_id"]])
        assert item["contribution"] > 0
        assert item["contribution"] == pytest.approx(full["Score"] - without["Score"], abs=0.01, rel=0)
        assert item["score_without"] == without["Score"]


def test_contributions_call_simulator_directly_for_four_measures(example, monkeypatch):
    decisions = normalize(example)
    report = main.simulate_scenario(main.SimulationRequest(decisions=example))
    simulator = Mock(wraps=simulate)
    monkeypatch.setattr(explanation, "simulate", simulator)
    explanation.build_explanation_data(decisions, report)
    assert simulator.call_count == 5
    assert all(len(call.args[0]) == 4 for call in simulator.call_args_list)


@pytest.mark.parametrize("violation", ["four", "budget"])
def test_invalid_does_not_call_openai(client, example, model_client, isolated_openai, monkeypatch, violation):
    if violation == "four":
        example.pop()
    else:
        example[1] = {"measure_id": "M3", "district": "Нура"}
    simulator = Mock(side_effect=AssertionError("Расчёт не должен запускаться"))
    monkeypatch.setattr(main, "simulate", simulator)
    response = client.post("/api/explain", json={"decisions": example})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"valid", "errors"}
    assert body["valid"] is False
    assert body["errors"]
    isolated_openai.assert_not_called()
    model_client.responses.create.assert_not_called()
    simulator.assert_not_called()


def test_no_key_returns_template(client, example, isolated_openai):
    response = client.post("/api/explain", json={"decisions": example})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["ai_generated"] is False
    assert "Нура" in body["summary"]
    best = max(body["contributions"], key=lambda item: item["contribution"])
    assert any(best["measure_id"] in point for point in body["strengths"])
    assert any("критических значений не осталось" in point for point in body["strengths"])
    explanation.Explanation.model_validate({key: body[key] for key in explanation.Explanation.model_fields})
    isolated_openai.assert_not_called()


def test_no_model_returns_template(client, example, isolated_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("OPENAI_MODEL", "")
    assert client.post("/api/explain", json={"decisions": example}).json()["ai_generated"] is False
    isolated_openai.assert_not_called()


def test_template_reports_remaining_critical_values(client):
    decisions = [
        {"measure_id": "M9", "district": "Нура"},
        {"measure_id": "M11", "district": "Нура"},
        {"measure_id": "M10", "district": "Нура"},
        {"measure_id": "M12", "district": None},
        {"measure_id": "M4", "district": "Сарыарка"},
    ]
    body = client.post("/api/explain", json={"decisions": decisions}).json()
    assert body["valid"] is True
    assert body["ai_generated"] is False
    assert any("Нура, S2 = 37.625" in point for point in body["risks"])
    assert not any("критических значений не осталось" in point for point in body["strengths"])


def test_structured_model_answer(example, model_client, model_answer, isolated_openai):
    result, ai_generated = explanation.explain(explanation_data(example))
    assert ai_generated is True
    assert result.model_dump() == model_answer
    isolated_openai.assert_called_once_with(api_key="test-key-not-a-secret", timeout=20.0, max_retries=0)
    request = model_client.responses.create.call_args.kwargs
    assert request["model"] == "test-model"
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["strict"] is True
    schema = request["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(model_answer)


def test_model_receives_only_server_facts(example, model_client):
    facts = explanation_data(example)
    explanation.explain(facts)
    request = model_client.responses.create.call_args.kwargs
    assert request["input"][0]["role"] == "system"
    data = json.loads(request["input"][1]["content"])
    assert data["Score"] == simulate(normalize(example))["Score"]
    assert data["baseline_score"] == simulate([])["Score"]
    assert data["remaining_budget"] == 5
    assert data["weakest_districts"] == ["Нура"]
    assert data["unchanged_districts"] == []  # Городская платформа меняет все районы.
    assert data["critical_values"] == {
        "before": [
            {"district": "Нура", "indicator": "S1", "value": 38},
            {"district": "Нура", "indicator": "S2", "value": 35},
        ],
        "after": [],
    }
    assert data["contributions"] == facts["contributions"]
    assert len(data["catalog"]) == 14
    assert len(data["selected_measures"]) == 5
    assert all("cost" in item and "lag" in item for item in data["selected_measures"])
    assert data["synergies"][0]["measures"] == ["M10", "M12"]
    assert "test-key-not-a-secret" not in request["input"][1]["content"]
    assert data["recommendation_options"]
    for option in data["recommendation_options"]:
        decisions = [item for item in normalize(example) if item["measure_id"] != option["replace_measure_id"]]
        replacement = {"measure_id": option["measure_id"]}
        if "district" in option:
            replacement["district"] = option["district"]
        decisions.append(replacement)
        assert validate(decisions) == []
        assert option["cost"] == simulate(decisions)["cost"]
        assert option["remaining_budget"] == 100 - option["cost"]


@pytest.mark.parametrize("error_kind", ["connection", "timeout", "unauthorized", "rate_limit", "server"])
def test_openai_error_returns_template(client, example, model_client, error_kind):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    if error_kind == "connection":
        error = APIConnectionError(request=request)
    elif error_kind == "timeout":
        error = APITimeoutError(request=request)
    else:
        status = {"unauthorized": 401, "rate_limit": 429, "server": 500}[error_kind]
        error = APIStatusError("private-service-error", response=httpx.Response(status, request=request), body=None)
    model_client.responses.create.side_effect = error
    response = client.post("/api/explain", json={"decisions": example})
    assert response.status_code == 200
    assert response.json()["ai_generated"] is False
    assert response.json()["summary"]
    assert "private-service-error" not in response.text


@pytest.mark.parametrize("status,output", [
    ("incomplete", ""),
    ("completed", ""),  # Отказ модели без output_text.
    ("completed", "not json"),
    ("completed", '{"summary":"Нет остальных полей"}'),
])
def test_unusable_model_response_returns_template(client, example, model_client, status, output):
    model_client.responses.create.side_effect = [
        tool_turn("resp_1", call("score_scenario", {}, "call_1")),
        final_turn(output, status=status),
    ]
    response = client.post("/api/explain", json={"decisions": example})
    assert response.status_code == 200
    assert response.json()["ai_generated"] is False


def test_client_cannot_supply_score(client, example, isolated_openai):
    response = client.post("/api/explain", json={"decisions": example, "Score": 100})
    assert response.status_code == 422
    isolated_openai.assert_not_called()


def test_explain_endpoint_uses_agent(client, example, model_answer, monkeypatch):
    assert main.explain is agent.explain_with_agent
    answer = agent.AgentExplanation(**model_answer, tools_called=["score_scenario", "get_optimum"])
    monkeypatch.setattr(main, "explain", lambda data: (answer, True))
    body = client.post("/api/explain", json={"decisions": example}).json()
    assert body["ai_generated"] is True
    assert body["tools_called"] == ["score_scenario", "get_optimum"]
    assert body["summary"] == model_answer["summary"]


def test_agent_fallback_without_key_reports_no_tools(client, example, isolated_openai):
    body = client.post("/api/explain", json={"decisions": example}).json()
    assert body["ai_generated"] is False
    assert body["tools_called"] == []
    isolated_openai.assert_not_called()

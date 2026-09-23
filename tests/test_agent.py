"""Агент с tool calling на подменённом клиенте OpenAI, без сети."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
from openai import APITimeoutError
import pytest

from api import agent
from api.explanation import Explanation, build_explanation_data, template_explanation
from api.main import SimulationRequest, simulate_scenario
from engine.optimizer import cached_top
from engine.simulator import simulate


EXAMPLE = [
    {"measure_id": "M7", "district": "Нура"},
    {"measure_id": "M8", "district": "Нура"},
    {"measure_id": "M10", "district": "Нура"},
    {"measure_id": "M12"},
    {"measure_id": "M5", "district": "Сарыарка"},
]
AS_MODEL = [{"measure_id": item["measure_id"], "district": item.get("district")} for item in EXAMPLE]


@pytest.fixture(autouse=True)
def isolated_openai(monkeypatch):
    # Даже при наличии настоящего .env тесты не могут обратиться к сервису.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    factory = MagicMock(side_effect=AssertionError("Реальный клиент OpenAI запрещён в тестах"))
    monkeypatch.setattr(agent, "OpenAI", factory)
    return factory


@pytest.fixture
def data():
    report = simulate_scenario(SimulationRequest(decisions=EXAMPLE))
    return build_explanation_data(EXAMPLE, report)


def call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name,
                           arguments=json.dumps(arguments, ensure_ascii=False), call_id=call_id)


def tool_turn(response_id, *calls):
    return SimpleNamespace(id=response_id, status="completed", output=list(calls), output_text="")


def final_turn(answer, status="completed"):
    text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    return SimpleNamespace(id="resp_final", status=status,
                           output=[SimpleNamespace(type="message")], output_text=text)


@pytest.fixture
def scripted(monkeypatch, isolated_openai):
    """Клиент отдаёт заранее заданные ответы по очереди и запоминает запросы."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-secret")
    isolated_openai.side_effect = None
    client = isolated_openai.return_value.__enter__.return_value

    def script(*responses):
        client.responses.create.side_effect = list(responses)
        return client.responses.create

    return script


def scenario_answer():
    """Только числа из score_scenario."""
    score = round(simulate(EXAMPLE)["Score"], 2)
    return {
        "summary": f"Score сценария — {score} при стоимости 95.",
        "strengths": ["Поддержана Нура: M7 и M8."],
        "risks": ["Лаг M7 — 3 квартала."],
        "consequences": ["Остаток бюджета — 5."],
        "recommendations": ["Сравнить замены по результату suggest_swaps."],
    }


def optimum_answer():
    """Только числа из get_optimum."""
    best = cached_top()["top"][0]
    return {
        "summary": f"Лучший набор перебора даёт Score {round(best['Score'], 2)}.",
        "strengths": ["Перебор покрывает все валидные наборы."],
        "risks": [],
        "consequences": [f"Стоимость лучшего набора — {best['cost']}."],
        "recommendations": ["Сравнить сценарий с лучшим набором."],
    }


def grounded_answer():
    scenario, optimum = scenario_answer(), optimum_answer()
    return {key: scenario[key] + (" " if key == "summary" else []) + optimum[key] for key in scenario}


def test_without_key_returns_template_without_client(data, isolated_openai, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    explanation, ai_generated = agent.explain_with_agent(data)
    assert ai_generated is False
    assert explanation.tools_called == []
    assert explanation.model_dump(exclude={"tools_called"}) == template_explanation(data).model_dump()
    isolated_openai.assert_not_called()


def test_agent_calls_engine_tools_and_answer_is_grounded(data, scripted):
    create = scripted(
        tool_turn("resp_1", call("score_scenario", {}, "call_1")),
        tool_turn("resp_2", call("suggest_swaps", {"top_k": 3}, "call_2")),
        tool_turn("resp_3", call("get_optimum", {"top_k": 1}, "call_3")),
        final_turn(grounded_answer()),
    )
    explanation, ai_generated = agent.explain_with_agent(data)

    assert ai_generated is True
    assert isinstance(explanation, Explanation)
    assert explanation.tools_called == ["score_scenario", "suggest_swaps", "get_optimum"]
    assert set(explanation.model_dump()) == set(Explanation.model_fields) | {"tools_called"}
    assert create.call_count == 4

    first = create.call_args_list[0].kwargs
    assert first["model"] == "test-model"
    assert {tool["name"] for tool in first["tools"]} == {"score_scenario", "suggest_swaps", "get_optimum"}
    assert first["parallel_tool_calls"] is False
    assert json.loads(first["input"][1]["content"]) == {"decisions": AS_MODEL}

    second = create.call_args_list[1].kwargs
    assert second["previous_response_id"] == "resp_1"
    [output] = second["input"]
    assert output["type"] == "function_call_output" and output["call_id"] == "call_1"
    scored = json.loads(output["output"])
    assert scored["Score"] == round(simulate(EXAMPLE)["Score"], 2)
    assert scored["cost"] == 95

    swaps = json.loads(create.call_args_list[2].kwargs["input"][0]["output"])["swaps"]
    assert swaps[0]["with"] == {"measure_id": "M3", "district": "Нура"}


def test_number_not_from_tools_falls_back(data, scripted):
    answer = scenario_answer() | {"summary": "Score вырастет на 12.5 пункта."}
    scripted(
        tool_turn("resp_1", call("score_scenario", {}, "call_1")),
        final_turn(answer),
    )
    explanation, ai_generated = agent.explain_with_agent(data)
    assert ai_generated is False
    assert explanation.tools_called == []
    assert explanation.summary == template_explanation(data).summary


def test_answer_without_tool_calls_is_rejected(data, scripted):
    scripted(final_turn(scenario_answer() | {"summary": "Сценарий без чисел.", "risks": [], "consequences": []}))
    assert agent.explain_with_agent(data)[1] is False


def test_tools_are_bound_to_user_scenario(data, scripted):
    # Даже если модель передаст чужой набор, инструменты считают сценарий пользователя.
    best = cached_top()["top"][0]["decisions"]
    create = scripted(
        tool_turn("resp_1", call("score_scenario", {"decisions": best}, "call_1")),
        tool_turn("resp_2", call("suggest_swaps", {"decisions": best, "top_k": 1}, "call_2")),
        final_turn(scenario_answer()),
    )
    assert agent.explain_with_agent(data)[1] is True
    scored = json.loads(create.call_args_list[1].kwargs["input"][0]["output"])
    assert scored["Score"] == round(simulate(EXAMPLE)["Score"], 2)
    swaps = json.loads(create.call_args_list[2].kwargs["input"][0]["output"])
    assert swaps["swaps"][0]["replace"] == {"measure_id": "M5", "district": "Сарыарка"}


def test_tool_call_limit(data, scripted):
    turns = [tool_turn("resp_0", call("score_scenario", {}, "call_0"))]
    turns += [tool_turn(f"resp_{index}", call("get_optimum", {"top_k": 3}, f"call_{index}"))
              for index in range(1, 4)]
    create = scripted(*turns, final_turn(optimum_answer()))
    explanation, ai_generated = agent.explain_with_agent(data)
    assert ai_generated is True
    assert explanation.tools_called == ["score_scenario"] + ["get_optimum"] * 3
    assert [item.kwargs["tool_choice"] for item in create.call_args_list] == ["auto"] * 4 + ["none"]


def test_model_ignoring_limit_gets_template(data, scripted):
    turns = [tool_turn(f"resp_{index}", call("get_optimum", {"top_k": 3}, f"call_{index}"))
             for index in range(6)]
    create = scripted(*turns)
    explanation, ai_generated = agent.explain_with_agent(data)
    assert ai_generated is False
    assert create.call_count == agent.MAX_TOOL_CALLS + 1
    assert create.call_args_list[-1].kwargs["tool_choice"] == "none"
    assert explanation.tools_called == []


@pytest.mark.parametrize("failure", [
    APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
    RuntimeError("неожиданная ошибка"),
])
def test_errors_and_timeouts_fall_back_without_raising(data, scripted, failure):
    scripted(failure)
    explanation, ai_generated = agent.explain_with_agent(data)
    assert ai_generated is False
    assert explanation.summary == template_explanation(data).summary


@pytest.mark.parametrize("final", [
    final_turn(scenario_answer(), status="incomplete"),
    final_turn("не JSON"),
    final_turn({"summary": "нет остальных полей"}),
])
def test_bad_final_answer_falls_back(data, scripted, final):
    scripted(tool_turn("resp_1", call("score_scenario", {}, "call_1")), final)
    assert agent.explain_with_agent(data)[1] is False


def test_bad_tool_arguments_are_returned_to_model(data, scripted):
    create = scripted(
        tool_turn("resp_1", call("get_optimum", {"wrong": 1}, "call_1")),
        tool_turn("resp_2", call("score_scenario", {}, "call_2")),
        final_turn(scenario_answer()),
    )
    explanation, ai_generated = agent.explain_with_agent(data)
    assert ai_generated is True
    assert json.loads(create.call_args_list[1].kwargs["input"][0]["output"]) == {
        "error": "Некорректные аргументы инструмента"}


def test_tools_use_engine_numbers():
    invalid = agent.score_scenario(AS_MODEL[:4])
    assert invalid["valid"] is False and invalid["errors"]
    scored = agent.score_scenario(AS_MODEL)
    assert scored["Score"] == round(simulate(EXAMPLE)["Score"], 2)
    assert scored["remaining_budget"] == 5
    optimum = agent.get_optimum(10)
    assert len(optimum["top"]) == agent.MAX_TOP_K
    assert optimum["n_valid"] == 694395
    assert optimum["top"][0]["Score"] == 57.24
    assert len(agent.suggest_swaps_tool(AS_MODEL, 99)["swaps"]) <= agent.MAX_TOP_K


def test_unverified_numbers():
    results = [{"Score": 57.24, "cost": 98, "lag": 1, "note": "Стоимость 105 превышает бюджет 100."}]

    def check(summary):
        answer = Explanation(summary=summary, strengths=[], risks=[], consequences=[], recommendations=[])
        return agent.unverified_numbers(answer, results)

    assert check("Меры M14, M2 и показатель T1; Score 57,24 при стоимости 98.") == []
    assert check("Бюджет 100, набор стоил бы 105.") == []
    assert check("Score 57.3") == ["57.3"]
    assert check("Рост на 15%") == ["15"]
    # Замечания ревью: точное совпадение, знак, 1e6 и 1_000 целиком.
    assert check("Score 57.2") == ["57.2"]
    assert check("Остаток −98 и -105") == ["−98", "-105"]
    assert check("Бюджет 1e6 или 1_000") == ["1e6", "1_000"]
    negative = [{"score_delta": -0.3}]
    answer = Explanation(summary="Score снизился на 0.3, изменение −0.3.", strengths=[], risks=[],
                         consequences=[], recommendations=[])
    assert agent.unverified_numbers(answer, negative) == []

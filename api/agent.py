"""Агент с tool calling: модель сама вызывает движок и объясняет только его числа.

Все числа считает engine/. Модель выбирает, какие проверки запустить
(score_scenario, suggest_swaps, get_optimum), и пишет объяснение. Любое число
в ответе должно найтись в результатах инструментов, иначе возвращается
локальный шаблон, как у explain() без ключа.
"""

import json
import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import Field

from api.explanation import Explanation, template_explanation
from engine.data import load_city
from engine.optimizer import cached_top, suggest_swaps
from engine.simulator import simulate
from engine.validator import validate


load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 4
MAX_TOP_K = 3
# Reasoning-модели думают дольше; лимит токенов не задаём, чтобы ответ не обрывался со статусом incomplete.
REQUEST_TIMEOUT = 60.0
TOTAL_TIMEOUT = 90.0
# Первый ход всегда считает сценарий пользователя: без этого объяснять нечего.
FIRST_TOOL = {"type": "function", "name": "score_scenario"}
# Числа, не приклеенные к буквам: коды M14, T1 не считаются числами.
# 1_000 и 1e6 разбираются целиком, чтобы не пройти проверку по первой цифре.
NUMBER_RE = re.compile(r"(?<![\w.,])[+\-−]?\d[\d_]*(?:[.,]\d+)?(?:[eE][+\-]?\d+)?")


class AgentExplanation(Explanation):
    tools_called: list[str] = Field(default_factory=list, description="Инструменты в порядке вызова.")


SYSTEM_PROMPT = """Ты аналитик акимата Астаны. Отвечай на русском.
Пользователь передаёт сценарий из мер. Сам ты ничего не считаешь: у тебя есть
инструменты расчётного движка.
1. Сначала вызови score_scenario: он считает сценарий пользователя.
2. Чтобы дать рекомендацию, вызови suggest_swaps (замены в сценарии
   пользователя) и/или get_optimum (лучшие наборы полного перебора).
   Рекомендации — только из их результатов.
3. Всего не больше 4 вызовов инструментов.
Score сценария пользователя — только поле Score из score_scenario. Score из
suggest_swaps и get_optimum относятся к другим наборам — так и подписывай.
Числа бери ТОЛЬКО из результатов инструментов и копируй как есть. Ничего не
вычисляй: не складывай, не вычитай, не переводи в проценты, не округляй.
Нужного числа нет в результатах — пиши без числа.
Объясни компромиссы: самый слабый район, критические значения, лаги мер,
что даёт замена или оптимум. Не добавляй внешних фактов о городе.
Верни JSON по схеме: summary — два или три предложения; strengths, risks,
consequences, recommendations — списки коротких пунктов, без Markdown-разметки.
"""

TOP_K_SCHEMA = {"type": "integer", "description": "Сколько вариантов вернуть, от 1 до 3."}

TOOLS = [
    {
        "type": "function",
        "name": "score_scenario",
        "description": "Проверить сценарий пользователя по правилам и рассчитать Score, D по районам, стоимость, лаги мер.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "suggest_swaps",
        "description": "Лучшие одиночные замены меры в сценарии пользователя, которые повышают Score и проходят правила.",
        "parameters": {
            "type": "object",
            "properties": {"top_k": TOP_K_SCHEMA},
            "required": ["top_k"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_optimum",
        "description": "Лучшие наборы по Score из полного перебора всех валидных наборов в бюджете.",
        "parameters": {
            "type": "object",
            "properties": {"top_k": TOP_K_SCHEMA},
            "required": ["top_k"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def _rounded(value):
    """Округление для показа модели делает код, а не LLM."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def _clean(decisions: list[dict]) -> list[dict]:
    """district = None означает городскую меру: validate() не принимает ключ."""
    return [{key: value for key, value in item.items() if value is not None} for item in decisions]


def _top_k(value) -> int:
    return max(1, min(MAX_TOP_K, int(value)))


def score_scenario(decisions: list[dict]) -> dict:
    city = load_city()
    decisions = _clean(decisions)
    errors = validate(decisions)
    if errors:
        return {"valid": False, "errors": errors}
    result = simulate(decisions)
    baseline = simulate([])
    measures = {measure["id"]: measure for measure in city["measures"]}
    return _rounded({
        "valid": True,
        "Score": result["Score"],
        "baseline_Score": baseline["Score"],
        "score_delta": result["Score"] - baseline["Score"],
        "cost": result["cost"],
        "budget": city["budget"],
        "remaining_budget": city["budget"] - result["cost"],
        "D": result["D"],
        "D_avg": result["D_avg"],
        "D_min": result["D_min"],
        "N_crit": result["N_crit"],
        "baseline_N_crit": baseline["N_crit"],
        "critical_threshold": city["scoring"]["critical_threshold"],
        "horizon_quarters": city["horizon"],
        "rules": city["rules"],
        "measures": [
            {key: measures[item["measure_id"]][key]
             for key in ("id", "name", "direction", "type", "cost", "lag", "effects")}
            | {"district": item.get("district")}
            for item in decisions
        ],
    })


def suggest_swaps_tool(decisions: list[dict], top_k: int) -> dict:
    decisions = _clean(decisions)
    errors = validate(decisions)
    if errors:
        return {"valid": False, "errors": errors}
    return _rounded({
        "valid": True,
        "Score": simulate(decisions)["Score"],
        "swaps": [
            {key: swap[key] for key in ("replace", "with", "Score", "gain", "cost")}
            for swap in suggest_swaps(decisions, top_k=_top_k(top_k))
        ],
    })


def get_optimum(top_k: int) -> dict:
    optimum = cached_top()
    return _rounded({"n_valid": optimum["n_valid"], "top": optimum["top"][:_top_k(top_k)]})


def run_tool(name: str, arguments: str, scenario: list[dict]) -> dict:
    """Инструменты привязаны к сценарию пользователя: модель не может подменить набор.

    Ошибка аргументов возвращается модели как данные, а не исключение.
    """
    try:
        args = json.loads(arguments)
        if name == "score_scenario":
            return score_scenario(scenario)
        if name == "suggest_swaps":
            return suggest_swaps_tool(scenario, args["top_k"])
        if name == "get_optimum":
            return get_optimum(args["top_k"])
        return {"error": f"Неизвестный инструмент {name}"}
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"error": "Некорректные аргументы инструмента"}


def _numbers(value, found: set[float]) -> set[float]:
    """Все числа из результатов со знаком, включая числа внутри строк."""
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, str):
        found.update(_parse(raw) for raw in NUMBER_RE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            _numbers(item, found)
    elif isinstance(value, list):
        for item in value:
            _numbers(item, found)
    return found


def _parse(raw: str) -> float:
    return float(raw.replace("−", "-").replace(",", "."))


def unverified_numbers(explanation: Explanation, tool_results: list) -> list[str]:
    """Числа ответа, которых нет в результатах инструментов.

    Сравнение точное: инструменты уже отдают округлённые кодом значения.
    Число без знака может описывать отрицательное значение словами («снизился на 0.3»),
    число с явным знаком должно совпасть со знаком.
    """
    allowed = _numbers(tool_results, set())
    text = "\n".join([explanation.summary, *explanation.strengths, *explanation.risks,
                      *explanation.consequences, *explanation.recommendations])

    def known(value: float) -> bool:
        return any(abs(value - item) <= 1e-9 for item in allowed)

    bad = []
    for raw in NUMBER_RE.findall(text):
        value = _parse(raw)
        signed = raw[0] in "+-−"
        if not (known(value) or (not signed and known(-value))):
            bad.append(raw)
    return bad


def _scenario(data: dict) -> list[dict]:
    return [
        {"measure_id": item["id"], "district": item.get("district")}
        for item in data["selected_measures"]
    ]


def _reject(reason: str, *args) -> None:
    """Причина отказа в журнал сервера: только тип и статус, без текста запросов и ключей."""
    logger.warning("Агент вернул шаблон: " + reason, *args)
    return None


def _tool_choice(called: list[str]):
    if not called:
        return FIRST_TOOL
    return "auto" if len(called) < MAX_TOOL_CALLS else "none"


def run_agent(client, model: str, data: dict) -> AgentExplanation | None:
    """Цикл tool calling; None означает, что ответ модели не принят."""
    deadline = time.monotonic() + TOTAL_TIMEOUT
    scenario = _scenario(data)
    called, results = [], []
    scenario_scored = False
    request = {
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                {"decisions": scenario}, ensure_ascii=False, separators=(",", ":"))},
        ],
    }
    for _ in range(MAX_TOOL_CALLS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _reject("истекло общее время %s с", TOTAL_TIMEOUT)
        response = client.responses.create(
            model=model,
            tools=TOOLS,
            tool_choice=_tool_choice(called),
            parallel_tool_calls=False,
            text={"format": {
                "type": "json_schema", "name": "city_explanation", "strict": True,
                "schema": Explanation.model_json_schema(),
            }},
            timeout=min(REQUEST_TIMEOUT, remaining),
            **request,
        )
        if response.status != "completed":
            details = getattr(response, "incomplete_details", None)
            return _reject("статус %s, причина %s", response.status, getattr(details, "reason", None))
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            # Без расчёта сценария пользователя объяснять нечего.
            if not scenario_scored:
                return _reject("сценарий пользователя не рассчитан")
            explanation = Explanation.model_validate_json(response.output_text)
            bad = unverified_numbers(explanation, results)
            if bad:
                return _reject("чисел не из инструментов: %s", len(bad))
            return AgentExplanation(**explanation.model_dump(), tools_called=called)
        outputs = []
        for call in calls:
            if len(called) < MAX_TOOL_CALLS:
                result = run_tool(call.name, call.arguments, scenario)
                called.append(call.name)
                results.append(result)
                if call.name == "score_scenario" and result.get("valid"):
                    scenario_scored = True
            else:
                result = {"error": "Лимит вызовов инструментов исчерпан"}
            outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            })
        # Состояние диалога, включая reasoning, хранится на стороне Responses API.
        request = {"previous_response_id": response.id, "input": outputs}
    return _reject("модель не закончила за %s вызовов", MAX_TOOL_CALLS)


def fallback(data: dict) -> AgentExplanation:
    return AgentExplanation(**template_explanation(data).model_dump(), tools_called=[])


def explain_with_agent(data: dict) -> tuple[Explanation, bool]:
    """Та же сигнатура, что у explain(); без ключа, при ошибке или тайм-ауте — шаблон."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not api_key or not model:
        return fallback(data), False
    try:
        with OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT, max_retries=0) as client:
            answer = run_agent(client, model, data)
    except Exception as error:
        # Наружу ничего не выпускаем и текст исключения не пишем: в нём могут быть служебные данные.
        answer = _reject("ошибка %s", type(error).__name__)
    if answer is None:
        return fallback(data), False
    return answer, True

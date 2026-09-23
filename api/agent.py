"""Агент с tool calling: модель сама вызывает движок и объясняет только его числа.

Все числа считает engine/. Модель выбирает, какие проверки запустить
(score_scenario, suggest_swaps, get_optimum), и пишет объяснение. Любое число
в ответе должно найтись в результатах инструментов, иначе возвращается
локальный шаблон, как у explain() без ключа.
"""

import json
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

MAX_TOOL_CALLS = 4
MAX_TOP_K = 3
REQUEST_TIMEOUT = 20.0
TOTAL_TIMEOUT = 45.0
# Числа, не приклеенные к буквам: коды M14, T1 не считаются числами.
NUMBER_RE = re.compile(r"(?<![\w.,])[+\-−]?\d+(?:[.,]\d+)?")


class AgentExplanation(Explanation):
    tools_called: list[str] = Field(default_factory=list, description="Инструменты в порядке вызова.")


SYSTEM_PROMPT = """Ты аналитик акимата Астаны. Отвечай на русском.
Пользователь передаёт сценарий из мер. Сам ты ничего не считаешь: у тебя есть
инструменты расчётного движка.
1. Сначала вызови score_scenario для переданного сценария.
2. Чтобы дать рекомендацию, вызови suggest_swaps и/или get_optimum.
   Рекомендации — только из их результатов.
3. Всего не больше 4 вызовов инструментов.
Числа бери ТОЛЬКО из результатов инструментов и копируй как есть. Ничего не
вычисляй: не складывай, не вычитай, не переводи в проценты, не округляй.
Нужного числа нет в результатах — пиши без числа.
Объясни компромиссы: самый слабый район, критические значения, лаги мер,
что даёт замена или оптимум. Не добавляй внешних фактов о городе.
Верни JSON по схеме: summary — два или три предложения; strengths, risks,
consequences, recommendations — списки коротких пунктов, без Markdown-разметки.
"""

DECISIONS_SCHEMA = {
    "type": "array",
    "description": "Набор мер: measure_id из каталога, district — район для районной меры, null для городской.",
    "items": {
        "type": "object",
        "properties": {
            "measure_id": {"type": "string"},
            "district": {"type": ["string", "null"]},
        },
        "required": ["measure_id", "district"],
        "additionalProperties": False,
    },
}

TOOLS = [
    {
        "type": "function",
        "name": "score_scenario",
        "description": "Проверить набор по правилам и рассчитать Score, D по районам, стоимость, лаги мер.",
        "parameters": {
            "type": "object",
            "properties": {"decisions": DECISIONS_SCHEMA},
            "required": ["decisions"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "suggest_swaps",
        "description": "Лучшие одиночные замены меры в наборе, которые повышают Score и проходят правила.",
        "parameters": {
            "type": "object",
            "properties": {
                "decisions": DECISIONS_SCHEMA,
                "top_k": {"type": "integer", "description": "Сколько замен вернуть, от 1 до 3."},
            },
            "required": ["decisions", "top_k"],
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
            "properties": {
                "top_k": {"type": "integer", "description": "Сколько наборов вернуть, от 1 до 3."},
            },
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


def _clean(decisions) -> list[dict]:
    """district = null от модели означает городскую меру: validate() не принимает ключ."""
    if not isinstance(decisions, list):
        raise ValueError("decisions должен быть списком")
    return [
        {key: value for key, value in item.items() if value is not None}
        if isinstance(item, dict) else item
        for item in decisions
    ]


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


def run_tool(name: str, arguments: str) -> dict:
    """Ошибка аргументов возвращается модели как данные, а не исключение."""
    try:
        args = json.loads(arguments)
        if name == "score_scenario":
            return score_scenario(args["decisions"])
        if name == "suggest_swaps":
            return suggest_swaps_tool(args["decisions"], args["top_k"])
        if name == "get_optimum":
            return get_optimum(args["top_k"])
        return {"error": f"Неизвестный инструмент {name}"}
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"error": "Некорректные аргументы инструмента"}


def _numbers(value, found: set[float]) -> set[float]:
    """Абсолютные значения всех чисел из результатов, включая числа внутри строк."""
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.add(abs(float(value)))
    elif isinstance(value, str):
        found.update(abs(_parse(raw)) for raw in NUMBER_RE.findall(value))
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
    """Числа ответа, которых нет в результатах инструментов (с точностью записанных знаков)."""
    allowed = _numbers(tool_results, set())
    text = "\n".join([explanation.summary, *explanation.strengths, *explanation.risks,
                      *explanation.consequences, *explanation.recommendations])
    bad = []
    for raw in NUMBER_RE.findall(text):
        digits = re.split(r"[.,]", raw)
        decimals = len(digits[1]) if len(digits) > 1 else 0
        value = abs(_parse(raw))
        if not any(abs(value - item) <= 0.5 * 10 ** -decimals + 1e-9 for item in allowed):
            bad.append(raw)
    return bad


def _scenario(data: dict) -> list[dict]:
    return [
        {"measure_id": item["id"], "district": item.get("district")}
        for item in data["selected_measures"]
    ]


def _same_set(arguments: str, scenario: list[dict]) -> bool:
    """Модель посчитала именно сценарий пользователя, а не другой набор."""
    try:
        decisions = _clean(json.loads(arguments)["decisions"])
        key = lambda items: sorted(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in items)
        return key(decisions) == key(_clean(scenario))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


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
            return None
        response = client.responses.create(
            model=model,
            tools=TOOLS,
            tool_choice="auto" if len(called) < MAX_TOOL_CALLS else "none",
            parallel_tool_calls=False,
            text={"format": {
                "type": "json_schema", "name": "city_explanation", "strict": True,
                "schema": Explanation.model_json_schema(),
            }},
            max_output_tokens=2048,
            timeout=min(REQUEST_TIMEOUT, remaining),
            **request,
        )
        if response.status != "completed":
            return None
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            # Без расчёта сценария пользователя числа ответа относятся к другому набору.
            if not scenario_scored:
                return None
            explanation = Explanation.model_validate_json(response.output_text)
            if unverified_numbers(explanation, results):
                return None
            return AgentExplanation(**explanation.model_dump(), tools_called=called)
        outputs = []
        for call in calls:
            if len(called) < MAX_TOOL_CALLS:
                result = run_tool(call.name, call.arguments)
                called.append(call.name)
                results.append(result)
                if call.name == "score_scenario" and result.get("valid") and _same_set(call.arguments, scenario):
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
    return None


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
    except Exception:
        # Наружу ничего не выпускаем и текст исключения не возвращаем: в нём могут быть служебные данные.
        answer = None
    if answer is None:
        return fallback(data), False
    return answer, True

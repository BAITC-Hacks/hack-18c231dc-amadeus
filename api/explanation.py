"""Факты из движка, структурированное AI-объяснение и локальный запасной ответ."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field

from engine.data import load_city
from engine.simulator import simulate
from engine.validator import validate


load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(description="Итог на русском, два или три предложения.")
    strengths: list[str] = Field(description="Сильные стороны: короткие пункты на русском.")
    risks: list[str] = Field(description="Риски: короткие пункты на русском.")
    consequences: list[str] = Field(description="Последствия и лаги: короткие пункты на русском.")
    recommendations: list[str] = Field(description="Рекомендации: короткие пункты на русском.")


SYSTEM_PROMPT = """Ты аналитик городского развития Астаны. Отвечай на русском.
Объясняй только факты из переданного JSON: все числа уже посчитал сервер.
Используй только числа из данных. Не пересчитывай Score, показатели, стоимость,
вклады, проценты или сроки, не придумывай чисел и не округляй их самостоятельно.
Объясни компромиссы: каким районам помогли, какие не затронуты, самый слабый
район, критические значения до и после, лаги и сработавшие синергии.
Вклад меры — Score полного набора минус Score без этой меры с её синергиями.
Вклады не обязаны складываться в разницу с базой из-за min(D), штрафов и синергий.
Рекомендации о смене мер предлагай ТОЛЬКО из recommendation_options: сервер
проверил каждую отдельную замену по каталогу, бюджету и правилам. Это альтернативы,
не совмещай их. Не добавляй шестую меру и не трать остаток вне правил.
Score альтернатив не рассчитан: предлагай сравнить, не обещай улучшение.
Если вариантов нет, предложи сохранить набор и наблюдать за указанными рисками.
Названия и эффекты мер бери из catalog. Не добавляй внешних фактов о городе.
Верни JSON по схеме: summary — два или три предложения; strengths, risks,
consequences, recommendations — списки коротких пунктов, без Markdown-разметки.
"""


def build_explanation_data(decisions: list[dict], report: dict) -> dict:
    """Вход уже проверен; наборы без одной меры считаются без валидатора."""
    city = load_city()
    measures = {measure["id"]: measure for measure in city["measures"]}
    contributions = []
    for decision in decisions:
        remaining = [item for item in decisions if item["measure_id"] != decision["measure_id"]]
        without = simulate(remaining)
        contributions.append({
            "measure_id": decision["measure_id"],
            "district": decision.get("district"),
            "score_without": without["Score"],
            "contribution": report["Score"] - without["Score"],
        })

    critical = {"before": [], "after": []}
    for district, values in report["districts"].items():
        for code, indicator in values["indicators"].items():
            for stage in critical:
                if indicator[stage] < city["scoring"]["critical_threshold"]:
                    critical[stage].append({
                        "district": district, "indicator": code, "value": indicator[stage],
                    })
    weakest = [name for name, values in report["districts"].items()
               if values["D_after"] == report["D_min"]]
    unchanged = [name for name, values in report["districts"].items()
                 if all(item["delta"] == 0 for item in values["indicators"].values())]

    return {
        "selected_measures": [
            {**measures[item["measure_id"]], "district": item.get("district")}
            for item in decisions
        ],
        **{key: report[key] for key in (
            "Score", "baseline_score", "score_delta", "cost", "remaining_budget",
            "D_avg", "D_min", "N_crit", "districts", "synergies",
        )},
        "critical_values": critical,
        "critical_threshold": city["scoring"]["critical_threshold"],
        "weakest_districts": weakest,
        "unchanged_districts": unchanged,
        "contributions": contributions,
        "contribution_note": "Вклады не складываются в общую разницу: min(D), штрафы и синергии нелинейны.",
        "budget": city["budget"],
        "horizon": city["horizon"],
        "horizon_unit": city["horizon_unit"],
        "indicators": city["indicators"],
        "catalog": city["measures"],
        "rules": city["rules"],
        "incompatibilities": city["incompatibilities"],
        "recommendation_options": replacement_options(decisions, contributions, weakest[0], city),
    }


def replacement_options(decisions: list[dict], contributions: list[dict], district: str, city: dict) -> list[dict]:
    """До трёх допустимых одиночных замен; это варианты для сравнения, не оптимум."""
    selected = {item["measure_id"] for item in decisions}
    costs = {item["id"]: item["cost"] for item in city["measures"]}
    options = []
    for removed in sorted(contributions, key=lambda item: (item["contribution"], item["measure_id"])):
        remaining = [item for item in decisions if item["measure_id"] != removed["measure_id"]]
        for measure in city["measures"]:
            if measure["id"] in selected:
                continue
            replacement = {"measure_id": measure["id"]}
            if measure["type"] == "district":
                replacement["district"] = district
            candidate = remaining + [replacement]
            if validate(candidate):
                continue
            cost = sum(costs[item["measure_id"]] for item in candidate)
            options.append({
                "replace_measure_id": removed["measure_id"],
                **replacement,
                "cost": cost,
                "remaining_budget": city["budget"] - cost,
            })
            if len(options) == 3:
                return options
    return options


def template_explanation(data: dict) -> Explanation:
    """Шаблон использует те же факты, что отправляются модели."""
    best = max(data["contributions"], key=lambda item: item["contribution"])
    names = {item["id"]: item["name"] for item in data["catalog"]}
    best_label = f"{best['measure_id']} — {names[best['measure_id']]}"
    weakest = ", ".join(data["weakest_districts"])
    strengths = []
    risks = [f"Самый слабый район: {weakest}; D = {data['D_min']}."]
    if best["contribution"] > 0:
        strengths.append(f"Наибольший вклад: {best_label}; вклад в Score — {best['contribution']}.")
    else:
        risks.append(f"Даже наибольший вклад не положителен: {best_label}; {best['contribution']}.")
    if data["critical_values"]["after"]:
        risks.extend(
            f"Критическое значение: {item['district']}, {item['indicator']} = {item['value']}."
            for item in data["critical_values"]["after"]
        )
    else:
        strengths.append("После мер критических значений не осталось.")
    if data["unchanged_districts"]:
        risks.append("Без изменений: " + ", ".join(data["unchanged_districts"]) + ".")
    recommendations = [
        f"Сравнить замену {item['replace_measure_id']} на {item['measure_id']} "
        f"({item.get('district', 'весь город')}): стоимость набора {item['cost']}, "
        f"остаток {item['remaining_budget']}; улучшение Score пока не проверено."
        for item in data["recommendation_options"]
    ]
    return Explanation(
        summary=(f"Score сценария — {data['Score']}, базовый — {data['baseline_score']}. "
                 f"Самый слабый район — {weakest}; остаток бюджета — {data['remaining_budget']}."),
        strengths=strengths,
        risks=risks,
        consequences=[
            f"{item['id']} — {item['name']}: лаг {item['lag']} кварталов "
            f"при горизонте {data['horizon']} кварталов."
            for item in data["selected_measures"]
        ] + [data["contribution_note"]],
        recommendations=recommendations or ["Сохранить набор и наблюдать за указанными рисками."],
    )


def explain(data: dict) -> tuple[Explanation, bool]:
    """Ошибка сервиса, отказ или некорректный ответ заменяются локальным шаблоном."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not api_key or not model:
        return template_explanation(data), False
    try:
        with OpenAI(api_key=api_key, timeout=20.0, max_retries=0) as client:
            response = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False, separators=(",", ":"))},
                ],
                text={"format": {
                    "type": "json_schema", "name": "city_explanation", "strict": True,
                    "schema": Explanation.model_json_schema(),
                }},
                max_output_tokens=2048,
                store=False,
            )
        if response.status != "completed":
            return template_explanation(data), False
        return Explanation.model_validate_json(response.output_text), True
    except (OpenAIError, ValueError):
        # Не возвращаем текст исключения: он может содержать служебные данные.
        return template_explanation(data), False

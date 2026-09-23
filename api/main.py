"""FastAPI-обёртка: проверка решений, вызов движка и сравнение с базой."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from api.explanation import build_explanation_data, explain
from engine.data import load_city
from engine.scenario_stats import critical_warnings, scenario_comparison
from engine.simulator import simulate
from engine.validator import validate


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure_id: str
    district: str | None = None


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[Decision]


app = FastAPI(title="Аким на 5 часов", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/config")
def get_config() -> dict:
    """Каталог, правила и исходное состояние для выбора решений."""
    city = load_city()
    return {
        "budget": city["budget"],
        "horizon": city["horizon"],
        "horizon_unit": city["horizon_unit"],
        "districts": city["districts"],
        "indicators": [
            {"id": code, **indicator, "weight": city["weights"][code]}
            for code, indicator in city["indicators"].items()
        ],
        "measures": city["measures"],
        "synergies": city["synergies"],
        "incompatibilities": city["incompatibilities"],
        "rules": city["rules"],
        "scoring": city["scoring"],
        "baseline_score": simulate([])["Score"],
    }


@app.post("/api/simulate")
def simulate_scenario(request: SimulationRequest) -> dict:
    """Невалидный набор возвращает HTTP 200 с причинами без запуска расчёта."""
    # API принимает null; движок требует отсутствие поля у городской меры.
    # У районной меры отсутствие поля по-прежнему отклоняет валидатор.
    decisions = [decision.model_dump(exclude_none=True) for decision in request.decisions]
    errors = validate(decisions)
    if errors:
        return {"valid": False, "errors": errors}

    city = load_city()
    result = simulate(decisions)
    baseline = simulate([])
    selected = {decision["measure_id"]: decision for decision in decisions}

    # Здесь только сведения о выбранных парах; их эффекты уже учтены движком.
    synergies = [
        {**synergy, "district": selected[synergy["target_measure"]]["district"]}
        for synergy in city["synergies"]
        if all(measure_id in selected for measure_id in synergy["measures"])
    ]
    districts = {
        name: {
            "D_before": baseline["D"][name],
            "D_after": result["D"][name],
            "indicators": {
                code: {
                    "before": baseline["indicators"][name][code],
                    "after": value,
                    "delta": value - baseline["indicators"][name][code],
                }
                for code, value in values.items()
            },
        }
        for name, values in result["indicators"].items()
    }
    return {
        "valid": True,
        "cost": result["cost"],
        "remaining_budget": city["budget"] - result["cost"],
        "Score": result["Score"],
        "baseline_score": baseline["Score"],
        "score_delta": result["Score"] - baseline["Score"],
        "D_avg": result["D_avg"],
        "D_min": result["D_min"],
        "N_crit": result["N_crit"],
        "synergies": synergies,
        "districts": districts,
        **scenario_comparison(result["Score"], city),
        "warnings": critical_warnings(decisions, baseline, result, city),
    }


@app.post("/api/explain")
def explain_scenario(request: SimulationRequest) -> dict:
    """Объяснить проверенный сервером сценарий; клиент не передаёт числа."""
    report = simulate_scenario(request)
    if not report["valid"]:
        return report
    decisions = [decision.model_dump(exclude_none=True) for decision in request.decisions]
    data = build_explanation_data(decisions, report)
    explanation, ai_generated = explain(data)
    return {
        "valid": True,
        "ai_generated": ai_generated,
        **explanation.model_dump(),
        "contributions": data["contributions"],
        **{key: data[key] for key in (
            "best_possible_score", "gap_to_best", "percentile", "warnings",
        )},
    }

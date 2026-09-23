import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Database,
  MapPinned,
  Play,
  Plus,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import city from "../data/city.json";
import "./styles.css";


const measureById = Object.fromEntries(city.measures.map((measure) => [measure.id, measure]));
const districtNames = Object.keys(city.districts);
const directions = ["Все", ...new Set(city.measures.map((measure) => measure.direction))];
const emptyDecision = () => ({ measure_id: "", district: "" });


function makeInitialDecisions() {
  const example = city.reference_scenarios.example.decisions;
  return Array.from({ length: city.rules.decision_count }, (_, index) => ({
    ...emptyDecision(),
    ...(example[index] ?? {}),
  }));
}


function clip(value) {
  return Math.min(city.scoring.maximum, Math.max(city.scoring.minimum, value));
}


function simulate(decisions) {
  const indicators = Object.fromEntries(
    Object.entries(city.districts).map(([district, data]) => [
      district,
      { ...data.indicators },
    ]),
  );
  const selected = {};
  let cost = 0;

  [...decisions]
    .filter((decision) => measureById[decision.measure_id])
    .sort((first, second) => first.measure_id.localeCompare(second.measure_id))
    .forEach((decision) => {
      const measure = measureById[decision.measure_id];
      selected[measure.id] = decision;
      cost += measure.cost;

      const fraction = (city.horizon - measure.lag) / city.horizon;
      const targets = measure.type === "city" ? districtNames : [decision.district];
      targets
        .filter((district) => indicators[district])
        .forEach((district) => {
          Object.entries(measure.effects).forEach(([indicator, effect]) => {
            indicators[district][indicator] += effect * fraction;
          });
        });
    });

  city.synergies.forEach((synergy) => {
    if (!synergy.measures.every((measureId) => selected[measureId])) {
      return;
    }
    const district = selected[synergy.target_measure]?.district;
    if (!indicators[district]) {
      return;
    }
    Object.entries(synergy.effects).forEach(([indicator, bonus]) => {
      indicators[district][indicator] += bonus;
    });
  });

  Object.values(indicators).forEach((values) => {
    Object.entries(values).forEach(([indicator, value]) => {
      values[indicator] = clip(value);
    });
  });

  const districtScores = Object.fromEntries(
    Object.entries(indicators).map(([district, values]) => [
      district,
      Object.entries(values).reduce(
        (sum, [indicator, value]) => sum + city.weights[indicator] * value,
        0,
      ),
    ]),
  );
  const average = Object.entries(districtScores).reduce(
    (sum, [district, score]) => sum + city.districts[district].population_share * score,
    0,
  );
  const minimum = Math.min(...Object.values(districtScores));
  const critical = Object.values(indicators).reduce(
    (count, values) => count + Object.values(values).filter(
      (value) => value < city.scoring.critical_threshold,
    ).length,
    0,
  );
  const score = (
    city.scoring.average_weight * average
    + city.scoring.minimum_weight * minimum
    - city.scoring.critical_penalty * critical
  );

  return {
    indicators,
    D: districtScores,
    D_avg: average,
    D_min: minimum,
    N_crit: critical,
    Score: score,
    cost,
  };
}


function validate(decisions) {
  const filled = decisions.filter((decision) => decision.measure_id);
  const errors = [];

  if (filled.length !== city.rules.decision_count) {
    errors.push(`Нужно выбрать ровно ${city.rules.decision_count} мер; выбрано ${filled.length}.`);
  }

  const seen = new Set();
  const directionsCount = {};
  let cost = 0;

  filled.forEach((decision, index) => {
    const measure = measureById[decision.measure_id];
    if (!measure) {
      errors.push(`Решение #${index + 1}: неизвестная мера.`);
      return;
    }

    if (seen.has(measure.id)) {
      errors.push(`Мера ${measure.id} выбрана повторно.`);
    }
    seen.add(measure.id);
    directionsCount[measure.direction] = (directionsCount[measure.direction] ?? 0) + 1;
    cost += measure.cost;

    if (measure.type === "district" && !city.districts[decision.district]) {
      errors.push(`Для меры ${measure.id} нужен район.`);
    }
    if (measure.type === "city" && decision.district) {
      errors.push(`Для городской меры ${measure.id} район не указывается.`);
    }
  });

  if (cost > city.budget) {
    errors.push(`Стоимость ${cost} превышает бюджет ${city.budget}.`);
  }

  Object.entries(directionsCount).forEach(([direction, count]) => {
    if (count > city.rules.max_per_direction) {
      errors.push(`В направлении "${direction}" выбрано ${count} мер.`);
    }
  });

  city.incompatibilities.forEach((conflict) => {
    const [first, second] = conflict.measures;
    const firstDecisions = filled.filter((decision) => decision.measure_id === first);
    const secondDecisions = filled.filter((decision) => decision.measure_id === second);
    if (!firstDecisions.length || !secondDecisions.length) {
      return;
    }
    if (conflict.scope === "global") {
      errors.push(`Меры ${first} и ${second} несовместимы.`);
      return;
    }
    districtNames.forEach((district) => {
      const hasFirst = firstDecisions.some((decision) => decision.district === district);
      const hasSecond = secondDecisions.some((decision) => decision.district === district);
      if (hasFirst && hasSecond) {
        errors.push(`Меры ${first} и ${second} несовместимы в районе "${district}".`);
      }
    });
  });

  return errors;
}


function formatNumber(value, digits = 1) {
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}


function App() {
  const [decisions, setDecisions] = useState(makeInitialDecisions);
  const [activeDirection, setActiveDirection] = useState("Все");
  const [activeDistrict, setActiveDistrict] = useState(districtNames[0]);

  const result = useMemo(() => simulate(decisions), [decisions]);
  const errors = useMemo(() => validate(decisions), [decisions]);
  const selectedIds = decisions.map((decision) => decision.measure_id).filter(Boolean);
  const filteredMeasures = city.measures.filter(
    (measure) => activeDirection === "Все" || measure.direction === activeDirection,
  );

  function updateDecision(index, patch) {
    setDecisions((current) => current.map((decision, itemIndex) => {
      if (itemIndex !== index) {
        return decision;
      }
      return { ...decision, ...patch };
    }));
  }

  function selectMeasure(index, measureId) {
    const measure = measureById[measureId];
    updateDecision(index, {
      measure_id: measureId,
      district: measure?.type === "district" ? (decisions[index].district || activeDistrict) : "",
    });
  }

  function addMeasure(measure) {
    const emptyIndex = decisions.findIndex((decision) => !decision.measure_id);
    const targetIndex = emptyIndex === -1 ? decisions.length - 1 : emptyIndex;
    updateDecision(targetIndex, {
      measure_id: measure.id,
      district: measure.type === "district" ? activeDistrict : "",
    });
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Astana Quality of Life Score</p>
          <h1>Аким на 5 часов</h1>
        </div>
        <div className="topbar-actions">
          <button className="ghost-button" type="button" onClick={() => setDecisions(makeInitialDecisions())}>
            <Sparkles size={18} />
            Пример
          </button>
          <button className="ghost-button" type="button" onClick={() => setDecisions(Array.from({ length: city.rules.decision_count }, emptyDecision))}>
            <RotateCcw size={18} />
            Сброс
          </button>
        </div>
      </header>

      <section className="kpi-grid" aria-label="Итоги сценария">
        <MetricCard label="Score" value={formatNumber(result.Score, 2)} accent="strong" />
        <MetricCard label="Бюджет" value={`${result.cost}/${city.budget}`} accent={result.cost > city.budget ? "bad" : "ok"} />
        <MetricCard label="Средний D" value={formatNumber(result.D_avg, 2)} />
        <MetricCard label="Мин. район" value={formatNumber(result.D_min, 2)} />
        <MetricCard label="Критичные" value={result.N_crit} accent={result.N_crit ? "bad" : "ok"} />
      </section>

      <section className="workspace-grid">
        <section className="panel decision-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Сценарий</p>
              <h2>5 решений</h2>
            </div>
            <StatusPill errors={errors} />
          </div>

          <div className="decision-list">
            {decisions.map((decision, index) => {
              const measure = measureById[decision.measure_id];
              return (
                <article className="decision-row" key={index}>
                  <div className="row-number">{index + 1}</div>
                  <div className="decision-fields">
                    <label>
                      <span>Мера</span>
                      <select
                        value={decision.measure_id}
                        onChange={(event) => selectMeasure(index, event.target.value)}
                      >
                        <option value="">Выберите меру</option>
                        {city.measures.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.id} · {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>Район</span>
                      <select
                        disabled={!measure || measure.type === "city"}
                        value={measure?.type === "district" ? decision.district : ""}
                        onChange={(event) => updateDecision(index, { district: event.target.value })}
                      >
                        <option value="">Город</option>
                        {districtNames.map((district) => (
                          <option key={district} value={district}>
                            {district}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="decision-meta">
                    <span>{measure ? `${measure.cost} ед.` : "0 ед."}</span>
                    <button className="icon-button" type="button" onClick={() => updateDecision(index, emptyDecision())} aria-label="Очистить">
                      <X size={17} />
                    </button>
                  </div>
                </article>
              );
            })}
          </div>

          <div className="error-box" data-empty={!errors.length}>
            {errors.length ? (
              errors.map((error) => (
                <div className="error-line" key={error}>
                  <AlertCircle size={16} />
                  {error}
                </div>
              ))
            ) : (
              <div className="error-line ok">
                <CheckCircle2 size={16} />
                Сценарий валиден
              </div>
            )}
          </div>
        </section>

        <section className="panel map-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Районы</p>
              <h2>Баланс качества</h2>
            </div>
            <MapPinned size={22} />
          </div>
          <CityMap scores={result.D} activeDistrict={activeDistrict} setActiveDistrict={setActiveDistrict} />
          <DistrictDetails
            district={activeDistrict}
            result={result}
          />
        </section>
      </section>

      <section className="panel catalog-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Каталог</p>
            <h2>Меры развития</h2>
          </div>
          <Database size={22} />
        </div>

        <div className="segmented-control" aria-label="Фильтр направления">
          {directions.map((direction) => (
            <button
              className={direction === activeDirection ? "active" : ""}
              key={direction}
              type="button"
              onClick={() => setActiveDirection(direction)}
            >
              {direction}
            </button>
          ))}
        </div>

        <div className="measure-grid">
          {filteredMeasures.map((measure) => {
            const selected = selectedIds.includes(measure.id);
            return (
              <article className="measure-card" key={measure.id}>
                <div className="measure-card-top">
                  <div>
                    <span className="measure-id">{measure.id}</span>
                    <h3>{measure.name}</h3>
                  </div>
                  <button
                    className="icon-button add-button"
                    type="button"
                    onClick={() => addMeasure(measure)}
                    disabled={selected}
                    aria-label="Добавить меру"
                  >
                    <Plus size={18} />
                  </button>
                </div>
                <div className="measure-facts">
                  <span>{measure.direction}</span>
                  <span>{measure.type === "city" ? "Город" : "Район"}</span>
                  <span>{measure.cost} ед.</span>
                  <span>L{measure.lag}</span>
                </div>
                <div className="effects">
                  {Object.entries(measure.effects).map(([indicator, effect]) => (
                    <span className={effect < 0 ? "negative" : ""} key={indicator}>
                      {indicator} {effect > 0 ? "+" : ""}{effect}
                    </span>
                  ))}
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </main>
  );
}


function MetricCard({ label, value, accent = "neutral" }) {
  return (
    <article className={`metric-card ${accent}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}


function StatusPill({ errors }) {
  if (errors.length) {
    return (
      <span className="status-pill bad">
        <AlertCircle size={16} />
        {errors.length}
      </span>
    );
  }
  return (
    <span className="status-pill ok">
      <CheckCircle2 size={16} />
      OK
    </span>
  );
}


function CityMap({ scores, activeDistrict, setActiveDistrict }) {
  const shapes = [
    { district: districtNames[0], x: 14, y: 16, width: 32, height: 28 },
    { district: districtNames[1], x: 47, y: 18, width: 38, height: 27 },
    { district: districtNames[2], x: 19, y: 48, width: 33, height: 31 },
    { district: districtNames[3], x: 55, y: 51, width: 31, height: 28 },
    { district: districtNames[4], x: 36, y: 32, width: 31, height: 31 },
  ];

  return (
    <svg className="city-map" viewBox="0 0 100 92" role="img" aria-label="Схема районов">
      <rect className="map-background" x="4" y="4" width="92" height="84" rx="8" />
      {shapes.map((shape) => {
        const active = shape.district === activeDistrict;
        const score = scores[shape.district];
        return (
          <g
            className={`district-shape ${active ? "active" : ""}`}
            key={shape.district}
            onClick={() => setActiveDistrict(shape.district)}
            tabIndex="0"
            role="button"
          >
            <rect
              x={shape.x}
              y={shape.y}
              width={shape.width}
              height={shape.height}
              rx="5"
            />
            <text x={shape.x + 5} y={shape.y + 13}>{shape.district}</text>
            <text className="score-label" x={shape.x + 5} y={shape.y + 24}>
              {formatNumber(score, 1)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}


function DistrictDetails({ district, result }) {
  const indicators = result.indicators[district];
  const lowest = Object.entries(indicators)
    .sort((first, second) => first[1] - second[1])
    .slice(0, 4);

  return (
    <div className="district-details">
      <div className="district-score">
        <span>{district}</span>
        <strong>{formatNumber(result.D[district], 2)}</strong>
      </div>
      <div className="indicator-bars">
        {lowest.map(([indicator, value]) => (
          <div className="indicator-bar" key={indicator}>
            <div className="bar-label">
              <span>{indicator}</span>
              <span>{formatNumber(value, 1)}</span>
            </div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${value}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}


export default App;

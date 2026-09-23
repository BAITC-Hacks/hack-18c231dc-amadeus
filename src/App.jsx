import {
  Bot,
  CheckCircle2,
  Database,
  Plus,
  RotateCcw,
  ShieldCheck,
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


function indicatorTitle(code) {
  return city.indicators[code]?.name ?? code;
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
  const analysis = useMemo(() => buildLocalAnalysis(decisions, result), [decisions, result]);

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
        <div className="brand">
          <strong>Аким на 5 часов</strong>
          <span>AI-симулятор управления городом</span>
        </div>
        <div className="topbar-actions">
          <button className="dark-button" type="button" onClick={() => setDecisions(makeInitialDecisions())}>
            <Sparkles size={14} />
            Пример
          </button>
          <button className="dark-button" type="button" onClick={() => setDecisions(Array.from({ length: city.rules.decision_count }, emptyDecision))}>
            <RotateCcw size={14} />
            Сброс
          </button>
        </div>
      </header>

      <section className="kpi-grid" aria-label="Итоги сценария">
        <MetricCard label="Score" value={formatNumber(result.Score, 2)} accent="strong" />
        <MetricCard label="Бюджет" value={`${result.cost} / ${city.budget}`} />
        <MetricCard label="Средний D" value={formatNumber(result.D_avg, 2)} />
        <MetricCard label="Мин. район" value={formatNumber(result.D_min, 2)} />
        <MetricCard label="Критичные" value={result.N_crit} />
      </section>

      <section className="workspace-grid">
        <section className="panel decision-panel">
          <div className="panel-heading compact">
            <h2>Сценарий · 5 решений</h2>
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
                  <span className="cost-chip">{measure ? `${measure.cost} ед.` : "0 ед."}</span>
                  <button className="icon-button" type="button" onClick={() => updateDecision(index, emptyDecision())} aria-label="Очистить">
                    <X size={15} />
                  </button>
                </article>
              );
            })}
          </div>

          <div className="validation-box" data-empty={!errors.length}>
            {errors.length ? (
              errors.map((error) => (
                <div className="validation-line" key={error}>
                  <ShieldCheck size={15} />
                  {error}
                </div>
              ))
            ) : (
              <div className="validation-line ok">
                <ShieldCheck size={15} />
                Сценарий валиден. Все 5 решений распределены корректно в рамках лимитов бюджета.
              </div>
            )}
          </div>
        </section>

        <section className="panel district-panel">
          <div className="panel-heading compact">
            <h2>Районы · баланс качества</h2>
          </div>
          <DistrictGrid
            scores={result.D}
            activeDistrict={activeDistrict}
            setActiveDistrict={setActiveDistrict}
          />
          <DistrictDetails district={activeDistrict} result={result} />
        </section>
      </section>

      <section className="panel catalog-panel">
        <div className="panel-heading compact">
          <h2>Каталог · меры развития</h2>
          <Database size={19} />
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
                  <span className="measure-id">{measure.id}</span>
                  <button
                    className="icon-button add-button"
                    type="button"
                    onClick={() => addMeasure(measure)}
                    disabled={selected}
                    aria-label="Добавить меру"
                  >
                    <Plus size={17} />
                  </button>
                </div>
                <h3>{measure.name}</h3>
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

      <AnalysisPanel analysis={analysis} />
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
        <X size={13} />
        {errors.length}
      </span>
    );
  }
  return (
    <span className="status-pill ok">
      <CheckCircle2 size={13} />
      OK
    </span>
  );
}


function DistrictGrid({ scores, activeDistrict, setActiveDistrict }) {
  return (
    <div className="district-grid">
      {districtNames.map((district) => (
        <button
          className={`district-card ${district === activeDistrict ? "active" : ""}`}
          key={district}
          type="button"
          onClick={() => setActiveDistrict(district)}
        >
          <span>{district}</span>
          <strong>{formatNumber(scores[district], 1)}</strong>
        </button>
      ))}
    </div>
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
        <h3>Детали района: {district}</h3>
        <strong>{formatNumber(result.D[district], 2)}</strong>
      </div>
      <div className="indicator-bars">
        {lowest.map(([indicator, value], index) => (
          <div className="indicator-bar" key={indicator}>
            <div className="bar-label">
              <span>{indicator} · {indicatorTitle(indicator)}</span>
              <span>{formatNumber(value, 1)}</span>
            </div>
            <div className="bar-track">
              <div className={`bar-fill tone-${index}`} style={{ width: `${value}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}


function buildLocalAnalysis(decisions, result) {
  const selectedMeasures = decisions
    .filter((decision) => measureById[decision.measure_id])
    .map((decision) => ({ ...measureById[decision.measure_id], district: decision.district }));
  const weakestDistrict = Object.entries(result.D)
    .sort((first, second) => first[1] - second[1])[0]?.[0];
  const strongestDirection = selectedMeasures.reduce((counts, measure) => {
    counts[measure.direction] = (counts[measure.direction] ?? 0) + 1;
    return counts;
  }, {});
  const dominantDirection = Object.entries(strongestDirection)
    .sort((first, second) => second[1] - first[1])[0]?.[0];
  const slowMeasures = selectedMeasures
    .filter((measure) => measure.lag >= 3)
    .map((measure) => measure.id);

  return {
    source: "Local Analysis",
    strengths: [
      `Score сценария: ${formatNumber(result.Score, 2)} при бюджете ${result.cost}/${city.budget}.`,
      dominantDirection
        ? `Основной фокус набора: ${dominantDirection}; лимит по направлениям контролируется валидатором.`
        : "Каталог мер готов для выбора управленческого набора.",
    ],
    risks: [
      `Самый слабый район сейчас: ${weakestDistrict}; D = ${formatNumber(result.D[weakestDistrict], 2)}.`,
      slowMeasures.length
        ? `Меры ${slowMeasures.join(", ")} имеют лаг 3+ квартала, поэтому первый эффект будет не мгновенным.`
        : "В наборе нет мер с высоким лагом реализации.",
    ],
    recommendations: [
      "Сравнить набор с альтернативами по слабому району и критичным показателям.",
      result.N_crit
        ? "Сначала закрыть показатели ниже критического порога 40."
        : "Сохранить баланс: критичных показателей после выбранных мер нет.",
    ],
  };
}


function AnalysisPanel({ analysis }) {
  return (
    <section className="panel analysis-panel">
      <div className="panel-heading compact">
        <h2>AI-анализ сценария</h2>
        <span className="source-badge">
          <Bot size={14} />
          {analysis.source}
        </span>
      </div>
      <div className="analysis-grid">
        <AnalysisColumn title="Сильные стороны" tone="green" items={analysis.strengths} />
        <AnalysisColumn title="Риски" tone="amber" items={analysis.risks} />
        <AnalysisColumn title="Рекомендации" tone="blue" items={analysis.recommendations} />
      </div>
    </section>
  );
}


function AnalysisColumn({ title, tone, items }) {
  return (
    <article className="analysis-column">
      <h3 className={tone}>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </article>
  );
}


export default App;

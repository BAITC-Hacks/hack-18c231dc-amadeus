import { AlertCircle, Bot, CheckCircle2, Database, Play, Plus, RotateCcw, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { explainScenario, getConfig, serializeDecisions, simulateScenario } from "./api.js";
import "./styles.css";

const emptyDecision = () => ({ measure_id: "", district: "" });
const EXAMPLE = [
  { measure_id: "M7", district: "Нура" },
  { measure_id: "M8", district: "Нура" },
  { measure_id: "M10", district: "Нура" },
  { measure_id: "M12", district: null },
  { measure_id: "M5", district: "Сарыарка" },
];
const explanationSections = [
  ["strengths", "Сильные стороны"], ["risks", "Риски"],
  ["consequences", "Последствия"], ["recommendations", "Рекомендации"],
];

function formatNumber(value, digits = 1) {
  if (value === undefined || value === null) return "—";
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits, minimumFractionDigits: digits,
  }).format(value);
}

function App() {
  const [city, setCity] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    getConfig(controller.signal).then((config) => {
      if (!controller.signal.aborted) setCity(config);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [attempt]);

  if (!city) return (
    <main className="app-shell"><section className="panel connection-panel" aria-live="polite">
      <h1>Аким на 5 часов</h1>
      {error ? <><p role="alert">{error}</p><button className="dark-button" onClick={() => setAttempt(attempt + 1)}>Повторить загрузку</button></>
        : <p role="status">Загружаем районы и каталог мер…</p>}
    </section></main>
  );
  return <Simulator city={city} />;
}

function Simulator({ city }) {
  const districtNames = Object.keys(city.districts);
  const measureById = Object.fromEntries(city.measures.map((measure) => [measure.id, measure]));
  const indicatorById = Object.fromEntries(city.indicators.map((indicator) => [indicator.id, indicator]));
  const directions = ["Все", ...new Set(city.measures.map((measure) => measure.direction))];
  const [decisions, setDecisions] = useState(() => Array.from({ length: city.rules.decision_count }, emptyDecision));
  const [activeDirection, setActiveDirection] = useState("Все");
  const [activeDistrict, setActiveDistrict] = useState(districtNames[0]);
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState([]);
  const [status, setStatus] = useState("idle");
  const [explanation, setExplanation] = useState(null);
  const [explanationStatus, setExplanationStatus] = useState("idle");
  const [explanationError, setExplanationError] = useState("");
  const activeRequest = useRef(null);
  useEffect(() => () => activeRequest.current?.abort(), []);

  const selectedIds = decisions.map((decision) => decision.measure_id).filter(Boolean);
  const cost = decisions.reduce((sum, item) => sum + (measureById[item.measure_id]?.cost ?? 0), 0);
  const busy = status === "loading" || explanationStatus === "loading";
  const filteredMeasures = city.measures.filter((measure) => activeDirection === "Все" || measure.direction === activeDirection);
  const scores = Object.fromEntries(districtNames.map((name) => [name, result?.districts[name].D_after ?? city.districts[name].reference_D]));

  function changeDecisions(next) {
    activeRequest.current?.abort();
    setDecisions(next);
    setResult(null);
    setErrors([]);
    setStatus("idle");
    setExplanation(null);
    setExplanationStatus("idle");
    setExplanationError("");
  }

  function updateDecision(index, patch) {
    changeDecisions(decisions.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  function selectMeasure(index, measureId) {
    const measure = measureById[measureId];
    updateDecision(index, {
      measure_id: measureId,
      district: measure?.type === "district" ? (decisions[index].district || activeDistrict) : null,
    });
  }

  function addMeasure(measure) {
    const index = decisions.findIndex((item) => !item.measure_id);
    if (index === -1) return;
    updateDecision(index, { measure_id: measure.id, district: measure.type === "district" ? activeDistrict : null });
  }

  async function calculate() {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setStatus("loading");
    setResult(null);
    setErrors([]);
    setExplanation(null);
    setExplanationStatus("idle");
    setExplanationError("");
    try {
      const data = await simulateScenario(serializeDecisions(decisions, city.measures), controller.signal);
      if (controller.signal.aborted) return;
      if (!data.valid) {
        setErrors(data.errors);
        setStatus("invalid");
      } else {
        setResult(data);
        setStatus("valid");
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setErrors([error.message]);
      setStatus("error");
    }
  }

  async function requestExplanation() {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setExplanationStatus("loading");
    setExplanationError("");
    setExplanation(null);
    try {
      const data = await explainScenario(serializeDecisions(decisions, city.measures), controller.signal);
      if (controller.signal.aborted) return;
      if (!data.valid) {
        setErrors(data.errors);
        setResult(null);
        setStatus("invalid");
        setExplanationStatus("idle");
      } else {
        setExplanation(data);
        setExplanationStatus("done");
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setExplanationError(error.message);
      setExplanationStatus("error");
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><strong>Аким на 5 часов</strong><span>AI-симулятор управления городом</span></div>
        <div className="topbar-actions">
          <button className="dark-button" type="button" onClick={() => changeDecisions(EXAMPLE.map((item) => ({ ...item })))}><Sparkles size={18} />Пример</button>
          <button className="dark-button" type="button" onClick={() => changeDecisions(Array.from({ length: city.rules.decision_count }, emptyDecision))}><RotateCcw size={18} />Сброс</button>
        </div>
      </header>
      <p className="scenario-note">Базовый Score: {formatNumber(city.baseline_score, 2)}. {result ? `Изменение: ${result.score_delta > 0 ? "+" : ""}${formatNumber(result.score_delta, 2)}. Остаток бюджета: ${result.remaining_budget}.` : "Выберите меры и нажмите «Рассчитать»."}</p>
      <section className="kpi-grid" aria-label="Итоги сценария" aria-live="polite">
        <MetricCard label="Score" value={formatNumber(result?.Score, 2)} accent="strong" />
        <MetricCard label="Бюджет" value={`${result?.cost ?? cost}/${city.budget}`} accent={cost > city.budget ? "bad" : "ok"} />
        <MetricCard label="Средний D" value={formatNumber(result?.D_avg, 2)} />
        <MetricCard label="Мин. район" value={formatNumber(result?.D_min, 2)} />
        <MetricCard label="Критичные" value={result?.N_crit ?? "—"} accent={result?.N_crit ? "bad" : "neutral"} />
      </section>

      <section className="workspace-grid">
        <section className="panel decision-panel">
          <div className="panel-heading compact"><h2>Сценарий · {city.rules.decision_count} решений</h2><StatusPill status={status} /></div>
          <div className="decision-list">
            {decisions.map((decision, index) => {
              const measure = measureById[decision.measure_id];
              return (
                <article className="decision-row" key={index}>
                  <div className="row-number">{index + 1}</div>
                  <div className="decision-fields">
                    <label><span>Мера {index + 1}</span><select value={decision.measure_id} onChange={(event) => selectMeasure(index, event.target.value)}>
                      <option value="">Выберите меру</option>
                      {city.measures.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.name}</option>)}
                    </select></label>
                    <label><span>Район {index + 1}</span><select disabled={!measure || measure.type === "city"} value={decision.district || ""} onChange={(event) => updateDecision(index, { district: event.target.value })}>
                      <option value="">{measure?.type === "district" ? "Выберите район" : "Город"}</option>
                      {districtNames.map((district) => <option key={district} value={district}>{district}</option>)}
                    </select></label>
                  </div>
                  <span className="cost-chip">{measure?.cost ?? 0} ед.</span><button className="icon-button" type="button" onClick={() => updateDecision(index, emptyDecision())} aria-label={`Очистить решение ${index + 1}`}><X size={15} /></button>
                </article>
              );
            })}
          </div>
          <div className="scenario-actions"><button className="dark-button primary-button" type="button" disabled={busy} onClick={calculate}><Play size={18} />{status === "loading" ? "Рассчитываем…" : "Рассчитать"}</button></div>
          <div aria-live="polite">
            {errors.length > 0 && <div className="validation-box" role="alert">{errors.map((error, index) => <div className="validation-line" key={index}><AlertCircle size={16} />{error}</div>)}</div>}
            {status === "valid" && <p className="validation-line ok"><CheckCircle2 size={16} />Сценарий валиден</p>}
          </div>
        </section>

        <section className="panel district-panel">
          <div className="panel-heading compact"><h2>Районы · баланс качества</h2></div><p className="scenario-note">{result ? "После решений" : "Исходные показатели"}</p>
          <DistrictGrid districtNames={districtNames} scores={scores} activeDistrict={activeDistrict} setActiveDistrict={setActiveDistrict} />
          <DistrictDetails district={activeDistrict} initial={city.districts[activeDistrict]} report={result?.districts[activeDistrict]} indicatorById={indicatorById} />
        </section>
      </section>

      <section className="panel catalog-panel">
        <div className="panel-heading compact"><h2>Каталог · меры развития</h2><Database size={19} /></div>
        <div className="segmented-control" aria-label="Фильтр направления">{directions.map((direction) => <button className={direction === activeDirection ? "active" : ""} key={direction} type="button" onClick={() => setActiveDirection(direction)}>{direction}</button>)}</div>
        <div className="measure-grid">{filteredMeasures.map((measure) => (
          <article className="measure-card" key={measure.id}>
            <div className="measure-card-top"><span className="measure-id">{measure.id}</span>
              <button className="icon-button add-button" type="button" onClick={() => addMeasure(measure)} disabled={selectedIds.includes(measure.id) || !decisions.some((item) => !item.measure_id)} aria-label={`Добавить ${measure.id}`}><Plus size={18} /></button>
            </div>
            <h3>{measure.name}</h3>
            <div className="measure-facts"><span>{measure.direction}</span><span>{measure.type === "city" ? "Город" : "Район"}</span><span>{measure.cost} ед.</span><span>Лаг: {measure.lag} кв.</span></div>
            <div className="effects">{Object.entries(measure.effects).map(([indicator, effect]) => <span className={effect < 0 ? "negative" : ""} key={indicator} title={indicatorById[indicator]?.name}>{indicator} {effect > 0 ? "+" : ""}{effect}</span>)}</div>
          </article>
        ))}</div>
      </section>

      <section className="panel analysis-panel explanation-panel" aria-busy={explanationStatus === "loading"}>
        <div className="panel-heading compact"><h2>Объяснение результата</h2>
          <button className="dark-button" type="button" disabled={!result || busy} onClick={requestExplanation}><Sparkles size={18} />{explanationStatus === "loading" ? "Готовим объяснение…" : "Объяснить результат"}</button>
        </div>
        {!result && <p>Сначала рассчитайте валидный набор решений.</p>}
        {result && !explanation && explanationStatus === "idle" && <p>Получите разбор сильных сторон, рисков и возможных последствий.</p>}
        {explanationError && <p className="validation-line" role="alert">{explanationError}</p>}
        <div aria-live="polite">
          {explanation && <>
            <span className={`source-badge explanation-badge ${explanation.ai_generated ? "" : "template"}`}><Bot size={15} />{explanation.ai_generated ? "AI-объяснение" : "Шаблонное объяснение — AI недоступен"}</span>
            <p>{explanation.summary}</p>
            <div className="analysis-grid explanation-grid">{explanationSections.map(([key, title]) => <section className="analysis-column" key={key}><h3 className={key === "risks" ? "amber" : key === "strengths" ? "green" : "blue"}>{title}</h3>{explanation[key].length ? <ul>{explanation[key].map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>Нет дополнительных замечаний.</p>}</section>)}</div>
          </>}
        </div>
      </section>

    </main>
  );
}

function MetricCard({ label, value, accent = "neutral" }) {
  return <article className={`metric-card ${accent}`}><span>{label}</span><strong>{value}</strong></article>;
}

function StatusPill({ status }) {
  const labels = { idle: "Не рассчитан", loading: "Расчёт…", valid: "Валиден", invalid: "Есть ошибки", error: "Нет ответа" };
  return <span className={`status-pill ${status === "valid" ? "ok" : ["invalid", "error"].includes(status) ? "bad" : ""}`}>{labels[status]}</span>;
}

function DistrictGrid({ districtNames, scores, activeDistrict, setActiveDistrict }) {
  return (
    <div className="district-grid">
      {districtNames.map((district) => (
        <button
          className={`district-card ${district === activeDistrict ? "active" : ""}`}
          key={district}
          type="button"
          aria-pressed={district === activeDistrict}
          onClick={() => setActiveDistrict(district)}
        >
          <span>{district}</span>
          <strong>{formatNumber(scores[district], 1)}</strong>
        </button>
      ))}
    </div>
  );
}

function DistrictDetails({ district, initial, report, indicatorById }) {
  const indicators = Object.entries(initial.indicators).map(([code, value]) => [
    code, report?.indicators[code].after ?? value,
  ]);
  const lowest = indicators.sort((first, second) => first[1] - second[1]).slice(0, 4);
  return <div className="district-details">
    <div className="district-score"><h3>Детали района: {district}</h3><strong>{report ? `${formatNumber(report.D_before, 2)} → ${formatNumber(report.D_after, 2)}` : formatNumber(initial.reference_D, 2)}</strong></div>
    <div className="indicator-bars">
      {lowest.map(([code, value], index) => (
        <div className="indicator-bar" key={code}>
          <div className="bar-label"><span>{code} · {indicatorById[code]?.name ?? code}</span><span>{formatNumber(value, 1)}</span></div>
          <div className="bar-track"><div className={`bar-fill tone-${index}`} style={{ width: `${value}%` }} /></div>
        </div>
      ))}
    </div>
    <details className="indicator-details"><summary>Все показатели и изменения</summary>
    <div className="indicator-table-wrap"><table className="indicator-table"><caption>{report ? "Изменения показателей" : "Исходные значения"}</caption>
      <thead><tr><th>Показатель</th><th>До</th><th>После</th><th>Δ</th></tr></thead>
      <tbody>{Object.entries(initial.indicators).map(([code, before]) => {
        const item = report?.indicators[code];
        return <tr key={code}><th scope="row">{indicatorById[code]?.name ?? code}</th><td>{formatNumber(item?.before ?? before)}</td><td>{formatNumber(item?.after)}</td><td>{item && item.delta > 0 ? "+" : ""}{formatNumber(item?.delta)}</td></tr>;
      })}</tbody>
    </table></div>
    </details>
  </div>;
}

export default App;

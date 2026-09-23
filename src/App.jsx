import {
  AlertCircle,
  AlertTriangle,
  BarChart3,
  Bot,
  CheckCircle2,
  Clock3,
  Download,
  Info,
  Landmark,
  Play,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

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
  ["strengths", "Прогноз"],
  ["risks", "Риски"],
  ["recommendations", "Рекомендации"],
];
const sparklineSets = {
  score: [38, 46, 54, 62, 70],
  budget: [28, 44, 63, 82, 95],
  districts: [45, 53, 58, 64, 72],
  min: [62, 56, 51, 47, 53],
  critical: [70, 52, 44, 32, 18],
};

function formatNumber(value, digits = 1) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}

function formatDelta(value, digits = 1) {
  if (value === undefined || value === null) return "—";
  return `${value > 0 ? "+" : ""}${formatNumber(value, digits)}`;
}

function costLabel(value) {
  return `${value ?? 0} ед.`;
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

  if (!city) {
    return (
      <main className="app-shell">
        <section className="panel connection-panel" aria-live="polite">
          <Landmark size={28} />
          <h1>Аким на 5 часов</h1>
          {error ? (
            <>
              <p role="alert">{error}</p>
              <button className="ghost-button" type="button" onClick={() => setAttempt(attempt + 1)}>
                Повторить загрузку
              </button>
            </>
          ) : (
            <p role="status">Загружаем районы Астаны, каталог мер и правила сценария...</p>
          )}
        </section>
      </main>
    );
  }

  return <Simulator city={city} />;
}

function Simulator({ city }) {
  const districtNames = Object.keys(city.districts);
  const measureById = useMemo(
    () => Object.fromEntries(city.measures.map((measure) => [measure.id, measure])),
    [city.measures],
  );
  const indicatorById = useMemo(
    () => Object.fromEntries(city.indicators.map((indicator) => [indicator.id, indicator])),
    [city.indicators],
  );
  const directions = useMemo(() => ["Все меры", ...new Set(city.measures.map((measure) => measure.direction))], [city.measures]);

  const [decisions, setDecisions] = useState(() => EXAMPLE.map((item) => ({ ...item })));
  const [activeDirection, setActiveDirection] = useState("Все меры");
  const [activeDistrict, setActiveDistrict] = useState(districtNames.includes("Нура") ? "Нура" : districtNames[0]);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState([]);
  const [status, setStatus] = useState("idle");
  const [explanation, setExplanation] = useState(null);
  const [explanationStatus, setExplanationStatus] = useState("idle");
  const [explanationError, setExplanationError] = useState("");
  const activeRequest = useRef(null);

  useEffect(() => () => activeRequest.current?.abort(), []);

  const selectedIds = decisions.map((decision) => decision.measure_id).filter(Boolean);
  const selectedCount = selectedIds.length;
  const cost = decisions.reduce((sum, item) => sum + (measureById[item.measure_id]?.cost ?? 0), 0);
  const remainingBudget = city.budget - cost;
  const budgetPercent = Math.min(100, Math.round((cost / city.budget) * 100));
  const busy = status === "loading" || explanationStatus === "loading";
  const hasOpenSlot = decisions.some((item) => !item.measure_id);
  const filteredMeasures = city.measures.filter((measure) => {
    const matchesDirection = activeDirection === "Все меры" || measure.direction === activeDirection;
    const text = `${measure.id} ${measure.name} ${measure.direction} ${Object.keys(measure.effects).join(" ")}`.toLowerCase();
    return matchesDirection && text.includes(query.trim().toLowerCase());
  });
  const scores = Object.fromEntries(
    districtNames.map((name) => [name, result?.districts[name].D_after ?? city.districts[name].reference_D]),
  );
  const baselineDavg = districtNames.reduce(
    (sum, name) => sum + city.districts[name].reference_D * city.districts[name].population_share,
    0,
  );
  const baselineDmin = Math.min(...districtNames.map((name) => city.districts[name].reference_D));
  const baselineCritical = districtNames.reduce((sum, name) => {
    const indicators = Object.values(city.districts[name].indicators);
    return sum + indicators.filter((value) => value < city.scoring.critical_threshold).length;
  }, 0);
  const minDistrict = Object.entries(scores).reduce((lowest, current) => (
    current[1] < lowest[1] ? current : lowest
  ));
  const conflictNotes = findConflicts(decisions, city.incompatibilities);
  const budgetDistribution = buildBudgetDistribution(decisions, measureById, districtNames);
  const localAnalysis = buildLocalAnalysis({
    result,
    city,
    selectedCount,
    cost,
    remainingBudget,
    minDistrict,
    conflictNotes,
  });

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

  function loadExample() {
    changeDecisions(EXAMPLE.map((item) => ({ ...item })));
    if (districtNames.includes("Нура")) setActiveDistrict("Нура");
  }

  function updateDecision(index, patch) {
    changeDecisions(decisions.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  }

  function selectMeasure(index, measureId) {
    const measure = measureById[measureId];
    updateDecision(index, {
      measure_id: measureId,
      district: measure?.type === "district" ? (decisions[index].district || activeDistrict) : null,
    });
  }

  function addMeasure(measure) {
    if (selectedIds.includes(measure.id) || !hasOpenSlot || measure.cost > remainingBudget) return;
    const index = decisions.findIndex((item) => !item.measure_id);
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

  function exportScenario() {
    const payload = {
      city: "Астана",
      exported_at: new Date().toISOString(),
      decisions: serializeDecisions(decisions, city.measures),
      result,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "akim-scenario.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><Landmark size={19} /></span>
          <div>
            <strong>Аким на 5 часов</strong>
            <span>AI-симулятор управления городом Астана</span>
          </div>
        </div>
        <div className="topbar-actions">
          <StatusPill status={status} warnings={result?.warnings?.length ?? conflictNotes.length} />
          <button className="ghost-button" type="button" onClick={loadExample}>
            <Sparkles size={15} /> Пример
          </button>
          <button
            className="ghost-button"
            type="button"
            onClick={() => changeDecisions(Array.from({ length: city.rules.decision_count }, emptyDecision))}
          >
            <RotateCcw size={15} /> Сброс
          </button>
          <button className="primary-compact" type="button" onClick={exportScenario}>
            <Download size={15} /> Экспорт
          </button>
        </div>
      </header>

      <section className="kpi-grid" aria-label="Итоги сценария" aria-live="polite">
        <MetricCard
          label="Общий score"
          value={`${formatNumber(result?.Score ?? city.baseline_score, 1)} / 100`}
          meta={result ? `${formatDelta(result.score_delta, 1)} за сценарий` : "базовое состояние"}
          sparkline={sparklineSets.score}
          tone="teal"
        />
        <MetricCard
          label="Бюджет"
          value={`${cost} / ${city.budget}`}
          meta={`${budgetPercent}% занято, ${costLabel(Math.max(remainingBudget, 0))} резерв`}
          sparkline={sparklineSets.budget}
          tone={remainingBudget < 0 ? "red" : "blue"}
        />
        <MetricCard
          label="Средний индекс района"
          value={formatNumber(result?.D_avg ?? baselineDavg, 1)}
          meta={result ? `${formatDelta(result.D_avg - baselineDavg, 1)} к базе` : "по исходным районам"}
          sparkline={sparklineSets.districts}
          tone="green"
        />
        <MetricCard
          label="Минимальный район"
          value={`${formatNumber(result?.D_min ?? baselineDmin, 1)} · ${minDistrict[0]}`}
          meta={result ? "после выбранных мер" : "до вмешательств"}
          sparkline={sparklineSets.min}
          tone="amber"
        />
        <MetricCard
          label="Критичные зоны"
          value={result?.N_crit ?? baselineCritical}
          meta={result ? `${formatDelta((result.N_crit ?? 0) - baselineCritical, 0)} за сценарий` : "порог ниже 40"}
          sparkline={sparklineSets.critical}
          tone={(result?.N_crit ?? baselineCritical) > 0 ? "red" : "green"}
        />
      </section>

      <section className="workspace-grid">
        <section className="panel decision-panel">
          <div className="panel-heading">
            <div>
              <h2>Сценарий развития</h2>
              <p>{selectedCount} решений · горизонт {city.horizon} {city.horizon_unit}</p>
            </div>
            <button className="primary-button" type="button" disabled={busy} onClick={calculate}>
              <Play size={16} /> {status === "loading" ? "Считаем..." : "Рассчитать"}
            </button>
          </div>

          <BudgetProgress cost={cost} budget={city.budget} />

          <div className="decision-list">
            {decisions.map((decision, index) => {
              const measure = measureById[decision.measure_id];
              return (
                <article className={`decision-row ${index === 1 ? "featured" : ""}`} key={index}>
                  <div className="row-number">{index + 1}</div>
                  <div className="decision-fields">
                    <label>
                      <span>Мера {index + 1}</span>
                      <select value={decision.measure_id} onChange={(event) => selectMeasure(index, event.target.value)}>
                        <option value="">Выберите меру</option>
                        {city.measures.map((item) => (
                          <option key={item.id} value={item.id}>{item.id} · {item.name}</option>
                        ))}
                      </select>
                    </label>
                    <div className="decision-meta">
                      <span>{measure?.direction ?? "Направление"}</span>
                      <span>{measure ? `Лаг ${measure.lag}` : `Место ${index + 1}`}</span>
                    </div>
                    <label>
                      <span>Район {index + 1}</span>
                      <select
                        disabled={!measure || measure.type === "city"}
                        value={decision.district || ""}
                        onChange={(event) => updateDecision(index, { district: event.target.value })}
                      >
                        <option value="">{measure?.type === "district" ? "Выберите район" : "Весь город"}</option>
                        {districtNames.map((district) => (
                          <option key={district} value={district}>{district}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <span className="cost-chip">{costLabel(measure?.cost ?? 0)}</span>
                  <button
                    className="icon-button"
                    type="button"
                    onClick={() => updateDecision(index, emptyDecision())}
                    aria-label={`Очистить решение ${index + 1}`}
                  >
                    <X size={15} />
                  </button>
                </article>
              );
            })}
          </div>

          <ScenarioMessages
            errors={errors}
            conflictNotes={conflictNotes}
            warnings={result?.warnings ?? []}
            status={status}
            remainingBudget={remainingBudget}
          />
        </section>

        <section className="panel district-panel">
          <div className="panel-heading">
            <div>
              <h2>Качество районов</h2>
              <p>Индекс 0-100 · данные по районам Астаны</p>
            </div>
            <BarChart3 size={20} />
          </div>
          <DistrictGrid
            districtNames={districtNames}
            scores={scores}
            activeDistrict={activeDistrict}
            setActiveDistrict={setActiveDistrict}
          />
          <DistrictDetails
            district={activeDistrict}
            initial={city.districts[activeDistrict]}
            report={result?.districts[activeDistrict]}
            indicatorById={indicatorById}
          />
          <BudgetDistribution entries={budgetDistribution} total={Math.max(cost, 1)} />
        </section>
      </section>

      <section className="panel catalog-panel">
        <div className="panel-heading catalog-heading">
          <div>
            <h2>Каталог мер развития</h2>
            <p>{city.measures.length} мер · показаны только релевантные сценарию варианты</p>
          </div>
          <label className="search-field">
            <Search size={15} />
            <span>Поиск мер</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Поиск меры или эффекта..."
            />
          </label>
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
            const disabled = selectedIds.includes(measure.id) || !hasOpenSlot || measure.cost > remainingBudget;
            return (
              <MeasureCard
                key={measure.id}
                measure={measure}
                indicatorById={indicatorById}
                disabled={disabled}
                selected={selectedIds.includes(measure.id)}
                onAdd={() => addMeasure(measure)}
              />
            );
          })}
        </div>
      </section>

      <section className="analysis-panel" aria-busy={explanationStatus === "loading"}>
        <div className="analysis-header">
          <div>
            <h2>AI-анализ сценария</h2>
            <p>{explanation ? "Ответ сверен с числами сервера" : "Локальный разбор обновляется после расчёта"}</p>
          </div>
          <div className="analysis-actions">
            <span className={`source-badge ${explanation?.ai_generated ? "" : "template"}`}>
              <Bot size={15} /> {explanation ? (explanation.ai_generated ? "AI Generated" : "Шаблон") : "Local Analysis"}
            </span>
            <button className="ghost-button" type="button" disabled={!result || busy} onClick={requestExplanation}>
              <Sparkles size={15} /> {explanationStatus === "loading" ? "Готовим..." : "Объяснить"}
            </button>
          </div>
        </div>

        <div className="analysis-grid" aria-live="polite">
          {explanation
            ? explanationSections.map(([key, title]) => (
              <AnalysisColumn key={key} title={title} items={explanation[key]} tone={key === "risks" ? "red" : key === "strengths" ? "blue" : "green"} />
            ))
            : localAnalysis.columns.map((column) => (
              <AnalysisColumn key={column.title} {...column} />
            ))}
        </div>

        <div className="analysis-footer">
          <div className="change-card">
            <Info size={15} />
            <span>{localAnalysis.change}</span>
          </div>
          {explanationError && (
            <div className="error-card" role="alert">
              <AlertCircle size={15} />
              <span>{explanationError}</span>
            </div>
          )}
        </div>
      </section>
    </main>
  );
}

function MetricCard({ label, value, meta, sparkline, tone }) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{meta}</small>
      </div>
      <div className="mini-bars" aria-hidden="true">
        {sparkline.map((height, index) => (
          <i key={index} style={{ height: `${Math.max(12, height)}%` }} />
        ))}
      </div>
    </article>
  );
}

function StatusPill({ status, warnings }) {
  const labels = {
    idle: "Готов к расчёту",
    loading: "Считаем сценарий",
    valid: "Сценарий валиден",
    invalid: "Есть ошибки",
    error: "Нет ответа API",
  };
  const bad = ["invalid", "error"].includes(status);
  return (
    <span className={`status-pill ${status === "valid" ? "ok" : bad ? "bad" : ""}`}>
      {bad ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}
      {labels[status]}
      {warnings > 0 && <em>риски: {warnings}</em>}
    </span>
  );
}

function BudgetProgress({ cost, budget }) {
  const percent = Math.min(100, Math.round((cost / budget) * 100));
  return (
    <div className="budget-progress">
      <div className="budget-progress-top">
        <span>Использовано {costLabel(cost)} из {costLabel(budget)}</span>
        <strong>{percent}%</strong>
      </div>
      <div className="budget-track">
        <div className={cost > budget ? "over" : ""} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function ScenarioMessages({ errors, conflictNotes, warnings, status, remainingBudget }) {
  const messages = [
    ...errors.map((text) => ({ text, tone: "bad", icon: AlertCircle })),
    ...conflictNotes.map((text) => ({ text, tone: "warn", icon: AlertTriangle })),
    ...warnings.map((warning) => ({
      text: `${warning.district ?? "Город"}: ${warning.indicator} может уйти ниже критического порога`,
      tone: "warn",
      icon: AlertTriangle,
    })),
  ];
  if (remainingBudget < 0) {
    messages.push({ text: `Бюджет превышен на ${costLabel(Math.abs(remainingBudget))}`, tone: "bad", icon: AlertCircle });
  } else if (remainingBudget <= 10) {
    messages.push({ text: `Осторожно: резерв бюджета всего ${costLabel(remainingBudget)}`, tone: "warn", icon: Info });
  }
  if (status === "valid" && messages.length === 0) {
    messages.push({ text: "Сценарий валиден. Все 5 решений распределены корректно в рамках лимитов бюджета.", tone: "ok", icon: ShieldCheck });
  }
  if (messages.length === 0) {
    messages.push({ text: "Заполните 5 решений и рассчитайте сценарий, чтобы увидеть риски и эффект.", tone: "info", icon: Info });
  }
  return (
    <div className="message-grid" aria-live="polite">
      {messages.slice(0, 3).map(({ text, tone, icon: Icon }, index) => (
        <div className={`message-card ${tone}`} key={`${text}-${index}`}>
          <Icon size={15} />
          <span>{text}</span>
        </div>
      ))}
    </div>
  );
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
          <strong>{formatNumber(scores[district], 0)}</strong>
        </button>
      ))}
    </div>
  );
}

function DistrictDetails({ district, initial, report, indicatorById }) {
  const indicators = Object.entries(initial.indicators)
    .map(([code, value]) => [code, report?.indicators[code].after ?? value, report?.indicators[code].delta ?? 0])
    .sort((first, second) => first[1] - second[1]);
  const districtDelta = report ? report.D_after - report.D_before : 0;
  return (
    <div className="district-details">
      <div className="district-score">
        <div>
          <h3>{district} район</h3>
          <span>{initial.profile}</span>
        </div>
        <strong>{formatDelta(districtDelta, 1)} за цикл</strong>
      </div>
      <div className="indicator-bars">
        {indicators.slice(0, 6).map(([code, value, delta], index) => (
          <div className="indicator-bar" key={code}>
            <div className="bar-label">
              <span>{indicatorById[code]?.direction}: {indicatorById[code]?.name ?? code}</span>
              <span>{formatNumber(value, 0)} {delta ? `(${formatDelta(delta, 1)})` : ""}</span>
            </div>
            <div className="bar-track">
              <div className={`bar-fill tone-${index}`} style={{ width: `${Math.max(4, Math.min(100, value))}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function BudgetDistribution({ entries, total }) {
  return (
    <div className="budget-distribution">
      <div className="subheading">
        <strong>Распределение бюджета</strong>
        <span>по выбранным мерам</span>
      </div>
      {entries.map((entry) => (
        <div className="distribution-row" key={entry.name}>
          <span>{entry.name}</span>
          <div><i style={{ width: `${Math.round((entry.value / total) * 100)}%` }} /></div>
          <strong>{costLabel(entry.value)}</strong>
        </div>
      ))}
    </div>
  );
}

function MeasureCard({ measure, indicatorById, disabled, selected, onAdd }) {
  return (
    <article className={`measure-card ${selected ? "selected" : ""}`}>
      <div className="measure-card-top">
        <span>{measure.direction} · {measure.type === "city" ? "город" : "район"}</span>
        <strong>{costLabel(measure.cost)}</strong>
      </div>
      <h3>{measure.name}</h3>
      <p>{effectSummary(measure, indicatorById)}</p>
      <div className="measure-footer">
        <span><Clock3 size={13} /> эффект через {measure.lag} {measure.lag === 1 ? "квартал" : "кварт."}</span>
        <button className="add-measure-button" type="button" disabled={disabled} onClick={onAdd}>
          {disabled ? (selected ? "Выбрано" : "Недоступно") : <><Plus size={14} /> Добавить</>}
        </button>
      </div>
    </article>
  );
}

function AnalysisColumn({ title, items, tone }) {
  return (
    <section className={`analysis-column ${tone}`}>
      <h3>{title}</h3>
      {items?.length ? (
        <ul>
          {items.slice(0, 3).map((item, index) => <li key={index}>{item}</li>)}
        </ul>
      ) : (
        <p>Нет дополнительных замечаний.</p>
      )}
    </section>
  );
}

function buildBudgetDistribution(decisions, measureById, districtNames) {
  const totals = Object.fromEntries(districtNames.map((district) => [district, 0]));
  let cityWide = 0;
  for (const decision of decisions) {
    const measure = measureById[decision.measure_id];
    if (!measure) continue;
    if (measure.type === "city") {
      cityWide += measure.cost;
    } else if (decision.district && totals[decision.district] !== undefined) {
      totals[decision.district] += measure.cost;
    }
  }
  const cityShare = cityWide / districtNames.length;
  return districtNames
    .map((name) => ({ name, value: Math.round((totals[name] + cityShare) * 10) / 10 }))
    .sort((first, second) => second.value - first.value);
}

function findConflicts(decisions, incompatibilities) {
  const selected = decisions.filter((decision) => decision.measure_id);
  const byMeasure = Object.fromEntries(selected.map((decision) => [decision.measure_id, decision]));
  return incompatibilities.flatMap((conflict) => {
    const [first, second] = conflict.measures;
    if (!byMeasure[first] || !byMeasure[second]) return [];
    if (conflict.scope === "same_district" && byMeasure[first].district !== byMeasure[second].district) return [];
    return [`Конфликт: ${first} и ${second}, ${conflict.reason}.`];
  });
}

function effectSummary(measure, indicatorById) {
  return Object.entries(measure.effects)
    .map(([code, value]) => `${indicatorById[code]?.name ?? code} ${value > 0 ? "+" : ""}${value}`)
    .join("; ");
}

function buildLocalAnalysis({ result, city, selectedCount, cost, remainingBudget, minDistrict, conflictNotes }) {
  if (!result) {
    return {
      change: `Сейчас выбрано ${selectedCount} из ${city.rules.decision_count} решений, бюджет ${costLabel(cost)} из ${costLabel(city.budget)}.`,
      columns: [
        {
          title: "Прогноз",
          tone: "blue",
          items: [
            "После расчёта здесь появится прирост Score и слабые районы.",
            "Каталог использует районы Есиль, Алматы, Сарыарка, Байконур и Нура.",
          ],
        },
        {
          title: "Риски",
          tone: "red",
          items: conflictNotes.length ? conflictNotes : ["Пока показаны только предварительные риски бюджета и совместимости."],
        },
        {
          title: "Рекомендации",
          tone: "green",
          items: [
            "Нажмите «Рассчитать», чтобы сверить сценарий с правилами движка.",
            "Оставляйте резерв бюджета на дорогие районные меры.",
          ],
        },
      ],
    };
  }

  const weakDistrict = minDistrict[0];
  const gap = result.gap_to_best !== null && result.gap_to_best !== undefined ? formatNumber(result.gap_to_best, 2) : null;
  return {
    change: `Score ${formatDelta(result.score_delta, 2)}, бюджет ${costLabel(result.cost)} из ${costLabel(city.budget)}. ${gap ? `До лучшего перебора осталось ${gap}.` : "Сравнение с полным перебором недоступно."}`,
    columns: [
      {
        title: "Прогноз",
        tone: "blue",
        items: [
          `Качество районов вырастает до среднего D ${formatNumber(result.D_avg, 2)}.`,
          `Самый слабый район после сценария: ${weakDistrict} (${formatNumber(minDistrict[1], 2)}).`,
          `Критичных зон: ${result.N_crit}.`,
        ],
      },
      {
        title: "Риски",
        tone: "red",
        items: [
          ...(remainingBudget <= 10 ? [`Резерв бюджета низкий: ${costLabel(remainingBudget)}.`] : []),
          ...(result.warnings ?? []).map((warning) => `${warning.district ?? "Город"}: ${warning.indicator} ниже порога ${city.scoring.critical_threshold}.`),
          ...(conflictNotes.length ? conflictNotes : ["Явных конфликтов мер после расчёта нет."]),
        ],
      },
      {
        title: "Рекомендации",
        tone: "green",
        items: [
          "Проверьте Нуру и Сарыарку: у них чаще проявляются узкие места.",
          result.percentile !== null && result.percentile !== undefined
            ? `Сценарий выше примерно ${formatNumber(result.percentile, 1)}% допустимых наборов.`
            : "Запустите предрасчёт сценариев, чтобы увидеть процентиль.",
          "Для финальной подачи используйте объяснение AI или локальный шаблон.",
        ],
      },
    ],
  };
}

export default App;

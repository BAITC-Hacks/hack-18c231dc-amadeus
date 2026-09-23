# План: «Аким на 5 часов»

Составлен 23.09.2026 в 13:50, обновлён в 16:20 по фактическому коду в `main` (после коммита `cf8f4ee`). Заморозка фич — 16:45, финальный push — 17:45, сдача — 18:00.

## 1. Требования из ТЗ и статус

| Требование | Тип | Как проверяется | Статус на 16:20 (`main`) |
|---|---|---|---|
| Единый виртуальный бюджет для всех | must have | `budget` в `data/city.json`, отдаётся в `GET /api/config` | готово |
| Решения по 5 направлениям | must have | `engine/validator.py`: ровно 5 мер, не больше 2 на направление (правила датасета) | готово |
| Автоматический контроль превышения бюджета | must have | `POST /api/simulate` → HTTP 200 `{"valid": false, "errors": [...]}`; автотесты | готово |
| AI-анализ принятых решений | must have | `POST /api/explain`; без ключа — шаблон с `ai_generated: false` | API готов, **в UI нет** |
| Расчёт Astana Quality of Life Score | must have | `engine/simulator.py`, автотесты с эталонами датасета | готово |
| Объяснение сильных сторон, рисков, последствий | must have | поля `strengths`, `risks`, `consequences`, `recommendations` в `/api/explain` | API готов, **в UI нет** |
| Изменение решений меняет Score | критерий проверки | автотесты движка | готово |
| README и воспроизводимость | 25 баллов | проход по README из чистого клона | **README не описывает запуск UI** |
| Использование AI/agentic AI | 25 баллов | агент с tool calling сам вызывает движок для проверки альтернатив | `api/agent.py` и `engine/optimizer.py` готовы, тесты на подменённом клиенте; **к `/api/explain` подключает xehrf** |

## 2. MVP

Сценарий от входа до результата: открыть страницу → увидеть бюджет, районы и каталог мер → выбрать 5 мер → система не даёт выйти за бюджет → нажать «Рассчитать» → получить Score до/после и AI-разбор (сильные стороны, риски, последствия, рекомендации) с подписью источника: модель или резервный шаблон.

Все числа в UI приходят из API. UI не считает Score сам.

## 3. Архитектура и стек

Стек: Python 3.10+, FastAPI, uvicorn, официальный `openai` SDK (Responses API, Structured Outputs), python-dotenv, numpy, pytest + httpx. UI: React 19 + Vite 6 + lucide-react.

```
engine/
  data.py           загрузка data/city.json (и чтение из SQLite)
  validator.py      правила набора: 5 мер, бюджет, направления, несовместимости
  simulator.py      детерминированный расчёт Score
  database.py       SQLite-копия датасета
  postgres_dump.py  выгрузка датасета в SQL для Postgres
  optimizer.py      оптимум полным перебором (numpy), одиночные замены
api/
  main.py           FastAPI: /api/config, /api/simulate, /api/explain
  explanation.py    AI-объяснение и резервный шаблон без ключа
  agent.py          агент с tool calling (пока не подключён к /api/explain)
data/
  city.json         районы, показатели, веса, 14 мер, синергии, несовместимости
  city.sqlite, city_postgres.sql
src/                React UI (App.jsx, main.jsx, styles.css), index.html, vite.config.js
tests/              102 теста в main на 16:20 (pytest --collect-only):
                    test_engine 36, test_api 20, test_explain 19, test_database 2,
                    test_optimizer 10, test_agent 15
```

**Разделение ответственности.** Score считает только движок `engine/`. Модель объясняет готовые числа и ничего не пересчитывает.

**Режим без ключа.** Если `OPENAI_API_KEY` или `OPENAI_MODEL` не заданы либо OpenAI недоступен, `/api/explain` возвращает локальный шаблон с `ai_generated: false`. Весь сценарий проверяется без наших ключей (п. 5.6.6 Положения).

**Agentic AI (в работе).** Агент получает сценарий и инструменты `score_scenario`, `suggest_swaps`, `get_optimum`. Он сам вызывает движок, сравнивает альтернативы в рамках бюджета и объясняет рекомендацию только числами из результатов инструментов. Оптимум ищется полным перебором валидных наборов в `engine/optimizer.py`. Перебор ленивый, при импорте не запускается.

## 4. Оставшиеся сроки

| Время | Что |
|---|---|
| до 15:55 | push [h3] от каждого |
| 16:45 | заморозка фич: дальше только баги, README, DEMO |
| до 16:55 | push [h4] |
| 17:20–17:40 | репетиция демо ×2 |
| 17:45 | финальный push, затем «Сдать решение»; после 17:55 никаких коммитов |

## 5. Разделение работы

| Зона | Файлы | Кто |
|---|---|---|
| Движок и данные | `engine/` (кроме `optimizer.py`), `data/`, `tests/test_engine.py`, `tests/test_database.py` | xehrf |
| API и объяснение | `api/main.py`, `api/explanation.py`, `tests/test_api.py`, `tests/test_explain.py` | xehrf |
| UI | `src/`, `index.html`, `package.json`, `vite.config.js` | xehrf |
| Агент и оптимум, план | `api/agent.py`, `engine/optimizer.py`, `tests/test_agent.py`, `tests/test_optimizer.py`, `docs/PLAN.md` | Малика (lMakEl) |
| README, демо, чистый клон | `README.md`, `docs/DEMO.md` | secorluve |

Чужой файл правит только его владелец. Подключение агента в `api/main.py` / `api/explanation.py` делает xehrf по договорённости с Маликой.

### Задачи до 16:45, по приоритету

1. **xehrf:** UI вызывает `/api/simulate` и `/api/explain`, показывает AI-разбор с подписью источника («AI-анализ: <модель>» или «Резервный режим: объяснение по шаблону, без LLM»). Расчёт Score и бюджета в `App.jsx` заменяется ответом API.
2. **secorluve:** README — запуск API и UI (`npm install`, `npm run dev`), режим без ключа, раздел «Сторонние компоненты» (FastAPI, uvicorn, openai, python-dotenv, pytest, httpx, React, Vite, lucide-react, датасет организаторов). Убрать фразу «интерфейс пока не реализован».
3. **Малика:** готово в 16:06 — `engine/optimizer.py` (694395 валидных наборов, оптимум 57.24 при стоимости 98, совпадает с `simulate()`) и `api/agent.py` (`explain_with_agent(data)`, та же сигнатура, что у `explain()`, плюс поле `tools_called`). Живой прогон с ключом ещё не сделан.

Не делаем: анализ чувствительности, отдельный SPEC, Chart.js.

После 16:45: secorluve проходит README на чистом клоне без `.env` и пишет `docs/DEMO.md` на 2,5 минуты.

## 6. Риски и запасной вариант

| Риск | Что делаем |
|---|---|
| Нет ключа OpenAI или сеть на площадке легла | Резервный шаблон уже работает, проект полностью проходится без ключа |
| UI не успевает подключиться к API к 16:30 | Демо AI-разбора через Swagger `/docs`, README описывает этот путь |
| Агент с tool calling не готов к 16:40 | Остаётся текущий `/api/explain` (один вызов LLM + шаблон), агент не подключается |
| README не проходит на чистом клоне | Проверка secorluve сразу после 16:45, исправления до 17:30 |

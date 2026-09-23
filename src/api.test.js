import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";

import { API_BASE_URL, explainScenario, getConfig, serializeDecisions, simulateScenario } from "./api.js";

afterEach(() => mock.restoreAll());

test("decisions contain only selected measures and city district is null", () => {
  const input = [
    { measure_id: "M7", district: "Нура", cost: 999 },
    { measure_id: "M12", district: "Нура" },
    { measure_id: "", district: "" },
  ];
  const before = structuredClone(input);
  assert.deepEqual(serializeDecisions(input, [{ id: "M7", type: "district" }, { id: "M12", type: "city" }]), [
    { measure_id: "M7", district: "Нура" },
    { measure_id: "M12", district: null },
  ]);
  assert.deepEqual(input, before);
});

test("all routes use one API base and send only decisions", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => Response.json({ valid: true }));
  const controller = new AbortController();
  const decisions = [{ measure_id: "M12", district: null }];
  await getConfig(controller.signal);
  await simulateScenario(decisions, controller.signal);
  await explainScenario(decisions, controller.signal);
  assert.deepEqual(fetch.mock.calls.map((call) => call.arguments[0]), [
    `${API_BASE_URL}/api/config`, `${API_BASE_URL}/api/simulate`, `${API_BASE_URL}/api/explain`,
  ]);
  assert.equal(fetch.mock.calls[0].arguments[1].method, "GET");
  assert.equal(fetch.mock.calls[0].arguments[1].body, undefined);
  for (const call of fetch.mock.calls.slice(1)) {
    const options = call.arguments[1];
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { decisions });
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.equal(options.signal, controller.signal);
  }
});

test("HTTP 200 validation errors remain available to the UI", async () => {
  const body = { valid: false, errors: ["Стоимость 105 превышает бюджет 100."] };
  mock.method(globalThis, "fetch", async () => Response.json(body));
  assert.deepEqual(await simulateScenario([]), body);
});

test("template explanation retains ai_generated false and all sections", async () => {
  const body = { valid: true, ai_generated: false, summary: "Шаблон.", strengths: [], risks: [], consequences: [], recommendations: [] };
  mock.method(globalThis, "fetch", async () => Response.json(body));
  assert.deepEqual(await explainScenario([]), body);
});

test("network errors have a readable Russian message", async () => {
  mock.method(globalThis, "fetch", async () => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(getConfig(), /Не удалось связаться с сервером/);
});

test("HTTP request validation details are shown", async () => {
  mock.method(globalThis, "fetch", async () => Response.json({ detail: [{ msg: "Обязательное поле" }] }, { status: 422 }));
  await assert.rejects(simulateScenario([]), /Обязательное поле/);
});

test("non-JSON HTTP errors show the status", async () => {
  mock.method(globalThis, "fetch", async () => new Response("Gateway error", { status: 502 }));
  await assert.rejects(getConfig(), /502/);
});

test("cancellation is preserved so stale responses do not become UI errors", async () => {
  mock.method(globalThis, "fetch", async () => { throw new DOMException("Cancelled", "AbortError"); });
  await assert.rejects(getConfig(), { name: "AbortError" });
});

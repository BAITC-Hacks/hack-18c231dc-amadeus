export const API_BASE_URL = "http://127.0.0.1:8000";

async function request(path, { decisions, signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: decisions === undefined ? "GET" : "POST",
      ...(decisions === undefined ? {} : {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decisions }),
      }),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Не удалось связаться с сервером. Проверьте, что бэкенд запущен, и повторите запрос.");
  }
  if (!response.ok) {
    let message = `Сервер вернул ошибку ${response.status}. Попробуйте ещё раз.`;
    try {
      const body = await response.json();
      if (Array.isArray(body.detail)) {
        message = body.detail.map((item) => item.msg).join("; ");
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch { /* Для не-JSON ответа оставляем сообщение со статусом HTTP. */ }
    throw new Error(message);
  }
  return response.json();
}

export const getConfig = (signal) => request("/api/config", { signal });
export const simulateScenario = (decisions, signal) => request("/api/simulate", { decisions, signal });
export const explainScenario = (decisions, signal) => request("/api/explain", { decisions, signal });

export function serializeDecisions(decisions, measures) {
  const byId = Object.fromEntries(measures.map((measure) => [measure.id, measure]));
  return decisions.filter((item) => item.measure_id).map((item) => ({
    measure_id: item.measure_id,
    district: byId[item.measure_id]?.type === "city" ? null : (item.district || null),
  }));
}

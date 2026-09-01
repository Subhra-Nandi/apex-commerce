const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export const API_BASE = BASE;

async function request(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new Error(
      `Cannot reach the backend at ${BASE}. Start it with "uvicorn app.main:app --reload" in the backend folder.`
    );
  }

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }

  if (!response.ok) {
    const detail =
      data?.detail ?? data?.error ?? `Request failed (${response.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export const api = {
  health: () => request("/"),
  summary: () => request("/dashboard/summary"),
  products: () => request("/dashboard/products"),
  orders: () => request("/dashboard/orders"),
  order: (id) => request(`/dashboard/orders/${id}`),
  llmStatus: () => request("/agent/llm-status"),
  offers: () => request("/recovery/offers"),
  negotiate: (body) => request("/agent/negotiate", { method: "POST", body }),
  purchase: (body) => request("/agent/purchase", { method: "POST", body }),
  purchaseResilient: (body) =>
    request("/recovery/agent-purchase", { method: "POST", body }),
  setStock: (sku, stock_quantity) =>
    request("/recovery/slippage/stock", {
      method: "POST",
      body: { sku, stock_quantity },
    }),
  setCost: (sku, cost_price_inr) =>
    request("/recovery/slippage/cost", {
      method: "POST",
      body: { sku, cost_price_inr },
    }),
  resetSlippage: () => request("/recovery/slippage/reset", { method: "POST" }),
};

const formatter = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Format integer paise (how the backend stores money) as rupees. */
export const inr = (paise) => `₹${formatter.format((paise || 0) / 100)}`;

/** Format a rupee float (how a few API fields arrive) as rupees. */
export const rupees = (value) => `₹${formatter.format(Number(value) || 0)}`;
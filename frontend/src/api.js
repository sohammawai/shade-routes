// Every call to the backend lives here.
// VITE_API_URL is empty on your laptop (Vite forwards /api to port 8000)
// and set to the Lambda function URL on AWS Amplify.
export const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

async function request(path, options) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* the body was not JSON */
    }
    throw new Error(message);
  }
  return res.json();
}

const post = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  areas: () => request("/api/areas"),
  compare: () => request("/api/compare"),
  streets: (area) => request(`/api/areas/${area}/streets`),
  priority: (area, hour, limit = 10) => request(`/api/areas/${area}/priority?limit=${limit}&hour=${hour}`),
  street: (area, id) => request(`/api/areas/${area}/streets/${encodeURIComponent(id)}`),
  simulate: (area, streetId, trees, hour) =>
    request(`/api/areas/${area}/simulate`, post({ street_id: streetId, trees, hour })),
  route: (area, from, to, hour) =>
    request(`/api/areas/${area}/route?from_lat=${from.lat}&from_lon=${from.lon}` +
            `&to_lat=${to.lat}&to_lon=${to.lon}&hour=${hour}`),
  places: (area, q) => request(`/api/areas/${area}/places?q=${encodeURIComponent(q)}`),
  chat: (message, area, history) => request("/api/chat", post({ message, area, history })),
};

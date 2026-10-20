import { requestJson } from "../api.js";


export async function optimizeRoute(apiBase, points) {
  return requestJson(`${apiBase}/optimize/route`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ points }),
  });
}

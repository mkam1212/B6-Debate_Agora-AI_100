import { requestJson } from "../api.js";


export async function runKMeans(apiBase, { points, clusters = 2 }) {
  return requestJson(`${apiBase}/ml/kmeans`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ points, clusters }),
  });
}

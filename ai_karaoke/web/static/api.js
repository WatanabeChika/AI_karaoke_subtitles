export async function readJson(response, fallbackMessage) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || fallbackMessage);
  }
  return data;
}

export function artifactUrl(jobId, itemId, kind, download = false) {
  const query = new URLSearchParams({ job_id: jobId, item_id: itemId, kind });
  if (download) query.set("download", "1");
  return `/artifact?${query}`;
}

export function assUrl(jobId, itemId) {
  return `/ass?${new URLSearchParams({ job_id: jobId, item_id: itemId })}`;
}

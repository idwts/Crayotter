export function downloadTextFile({
  text,
  filename,
  document: doc = document,
  urlApi = URL,
  scheduler = (callback, delay) => window.setTimeout(callback, delay),
}) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const objectUrl = urlApi.createObjectURL(blob);
  const anchor = doc.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  doc.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  scheduler(() => urlApi.revokeObjectURL(objectUrl), 0);
}

export function jobEventsDownloadUrl(jobId) {
  return `/jobs/${encodeURIComponent(jobId || "job")}/events.log`;
}

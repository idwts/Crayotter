const request = async (url, { method = "GET", json } = {}) => {
  const options = { method };
  if (json !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(json);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.error || message;
    } catch (_) {
      // Ignore non-JSON error bodies.
    }
    throw new Error(message);
  }
  return response.json();
};

export const fetchCurrentStory = (jobId) => request(`/jobs/${jobId}/story/current`);
export const reviseStory = (jobId, version, changes) =>
  request(`/jobs/${jobId}/story/${version}`, { method: "PATCH", json: { changes } });
export const approveStory = (jobId, version) =>
  request(`/jobs/${jobId}/story/${version}/approve`, { method: "POST", json: {} });
export const composeStoryVideo = (jobId, version, options) =>
  request(`/jobs/${jobId}/story/${version}/compose-video`, {
    method: "POST",
    json: options,
  });

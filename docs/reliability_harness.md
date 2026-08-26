# Crayotter Scenario and Reliability Harness

The harness evaluates the same worker protocol, scheduler events, editing plans,
story documents, and artifact manifests used by real jobs. It does not maintain
a second video workflow.

Scenario definitions live in `harness/scenarios/`. JSON works with the base
installation; YAML is optional and requires PyYAML.

## Evaluate a persisted job

```bash
python -m script.harness harness/scenarios/beijing_century.json \
  --incident app_state/jobs/<job_id> \
  --output benchmark_runtime/beijing-report.json \
  --junit benchmark_runtime/beijing-junit.xml
```

To launch the normal worker in an isolated runtime, add `request.task` to a
scenario and use `--run`. The runtime profile is removed immediately after the
worker exits.

The harness provides deterministic fault occurrence rules, sanitized provider
record/replay, credential-free capability preflight caching, JSON/JUnit reports,
and acceptance oracles for hard terms, forbidden terms, duration, voice,
approved narration, download quorum, retry time, fallback count, and wall time.

The accompanying runtime safeguards include:

- two-thirds material-download quorum, preserving successful downloads;
- a worker-wide DashScope video retry/backoff budget configured through
  `CRAYOTTER_VIDEO_RETRY_BUDGET_SECONDS`;
- deterministic female/male narration voice selection from the approved request;
- terminal wall/processing timing recovery if a worker exits without SLA events;
- concrete-brief story direction selection and similarity-risk checks.

Replay and preflight persistence excludes credential-like keys and sanitizes
error strings through Crayotter's existing secret redaction.

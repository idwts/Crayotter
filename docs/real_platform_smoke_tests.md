# Real Platform Download Smoke Tests

`tests/test_real_platform_download_smoke.py` verifies the platform-agnostic
`download_material_video` tool against manually supplied Douyin, Kuaishou, and
Xiaohongshu URLs.

These tests are intentionally skipped by default. They perform real network
downloads, may depend on platform policy, URL freshness, cookies already
available to `yt-dlp`, regional network conditions, and FFmpeg availability.
They should not be added as mandatory CI checks.

## Run Manually

PowerShell example:

```powershell
$env:CRAYOTTER_RUN_REAL_PLATFORM_SMOKE = "1"
$env:CRAYOTTER_SMOKE_DOUYIN_URL = "https://..."
$env:CRAYOTTER_SMOKE_KUAISHOU_URL = "https://..."
$env:CRAYOTTER_SMOKE_XIAOHONGSHU_URL = "https://..."
.venv\Scripts\python.exe -m unittest tests.test_real_platform_download_smoke -v
```

Each platform URL is optional. When the global switch is enabled but a platform
URL is missing, that platform subtest is skipped.

## Expected Behavior

The smoke test calls:

```python
download_material_video.invoke({
    "url": url,
    "source": source,
    "filename": f"smoke_{source}",
    "fallback_query": "校园宣传片",
    "fallback_to_bilibili": True,
})
```

The downloaded output is redirected to a temporary directory with
`tempfile.TemporaryDirectory()`, so real smoke runs do not write downloaded
videos into the repository runtime folders.

If the tool returns `status == "success"`, the test requires the output path to
exist and the response metadata to include `source`, `original_source`, and
`standardized`.

If the tool returns `status == "error"`, the test records `error_type` and
`error` and still passes structurally. This keeps the smoke useful for observing
current platform availability without making platform policy changes break the
default test suite.

Do not use these tests to add anti-bot bypasses, automated login workarounds,
JavaScript signature injection, CDN watermark URL rewriting, watermark removal,
or hosted crawler dependencies.

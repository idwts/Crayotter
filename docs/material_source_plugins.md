# Material Source Plugin Policy

Crayotter may support third-party material-source adapters through explicit plugin boundaries. Advanced platform adapters must be opt-in, disabled by default, and reviewed before they are used in production workflows.

Allowed plugin capabilities include metadata probing, user-provided URL import, authorized cookie use, format standardization, and manual smoke testing. Adapter authors should declare capabilities and validate them with `script.tools.material_source_policy.validate_material_source_capabilities`. Capabilities are allowlisted: unknown capability names are blocked until explicitly reviewed and added to the safe set.

The following capabilities are prohibited:

- Anti-bot bypass
- Automated login/session circumvention
- JavaScript signature injection
- CDN or watermark-bypass URL rewriting
- Watermark removal
- Captcha bypass

Plugins must not introduce Playwright, MediaCrawler, YOLO, ProPainter, hosted crawler services, or equivalent dependencies for these prohibited capabilities. Third-party platform access must rely on explicit user authorization, comply with platform terms, and avoid scraping or media transformation techniques intended to bypass access controls or ownership marks.

Crayotter Website
================

This folder contains a static landing page for Crayotter.

Run locally:
1) Open terminal in this folder.
2) Run: python -m http.server 8080
3) Visit: http://127.0.0.1:8080

Deploy with GitHub Pages:
1) Push code to GitHub repository default branch (main/master).
2) In GitHub repo, go to Settings -> Pages.
3) In "Build and deployment", choose "Source: GitHub Actions".
4) Ensure workflow ".github/workflows/deploy-pages.yml" exists and run completes.
5) Your site URL will be:
   - https://<your-username>.github.io/<your-repo-name>/
   - If repo name is <your-username>.github.io, URL is https://<your-username>.github.io/
6) If your repository uses Git LFS for mp4 files, keep workflow LFS checkout enabled:
   - actions/checkout with "lfs: true"
   - run "git lfs pull" before uploading pages artifact

Important for asset paths:
- This site uses relative paths, so it works on project pages without extra base-path config.

Video assets:
- Put demo mp4 files into website\assets\videos\
- Expected names:
  - campus_youth_final.mp4
  - Xinjiang_Nature_Final.mp4
  - travel_healing_final.mp4

Config-driven demo + log rendering:
- Edit `website\assets\demo-showcase.json` to manage homepage demo cards.
- Each item supports:
  - `tab`: target tab key (`campus` / `pipeline` / `ops`)
  - `videoSrc`: video path
  - `logSrc`: raw log path used to parse "Phase3 工具调用"
  - `traceHref`: link to detailed trace page
  - `title`, `description`: card display text
- Homepage now parses tool calls directly from each configured log file and renders one video per tab.
- If log files are large, keep only required logs in `website\assets\logs\`.

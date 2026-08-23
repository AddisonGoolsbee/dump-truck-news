# Dump Truck News

<https://www.dumptrucknews.com>

Live news converted into degenerate emojipasta.

This repo is a small monorepo:
- `backend/` fetches BBC RSS items and converts them into emojipasta JSON.
- `frontend/` (Vite + React + TypeScript + Tailwind) renders a feed from JSON in `frontend/public/news/`.

## Frontend

From `frontend/`:

- Install deps: `pnpm install`
- Dev server: `pnpm dev`
- Lint: `pnpm lint`
- Build: `pnpm build`

Notes:
- `pnpm dev` runs `pnpm news:index` first to regenerate `public/news/index.json`.
- News JSON is served as static assets from `frontend/public/news/`.

## Backend

From `backend/`:

- Install deps: `pip install -r requirements.txt`
- Create `backend/.env` with:
  - `XAI_API_KEY`: XAI API key for Grok
  - `ARTICLE_HASH_KEY`: secret salt used to hash RSS GUIDs for deduping (example: `demo-secret-change-me-041f6a73`)
  - `OPENAI_API_KEY`: OpenAI API key, only needed if thumbnail generation is enabled (see below)
- Run: `python main.py`

What it does:
- Fetches the top article from each BBC section RSS feed (US & Canada, World, Business, Technology, Entertainment)
- Converts each to emojipasta via Grok
- Hashes the article GUID for deduplication
- Writes JSON files into `frontend/public/news/`

Thumbnails:
- Disabled by default (costs ~$0.04/image via OpenAI). Set `ENABLE_THUMBNAILS=true` in `backend/.env` (and provide `OPENAI_API_KEY`) to turn them back on.

Deduping:
- Only articles with hashes not seen in the last 7 days are published.

## Useful scripts (frontend)

From `frontend/`:

- `pnpm news:index`: regenerates `public/news/index.json`
- `pnpm sitemap`: regenerates `public/sitemap.xml` (uses `../CNAME` if present)

## GitHub Pages

- Deploy workflow: `.github/workflows/deploy-pages.yml` builds `frontend/` with pnpm and publishes `frontend/dist` to GitHub Pages on pushes to `master`.
- Vite `base` is set to the repo name automatically when running in GitHub Actions so assets load correctly under `/REPO/`.

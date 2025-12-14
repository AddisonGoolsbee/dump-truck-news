# Dump Truck News

The greatest news.

## Frontend
Frontend lives in `frontend/` (Vite + React + TypeScript with Tailwind). From that folder:

- Install deps: `pnpm install`
- Dev server: `pnpm dev`
- Lint: `pnpm lint`
- Build: `pnpm build`

## Backend
News to Emojipasta converter lives in `backend/` (Python). From that folder:

- Install deps: `pip install -r requirements.txt`
- Set environment variables in `.env`:
  - `XAI_API_KEY`: Your XAI API key for Grok
  - `ARTICLE_HASH_KEY`: Secret salt used to hash RSS GUIDs for deduping (example: `demo-secret-change-me-041f6a73`)
- Run: `python main.py`

The script fetches top news articles from BBC RSS, converts them to emojipasta format using Grok, hashes the article GUID for deduplication, and saves JSON files to `frontend/public/news/`. Only articles with hashes not seen in the last 7 days are published.

## GitHub Pages

- Deploy workflow: `.github/workflows/deploy-pages.yml` builds `frontend/` with pnpm and publishes `frontend/dist` to GitHub Pages on pushes to `master`.
- Vite `base` is set to the repo name automatically when running in GitHub Actions so assets load correctly under `/REPO/`.

# Pill Cabinet

A private Telegram Mini App for tracking scheduled medication, intake, and inventory.

## Run locally

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Copy `.env.example` to `.env` and set the bot token and scheduler secret. The backend uses SQLite locally and accepts a Supabase PostgreSQL URL in production. The scheduler is invoked externally through `POST /internal/scheduler/tick` with `X-Scheduler-Secret`.

`DEMO_MODE=true` may be enabled for a public browser preview. Telegram deployments should keep it disabled so all real data requires verified Telegram initialization data.

## Deployment

- Frontend: Cloudflare Pages, build command `npm run build`, output `dist`.
- Backend: Render web service, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Database: Supabase PostgreSQL via `DATABASE_URL`.
- Scheduler: any free external HTTP cron calling the protected tick endpoint once per minute.

Never place Telegram, Supabase secret, Render, or Cloudflare credentials in this repository or frontend environment variables.

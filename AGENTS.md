# Repository Guidelines

## Project Structure & Module Organization

The FastAPI backend is in `app/`: agent orchestration and tools in `app/agent/`, HTTP endpoints in `app/routers/`, business logic in `app/services/`, persistence in `app/models/` and `app/repositories/`, and Pydantic schemas in `app/schemas/`. Migrations are under `alembic/`. The React + Vite + Tailwind client is in `frontend/src/`; production assets go to `static/`. Operational scripts are in `scripts/`, and technical notes are in `docs/`.

## Build, Test, and Development Commands

- `python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt` - create the Python 3.11+ environment.
- `Copy-Item .env.example .env` - create local configuration; fill in credentials before starting services.
- `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` - run the API locally.
- `python -m compileall app scripts` - perform the required Python syntax check.
- `cd frontend; npm run dev` - run Vite with API proxying; `npm run build` type-checks and writes to `static/`.
- `cd frontend; npm test` - run Vitest (add focused tests for client behavior).
- `./scripts/start.ps1` / `./scripts/stop.ps1` - start or stop Docker. Use `-NoMigrate`, `-SkipSeed`, or `-Data` deliberately; run `docker compose run --rm app alembic upgrade head` for migrations.

## Coding Style & Naming Conventions

Use 4-space indentation and type hints in Python; keep async routes and services non-blocking. Place code in the matching existing module. Use `snake_case` for Python, `camelCase` for TypeScript, and `PascalCase` for React components. Keep TypeScript strict and avoid unrelated reformatting.

## Testing Guidelines

There is no checked-in Python test suite. Run `compileall`, exercise changed API paths through `/health` or `/docs`, and add regression tests for new behavior. Frontend tests use Vitest and should be named `*.test.ts`/`*.test.tsx`; run `npm test` and `npm run build` before review.

## Commit & Pull Request Guidelines

Recent commits use short summaries with prefixes such as `fix:`, `chore:`, or `refactor(rag):`; Chinese summaries are also used. Keep commits focused. Pull requests should explain behavior/configuration changes, link issues, list verification commands, and include screenshots or API examples when useful. Call out migrations, seed data, and external services.

## Security & Configuration

Never commit `.env`, secrets, tokens, database passwords, generated databases, logs, or build output. Start from `.env.example`; production requires a strong `AUTH_SECRET_KEY`, explicit CORS origins, and non-demo credentials. Do not log full user data or credentials, and retain the existing degraded paths when Redis, Milvus, or LLM services are unavailable.

# Repository Guidelines

## Project Structure & Module Organization

Relay collects automotive supply-chain email attachments and manages customer delivery plans. The implementation roadmap, phase dependencies, and acceptance checks are tracked in `docs/工作地图.md`.

- `docs/architecture/Relay-汽车供应链数据采集平台-技术方案.md`: architecture, scope, mappings, and acceptance criteria.
- `samples/edi/raw/`: five original EDI workbook samples. `samples/edi/reference/`: manual DRX reference and unconfirmed JLR workbook. Preserve original files. Keep sanitized automated fixtures under `tests/fixtures/`.
- `docs/commercial/`: historical estimate and local contract files.
- Planned paths: `src/relay/api/` for FastAPI, `workers/` for mail and parser processes, `parsers/` for template adapters, `domain/` and `services/` for shared rules and use cases, `database/` for SQLAlchemy models.
- Planned supporting paths: `migrations/`, `tests/{unit,integration,fixtures}/`, `web/` for Next.js, and `deployments/` for Compose.

## Build, Test, and Development Commands

See `README.md` for setup and the work map for milestones still in progress.

- `uv sync --locked`: install locked dependencies.
- `uv run uvicorn relay.api.app:app --reload`: start the REST API.
- `uv run python -m relay.workers.mail`: start email ingestion; use `relay.workers.parser` for parsing.
- `uv run alembic upgrade head`: apply database migrations.
- `uv run pytest`: run backend tests.
- `uv run ruff check .` and `uv run ruff format --check .`: check code and formatting.
- `uv run mypy src/relay`: check types.
- `docker compose -f deployments/docker-compose.yml up --build`: start the stack.

Document frontend scripts in `README.md` when adding `web/package.json`.

## Coding Style & Naming Conventions

Use four-space Python indentation, type annotations, snake_case functions/modules, PascalCase classes, and `test_*.py` tests. Configure Ruff and mypy with the scaffold. Use Decimal for quantities, NUMERIC in PostgreSQL, and decimal strings in REST responses. Preserve unconfirmed business codes. Use two-space TypeScript indentation, PascalCase components, and camelCase functions.

## Testing Guidelines

Use pytest and golden fixtures pairing sanitized workbooks with manually verified JSON. Cover multiple material blocks, date intervals, special dates, zero quantities, unknown codes, and retries. Verify transactions, unique constraints, and worker leases against PostgreSQL. No coverage threshold or frontend framework is established.

## Commit & Pull Request Guidelines

No Git history is available. Use imperative messages such as `Add DRX parser`. PRs should describe scope, link requirements, report validation, identify unresolved business assumptions, and include UI screenshots where relevant.

## Security & Configuration

Use 263 IMAP over TLS with read-only collection. Keep credentials out of source control, logs, and API responses. Sanitize shared fixtures; preserve archived attachments and validate files before parsing.

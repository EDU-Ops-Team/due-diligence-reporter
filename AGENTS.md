# Project Instructions

## Validation

- Test: `uv run pytest`
- Lint: `uv run ruff check .`
- Typecheck: `uv run mypy src/`

## Notes

- Runtime secrets live outside the repo. Do not read or commit `.env`, OAuth tokens, or credential files.
- Use `uv` for all Python commands.

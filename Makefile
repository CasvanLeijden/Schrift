.PHONY: setup dev css css-watch test lint format

setup:
	uv sync
	scripts/tailwind.sh

dev:
	uv run uvicorn schrift.main:app --reload

css:
	scripts/tailwind.sh --minify

css-watch:
	scripts/tailwind.sh --watch

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

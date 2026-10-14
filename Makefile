.PHONY: setup dev css css-watch js js-watch test lint format

setup:
	uv sync --all-extras
	cd frontend && npm install
	cd frontend && npm run build
	scripts/tailwind.sh

dev:
	uv run uvicorn schrift.main:app --reload

css:
	scripts/tailwind.sh --minify

css-watch:
	scripts/tailwind.sh --watch

test:
	uv run pytest
	cd frontend && npm test

js:
	cd frontend && npm run build

js-watch:
	cd frontend && npm run watch

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

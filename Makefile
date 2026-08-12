.PHONY: test lint format typecheck check demo setup

# Developer commands. Run from the repository root.

test:
	python3 -m pytest

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy autoforge

check: lint
	ruff format --check .
	mypy autoforge
	python3 -m pytest

demo:
	python3 -m autoforge.scripts.demo_foundation

setup:
	pip install -e ".[dev]"

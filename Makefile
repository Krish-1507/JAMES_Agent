.PHONY: install dev run check lint test ui new-tool clean format

install:
	pip install -e .

dev:
	pip install -e ".[dev,ui,mcp]"
	pip install playwright
	playwright install chromium

run:
	python -m james

text:
	python -m james --text

ui:
	python -m james --ui

check:
	python -m james --check

format:
	ruff check james tests --fix
	ruff format james tests

lint:
	ruff check james tests
	python -m compileall -q james

test:
	python -m pytest -q

new-tool:
	@echo "Usage: python -m james --new-tool <name>"

clean:
	@if [ -d .git ]; then git clean -fdx --exclude=.env --exclude=legacy; fi
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete."

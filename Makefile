.PHONY: install dev run text ui check lint test new-tool clean format doctor

install:
	pip install -e .

dev:
	pip install -e ".[dev,ui,mcp]"
	pip install playwright
	playwright install chromium

run:
	james

text:
	james --text

ui:
	james --ui

check:
	james --check

doctor:
	james doctor

format:
	ruff check james tests --fix
	ruff format james tests

lint:
	ruff check james tests
	python -m compileall -q james

test:
	python -m pytest -q

new-tool:
	@echo "Usage: james --new-tool <name>"

clean:
	@if [ -d .git ]; then git clean -fdx --exclude=.env --exclude=legacy; fi
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete."

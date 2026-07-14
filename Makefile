.PHONY: install dev run check lint test ui new-tool clean

install:
	pip install -e .

dev:
	pip install -e .
	pip install pytest ruff playwright
	playwright install chromium

run:
	python -m james

text:
	python -m james --text

ui:
	python -m james --ui

check:
	python -m james --check

lint:
	ruff check james || true
	python -m py_compile james/__main__.py james/config.py \
		james/llm/*.py james/tools/*.py james/voice/*.py james/core/*.py

test:
	python -m james --check
	python -c "import james.tools.registry as r; assert 'write_file' in r.ToolRegistry().names()"

new-tool:
	@echo "Usage: python -m james --new-tool <name>"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

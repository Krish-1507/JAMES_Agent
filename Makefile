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
	@if [ -d .git ]; then git clean -fdx --exclude=.env --exclude=legacy; fi
	@if [ -d james/__pycache__ ]; then rm -rf james/__pycache__; fi
	@if [ -d james/*/__pycache__ ]; then rm -rf james/*/__pycache__; fi
	@if [ -d james/core/__pycache__ ]; then rm -rf james/core/__pycache__; fi
	@if [ -d james/tools/__pycache__ ]; then rm -rf james/tools/__pycache__; fi
	@if [ -d james/ui/__pycache__ ]; then rm -rf james/ui/__pycache__; fi
	@if [ -d james/voice/__pycache__ ]; then rm -rf james/voice/__pycache__; fi
	@if [ -d james/llm/__pycache__ ]; then rm -rf james/llm/__pycache__; fi
	@if [ -d james/evaluation/__pycache__ ]; then rm -rf james/evaluation/__pycache__; fi
	@if [ -d tests/__pycache__ ]; then rm -rf tests/__pycache__; fi
	@echo "Clean complete."

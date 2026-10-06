install:
	python -m pip install -e '.[dev]'
test:
	python -m pytest -q
lint:
	ruff check src tests
demo:
	python -m router.demo

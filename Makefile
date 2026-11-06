# NetPulse developer commands. Run `make install` once, then the others.
PYTHON ?= python3.12
VENV   := .venv
BIN    := $(VENV)/bin
IMAGE  ?= netpulse
TAG    ?= local

.PHONY: install lint test run docker-build docker-run clean

install:  ## Create the virtualenv and install runtime + dev dependencies
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.txt

lint:  ## Static checks
	$(BIN)/ruff check .

test:  ## Unit tests with an 80% coverage gate
	$(BIN)/pytest -q --cov=app --cov-fail-under=80

run:  ## Dev server with auto-reload on http://127.0.0.1:8000
	$(BIN)/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

docker-build:
	docker build -t $(IMAGE):$(TAG) --build-arg APP_VERSION=$(TAG) .

docker-run:  ## http://localhost:8080, history persisted in the netpulse-data volume
	docker run --rm --name netpulse -p 8080:8000 -v netpulse-data:/app/data $(IMAGE):$(TAG)

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

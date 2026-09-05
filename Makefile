# Job360 developer Makefile.
#
# Convention: every target is self-describing. Run `make help` for a menu.

.PHONY: help install test test-fast test-live lint format migrate bootstrap migrate-roundtrip clean

help:
	@echo "Job360 targets:"
	@echo "  install          install backend in editable mode"
	@echo "  test             run the full backend test suite"
	@echo "  test-fast        run only @pytest.mark.fast tests (smoke subset)"
	@echo "  lint             ruff lint across backend/"
	@echo "  format           ruff format across backend/"
	@echo "  migrate          apply pending DB migrations"
	@echo "  bootstrap        run backend/scripts/bootstrap_dev.py against localhost:8000"
	@echo "  migrate-roundtrip  test migrations down->up round-trip"
	@echo "  clean            wipe __pycache__ + *.pyc"

install:
	cd backend && python -m pip install -e .

test:
	cd backend && python -m pytest tests/ -q -p no:randomly

test-fast:
	cd backend && python -m pytest tests/ -m fast -q -p no:randomly

# Real ONLINE end-to-end test — hits live job-site APIs (no mocks). Needs
# internet; excluded from `make test`. Runs on demand + nightly in CI.
test-live:
	cd backend && python -m pytest -m live -v -p no:randomly

lint:
	cd backend && python -m ruff check src tests

format:
	cd backend && python -m ruff format src tests

migrate:
	cd backend && python -m migrations.runner up

bootstrap:
	cd backend && python scripts/bootstrap_dev.py

migrate-roundtrip:
	bash scripts/migration_roundtrip.sh

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

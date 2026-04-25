# Job360 developer Makefile.
#
# Convention: every target is self-describing. Run `make help` for a menu.
# `verify-step-0` is the aggregate gate checked by the Step-0 Ralph Loop.

.PHONY: help install test test-fast lint format migrate bootstrap verify-step-0 verify-step-1 verify-step-1-5 clean

help:
	@echo "Job360 targets:"
	@echo "  install          install backend in editable mode"
	@echo "  test             run the full backend test suite"
	@echo "  test-fast        run only @pytest.mark.fast tests (smoke subset)"
	@echo "  lint             ruff lint across backend/"
	@echo "  format           ruff format across backend/"
	@echo "  migrate          apply pending DB migrations"
	@echo "  bootstrap        run backend/scripts/bootstrap_dev.py against localhost:8000"
	@echo "  verify-step-0    run the Step-0 pre-flight gate (aggregate of below)"
	@echo "  verify-step-1    run the Step-1 engine→API seam gate"
	@echo "  verify-step-1-5  run the Step-1.5 stabilisation gate (S1.1 + S1.5 + Step-3 MVP)"
	@echo "  clean            wipe __pycache__ + *.pyc"

install:
	cd backend && python -m pip install -e .

test:
	cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly

test-fast:
	cd backend && python -m pytest tests/ -m fast -q -p no:randomly

lint:
	cd backend && python -m ruff check src tests

format:
	cd backend && python -m ruff format src tests

migrate:
	cd backend && python -m migrations.runner up

bootstrap:
	cd backend && python scripts/bootstrap_dev.py

# ---------------------------------------------------------------------------
# Step-0 pre-flight gate.
#
# The Ralph Loop halts once this target exits 0. Each check is best-effort
# wired so a failure prints a readable reason instead of a silent non-zero.
# ---------------------------------------------------------------------------

verify-step-0:
	@echo "==> Step-0 gate: pytest"
	cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly --tb=no
	@echo "==> Step-0 gate: env parity"
	cd backend && python scripts/check_env_example.py
	@echo "==> Step-0 gate: migrations applied"
	cd backend && python -m migrations.runner status
	@echo "==> Step-0 gate: docs inventory"
	@test -f CONTRIBUTING.md          || { echo "MISSING: CONTRIBUTING.md"; exit 1; }
	@test -f backend/README.md        || { echo "MISSING: backend/README.md"; exit 1; }
	@test -f frontend/README.md       || { echo "MISSING: frontend/README.md"; exit 1; }
	@test -f docs/README.md           || { echo "MISSING: docs/README.md"; exit 1; }
	@test -f docs/troubleshooting.md  || { echo "MISSING: docs/troubleshooting.md"; exit 1; }
	@test -f .gitattributes           || { echo "MISSING: .gitattributes"; exit 1; }
	@test -f setup.bat                || { echo "MISSING: setup.bat"; exit 1; }
	@test -f backend/scripts/bootstrap_dev.py || { echo "MISSING: bootstrap_dev.py"; exit 1; }
	@test -f backend/migrations/0010_run_log_observability.up.sql || { echo "MISSING: 0010 up"; exit 1; }
	@echo "==> Step-0 gate: PASS"
	@mkdir -p .claude
	@git rev-parse HEAD > .claude/step-0-verified.txt
	@echo "STEP-0 GREEN: $$(cat .claude/step-0-verified.txt)"

# ---------------------------------------------------------------------------
# Step-1 engine→API seam gate.
#
# Aggregates the 13 verification checks from docs/step_1_plan.md §Verification.
# Ralph Loop halts once this exits 0 and the sentinel is written.
# ---------------------------------------------------------------------------

verify-step-1:
	@echo "==> Step-1 gate: pytest regression (>=1,018p/0f/3s)"
	cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly --tb=short
	@echo "==> Step-1 gate: migration concurrency"
	cd backend && python scripts/verify_migration_race.py
	@echo "==> Step-1 gate: dataclass round-trip (B1+B2)"
	cd backend && python scripts/verify_dataclass_roundtrip.py
	@echo "==> Step-1 gate: expired-job filter (B9)"
	cd backend && python -m pytest tests/test_api_security.py::test_expired_jobs_filtered -v -p no:randomly
	@echo "==> Step-1 gate: CLI↔ARQ parity (B10)"
	cd backend && python -m pytest tests/test_workers_tasks.py::test_cli_arq_scoring_parity -v -p no:randomly
	@echo "==> Step-1 gate: enrichment batch concurrency (B7)"
	cd backend && python -m pytest tests/test_job_enrichment.py::test_enrich_batch_respects_semaphore -v -p no:randomly
	@echo "==> Step-1 gate: hybrid mode fallback (B8)"
	cd backend && python -m pytest tests/test_retrieval_integration.py::test_mode_hybrid_empty_index_falls_back tests/test_retrieval_integration.py::test_mode_hybrid_populated_index_fuses -v -p no:randomly
	@echo "==> Step-1 gate: per-user rate limit (B12)"
	cd backend && python -m pytest tests/test_api_security.py::test_search_concurrent_cap_per_user -v -p no:randomly
	@echo "==> Step-1 gate: lazy-import startup safety"
	cd backend && python scripts/verify_lazy_imports.py
	@echo "==> Step-1 gate: ARQ worker smoke"
	cd backend && ARQ_TEST_MODE=1 python scripts/verify_arq_functions.py
	@echo "==> Step-1 gate: frontend build"
	cd frontend && npm run build
	@echo "==> Step-1 gate: PASS"
	@mkdir -p .claude
	@git rev-parse HEAD > .claude/step-1-verified.txt
	@echo "STEP-1 GREEN: $$(cat .claude/step-1-verified.txt)"

# ---------------------------------------------------------------------------
# Step-1.5 stabilisation gate (S1.1 + S1.5 + Step-3 MVP).
#
# Aggregates the verification checks from docs/step_1_5_plan.md §Verification.
# Ralph Loop halts once this exits 0 and the sentinel is written.
# ---------------------------------------------------------------------------

verify-step-1-5:
	@echo "==> Step-1.5 gate: pytest regression"
	cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly --tb=short
	@echo "==> Step-1.5 gate: dim-column round-trip"
	cd backend && python -m pytest tests/test_database.py::test_dim_columns_round_trip -v -p no:randomly
	@echo "==> Step-1.5 gate: bombshell value-presence"
	cd backend && python -m pytest tests/test_api.py::test_jobs_response_includes_score_dim_breakdown -v -p no:randomly
	@echo "==> Step-1.5 gate: ghost-detection state machine"
	cd backend && python -m pytest tests/test_ghost_detection_integration.py -v -p no:randomly
	@echo "==> Step-1.5 gate: ESCO normaliser smoke"
	cd backend && python -m pytest tests/test_cv_parser_esco.py -v -p no:randomly
	@echo "==> Step-1.5 gate: profile version + JSON Resume endpoints"
	cd backend && python -m pytest tests/test_profile_versions_endpoint.py -v -p no:randomly
	@echo "==> Step-1.5 gate: notification ledger endpoint"
	cd backend && python -m pytest tests/test_notifications_endpoint.py -v -p no:randomly
	@echo "==> Step-1.5 gate: PASS"
	@mkdir -p .claude
	@git rev-parse HEAD > .claude/step-1-5-verified.txt
	@echo "STEP-1.5 GREEN: $$(cat .claude/step-1-5-verified.txt)"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

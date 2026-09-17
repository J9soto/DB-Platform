.PHONY: help install install-dev test test-cov lint format format-check typecheck security \
        schema dashboards docker-up docker-down clean validate-examples demo \
        k3s-setup k3s-demo k3s-down k8s-reference \
        cre-install demo-cre cre-api cre-clean

PYTHON ?= python3
CNPG_VERSION ?= 1.30.0
CNPG_MANIFEST = https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v$(CNPG_VERSION)/cnpg-$(CNPG_VERSION).yaml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install the platform (runtime dependencies only)
	$(PYTHON) -m pip install -e . --break-system-packages

install-dev: ## Install the platform with dev tooling (pytest, ruff, mypy, bandit)
	$(PYTHON) -m pip install -e ".[dev]" --break-system-packages

cre-install: ## Install the Change Risk Engine (CLI, parser, API) -- separate from dbre's runtime deps
	$(PYTHON) -m pip install -e ".[cre,cre-api,dev]" --break-system-packages

test: ## Run all tests: unit, policy, and integration (stdlib unittest; also pytest-compatible)
	$(PYTHON) -m unittest discover -s tests -v

test-cov: ## Run tests under coverage (requires pytest + pytest-cov)
	pytest tests --cov=dbre_platform --cov-report=term-missing

lint: ## Lint with ruff
	ruff check src/ tests/ automation/

format: ## Auto-format with ruff
	ruff format src/ tests/ automation/

format-check: ## Check formatting without modifying files (CI mode)
	ruff format --check src/ tests/ automation/

typecheck: ## Type-check with mypy
	mypy src/dbre_platform src/change_risk_engine

security: ## Run bandit static security analysis
	bandit -r src/dbre_platform src/change_risk_engine -c pyproject.toml

schema: ## Regenerate schemas/database-request.schema.json from the Pydantic models
	$(PYTHON) automation/generate_schema.py

dashboards: ## Regenerate monitoring/dashboards/*.json from the dashboard model
	$(PYTHON) automation/generate_dashboards.py

validate-examples: ## Validate every example request against policy
	@for f in examples/requests/*.yaml; do \
		echo "--- $$f ---"; \
		dbre request validate $$f || true; \
		echo; \
	done

docker-up: ## Start the local PostgreSQL container (no AWS account required)
	docker compose up -d postgres

docker-down: ## Stop and remove the local PostgreSQL container and its volume
	docker compose down -v

k3s-setup: ## Install the CloudNativePG operator into the current cluster (one-time; needs KUBECONFIG)
	kubectl apply --server-side -f $(CNPG_MANIFEST)
	kubectl -n cnpg-system rollout status deployment/cnpg-controller-manager --timeout=180s
	kubectl get crd clusters.postgresql.cnpg.io -o name

k3s-demo: ## Provision the K3s example request against the current cluster (needs KUBECONFIG + k3s-setup)
	dbre request validate examples/requests/k3s-app.yaml
	dbre readiness assess examples/requests/k3s-app.yaml
	dbre request provision examples/requests/k3s-app.yaml --mode k3s
	dbre audit tail

k3s-down: ## Delete the K3s demo Cluster (data included) from the dbre namespace
	kubectl delete cluster catalog-api -n dbre --ignore-not-found

k8s-reference: ## Regenerate k8s/reference/*.yaml from build_cluster_manifest
	$(PYTHON) automation/generate_k8s_reference.py

demo: docker-up ## Run the full local demo: provision, validate, readiness, backup, DR test
	@echo "Waiting for PostgreSQL to accept connections..."
	@sleep 3
	dbre request provision examples/requests/dev-app.yaml --mode local
	dbre request validate examples/requests/prod-app-compliant.yaml
	dbre readiness assess examples/requests/prod-app-compliant.yaml
	dbre audit tail

demo-cre: ## Run the Change Risk Engine demo: assess a low-risk and a high-risk change (no DB needed)
	cre demo
	cre history

cre-api: ## Run the Change Risk Engine REST API + web UI at http://127.0.0.1:8000 (needs cre-install)
	uvicorn change_risk_engine.api.app:app --reload --port 8000

clean: ## Remove local build/test/runtime artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +

cre-clean: ## Remove the Change Risk Engine's local demo store and audit log
	rm -rf .cre/ audit-log/cre-audit.jsonl

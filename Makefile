.PHONY: types fixtures dev test e2e run-agent run-geo run-sites run-web

# Export JSON Schema from contracts and regenerate the web types.
# If json2ts chokes on all.json, generate per-model files from contracts/schema/*.json instead.
types:
	PYTHONPATH=. python -m contracts.export
	cd web && npx -p json-schema-to-typescript json2ts -i ../contracts/schema/all.json -o src/types/contracts.ts --unreachableDefinitions

fixtures:
	PYTHONPATH=. python fixtures/make_grid_fixture.py
	PYTHONPATH=. python fixtures/make_agent_fixtures.py
	PYTHONPATH=. python fixtures/validate.py

run-agent:
	PYTHONPATH=. uvicorn services.agent.app:app --reload --port 8000

run-geo:
	PYTHONPATH=. uvicorn services.geo.app:app --reload --port 8001

run-sites:
	PYTHONPATH=. uvicorn services.sites.app:app --reload --port 8002

run-web:
	cd web && npm run dev

dev:
	docker compose up --build

test:
	PYTHONPATH=. pytest -q services/geo services/sites services/agent connectors

e2e:
	PYTHONPATH=. python scripts/e2e.py

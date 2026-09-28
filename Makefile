# Contract: fill in target bodies as the work lands, but never rename these targets.
# CI calls install, lint, test, build and licences by name.
# Windows without make: run the command inside each target directly.
# Deployment targets come later: AWS changes go through CDK, with an IAM user requested from andres (DECISIONS.md D10, corrected).

PYTHON ?= python3

.PHONY: install lint test build licences ci data validate pipeline train eval up

install:
	$(PYTHON) -m pip install -r requirements.txt

lint:
	$(PYTHON) -m compileall -q config src scripts

test:
	@printf '%s\n' 'test: no tests yet - owners add pytest suites under tests/'

build:
	@printf '%s\n' 'build: nothing to build yet'

licences:
	$(PYTHON) scripts/licences.py

ci: install lint test build licences

# Local workflow targets (owners in parentheses).

data:  # diego: organizer S3 -> data/raw -> data/processed/latam_bank.duckdb
	$(PYTHON) src/etl/ingest_s3_duckdb.py

validate:  # diego: basic table and key checks
	$(PYTHON) src/etl/validate_data.py

pipeline:  # diego: silver and gold tables (INTERFACES.md #5)
	@printf '%s\n' 'pipeline: not implemented yet (diego)'

train:  # cristhian: M1, M2, M3 (INTERFACES.md #4)
	@printf '%s\n' 'train: not implemented yet (cristhian)'

eval:  # cristhian: planted scenarios and test messages against baselines
	@printf '%s\n' 'eval: not implemented yet (cristhian)'

up:  # arturo: run the API and front end locally with duckdb + mock LLM
	@printf '%s\n' 'up: not implemented yet (arturo)'

# Contract: fill in target bodies as the work lands, but never rename these targets.
# CI calls install, lint, test, build and licences by name.
# Windows without make: run the command inside each target directly.
# AWS changes go through CDK in infra/ (DECISIONS.md D12). synth and infra-test need no AWS
# credentials; diff, deploy and destroy need an IAM user from andres (D10, corrected).

PYTHON ?= python3
STAGE ?= dev
PROFILE ?= expvivo

.PHONY: install lint test build licences ci data validate pipeline train eval up \n	infra-install infra-test synth diff deploy destroy

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

# Infrastructure (infra/README.md). Use your own stage: make synth STAGE=dev-<yourname>

infra-install:  # everyone: CDK CLI (Node) and CDK Python libraries
	npm install -g aws-cdk
	$(PYTHON) -m pip install -r infra/requirements-dev.txt

infra-test:  # everyone, no AWS credentials needed
	cd infra && $(PYTHON) -m pytest -q tests

synth:  # everyone, no AWS credentials needed
	cd infra && cdk synth -c stage=$(STAGE)

diff:  # needs an IAM user from andres
	cd infra && cdk diff --all -c stage=$(STAGE) --profile $(PROFILE)

deploy:  # needs an IAM user from andres; dev and prod only after agreeing with andres
	cd infra && cdk deploy --all -c stage=$(STAGE) --profile $(PROFILE)

destroy:  # removes a personal stage; never prod
	cd infra && cdk destroy --all -c stage=$(STAGE) --profile $(PROFILE)

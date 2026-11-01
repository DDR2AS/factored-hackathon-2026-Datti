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

test:  # arturo: chat API suite (front: cd frontend && npm ci && npm test, needs Node 22.22+/24.15+)
	$(PYTHON) -m pytest -q tests

build:
	@printf '%s\n' 'build: nothing to build yet'

licences:
	$(PYTHON) scripts/licences.py

ci: install lint test build licences

# Local workflow targets (owners in parentheses).

data:  # organizer S3 -> data/raw -> data/processed/latam_bank.duckdb; without the S3 key, the SYNTHETIC replica
	$(PYTHON) scripts/get_data.py

validate:  # diego: basic table and key checks
	$(PYTHON) src/etl/validate_data.py

pipeline:  # bronze -> silver -> quality (194 contracts) -> gold (INTERFACES.md #5) -> DynamoDB serving export
	$(PYTHON) scripts/run_pipeline.py --serving

train:  # M1 ranker, M2 intent classifier, M3 risk evidence + lift experiment (INTERFACES.md #4)
	$(PYTHON) scripts/train.py

eval:  # 240 planted scenarios end to end, v2 vs v1.4 rules (B1); report in docs/evaluacion.md
	$(PYTHON) scripts/evaluate.py

up:  # run the API and front end locally (gold via duckdb, M1/M2 models, G2 investigator, mock LLM)
	cd frontend && npm ci && npm run build
	$(PYTHON) scripts/local_api.py --port 8000

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

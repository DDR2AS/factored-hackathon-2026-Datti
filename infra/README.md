# Infrastructure (AWS CDK, Python)

Every AWS resource for Expediente Vivo is defined here. Nobody changes AWS by hand in the console: a change to infrastructure is a pull request that edits this folder. Andrés administers the account; teammates don't have AWS access yet and ask him for an IAM user when their work needs deploying (DECISIONS.md D10, corrected; D12).

You can change and test everything in this folder **without AWS credentials**: `cdk synth` and the tests run offline.

## Layout

| Path | What it defines | Main owner |
| --- | --- | --- |
| `app.py` | Wires the stacks together for one stage | andres |
| `cdk.json` | Default context: stage, region, model IDs by role, feature switches | andres |
| `stacks/config.py` | Reads context into `StageConfig`; naming and removal rules | andres |
| `stacks/common.py` | `python_function` (standard Lambda), `allow_bedrock`, model env vars | andres |
| `stacks/data_stack.py` | S3 (lake, artifacts, traces), DynamoDB (sessions, cases, serving, demo), Glue, Athena | andres + diego |
| `stacks/pipeline_stack.py` | Pipeline Lambda + Step Functions map over partitions | diego |
| `stacks/workflow_stack.py` | Case lifecycle state machine, investigator, SLA timer group | andres |
| `stacks/api_stack.py` | HTTP API routes, API Lambda, analyst Cognito pool, throttling, warm-up | arturo + andres |
| `stacks/web_stack.py` | CloudFront + S3 site; `/api/*` forwarded to the HTTP API | arturo + diego |
| `stacks/monitoring_stack.py` | Alarms, SNS alerts topic, optional budget | andres |
| `stacks/access_stack.py` | IAM deployers group and users (andres only) | andres |
| `tests/test_stacks.py` | Guardrails every change must keep | everyone |
| `web_placeholder/` | Page served until `frontend/dist` exists | — |

Lambda code is the repo's `src/` folder; entry points are in `src/handlers/` (`api.py`, `investigator.py`, `case_steps.py`, `pipeline.py`). They are placeholders until each owner connects the real code behind `INTERFACES.md`.

## Stages

A stage is a full, isolated copy of the system. Everything is named `expvivo-<stage>-...`, and stacks are `ExpVivo-<stage>-<Stack>`.

- `dev-<yourname>`: your personal sandbox, when you have an IAM user. Deploy and destroy it freely.
- `dev`: shared integration stage; deployed from `main`.
- `prod`: what judges see; deployed by Andrés from `main` only. Data is retained if the stack is deleted.

Stage names are 2–20 lowercase letters, digits or hyphens (checked in `config.py`).

## Set up (once)

```bash
npm install -g aws-cdk
cd infra
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements-dev.txt
```

## Work without AWS credentials

```bash
cd infra
cdk synth -c stage=dev-diego            # builds the CloudFormation templates into cdk.out/
python -m pytest -q tests               # guardrails
```

CI runs the same two commands on every push and pull request.

## Make a change

1. Claim it in `STATUS.md` (skill `claim-before-build`).
2. Put the resource in the stack that owns that concern (table above). Create Lambdas with `python_function(...)` so they get the stage name, log retention, tracing and `STAGE` variable. Grant access with the construct's `grant_*` methods, never `"*"` resources.
3. New environment variables for application code go in `INTERFACES.md` #9, so code and infrastructure agree on names.
4. New tunable values (model IDs, thresholds, switches) go in `cdk.json` context and `config.py`, not hard-coded in a stack.
5. Run `cdk synth` and the tests; add a test when you add a rule worth keeping.
6. Open a pull request. Changes to IAM, the access stack or `prod` settings need Andrés's review.

## Deploy (only once Andrés has given you an IAM user)

Andrés creates your user in the `expvivo-deployers` group and hands you credentials privately. Store them in a named AWS CLI profile, never in the repo. Then:

```bash
cd infra
cdk diff   --all -c stage=dev-<yourname> --profile expvivo
cdk deploy --all -c stage=dev-<yourname> --profile expvivo
cdk destroy --all -c stage=dev-<yourname> --profile expvivo   # when you're done
```

Never deploy `dev` or `prod` from a laptop without agreeing it with Andrés first.

## Switches (`-c key=value`)

| Key | Default | Effect |
| --- | --- | --- |
| `stage` | `dev` | Which copy of the system to build |
| `models` | Haiku 4.5 / Sonnet 5 / Opus 5 | Bedrock model ID per role (`chat`, `investigator`, `judge`); confirm the exact IDs in the Bedrock console |
| `bundle` | `false` | `true` installs `src/requirements-lambda.txt` into the Lambda package (needs Docker) |
| `warmup` | `false` | `true` pings the API Lambda every 5 minutes (use during judging) |
| `allowedOrigins` | `["*"]` | CORS origins for the HTTP API |
| `alertEmail` | none | Email subscribed to alarms and the budget |
| `createBudget` | `false` | Account-wide monthly budget with alerts at `budgetLimitsUsd`; enable in one stage only |
| `withAccess` | `false` | Adds the `ExpVivo-Access` stack (IAM deployers group); Andrés only |
| `deployers` | `[]` | IAM users to create in the deployers group (no credentials are created) |

## Andrés's one-time account setup

```bash
cdk bootstrap aws://<account-id>/us-east-2
cdk deploy ExpVivo-Access -c withAccess=true -c deployers='["arturo","cristhian","diego"]'
```

The bootstrap execution role is broad by default. To narrow what CloudFormation can create for the team, bootstrap with `--cloudformation-execution-policies <policy-arn>`.

## Costs

Everything here is pay-per-use and scales to zero; idle cost is storage only (about 2 USD a month per stage). Destroy personal stages you aren't using. Bedrock calls are the main cost (plan v2, section 10).

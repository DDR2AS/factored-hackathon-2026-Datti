# Project agent memory

Committed home for knowledge every agent session in this repo needs: how to build, test and run it, where the plan lives, and the sharp edges. Keep it short; point to files instead of repeating them.

## Start here

1. Read `RUNBOOK.md` (event facts, rules, local-first setup, operating model).
2. Read `DECISIONS.md` and `INTERFACES.md` before writing code; they are the current truth.
3. Full plan and reasoning: `docs/plan/expediente-vivo-v2.html` (HTML; read its text).
4. Follow the skills in `.agents/skills/` (copied to `.claude/skills/`).

## Sharp edges

- **Infrastructure is `infra/` (AWS CDK, Python).** Read `infra/README.md` before touching it. Create Lambdas with `python_function`, grant access with `grant_*`, put tunables in `cdk.json` context, keep resource names `expvivo-<stage>-*`. `cdk synth -c stage=dev-<name>` and `python -m pytest -q tests` (inside `infra/`) run without AWS credentials; run both before any infra PR.
- **AWS by request, through CDK only.** andres administers the AWS account; teammates don't have access yet. Build and test locally with the backends behind the interfaces (DuckDB for data, `mock` for the LLM). When work needs deploying, the teammate asks andres for an IAM user and deploys through the repo's CDK code; never through the console or ad-hoc scripts. Don't invent deploy steps before then, and never call AWS services directly from app code.
- **No secrets or data in git.** `.env` is ignored; `.env.test` is the empty template. `data/`, `*.duckdb`, `*.csv`, `*.parquet` are ignored.
- **`.gitignore` also ignores every `*.json` and the folders `src/agent/` and `src/analysis/`.** Files there are silently not committed. Don't put shared code there; check `git status` after creating JSON files.
- **Windows.** Several teammates use Windows without `make`. Every Makefile target wraps a plain `python ...` command; run it directly. Use `PYTHON=python make <target>` where `python3` doesn't exist.
- **Models and providers.** Claude via Bedrock in the cloud, through `src/llm/` only. `requirements.txt` still lists `openai`; don't build on it (open question in `STATUS.md`).
- **Synthetic content.** All conversation text and all Portuguese are generated; label them synthetic. Team-written test messages never go into training data or prompts.

## Commands

- Install: `pip install -r requirements.txt`
- Raw data to local DuckDB: `python src/etl/ingest_s3_duckdb.py` (needs the organizer S3 key in `.env`)
- Basic data checks: `python src/etl/validate_data.py`
- Licence inventory: `python scripts/licences.py`
- Infrastructure (no AWS credentials needed): `cd infra && cdk synth -c stage=dev-<name>` and `cd infra && python -m pytest -q tests`
- Full contract: see `Makefile` (targets are fixed; fill in bodies, never rename them).

## Maintaining this file

Keep it to knowledge useful to almost every future session. Prefer rewriting or pruning entries over appending. Edit `.agents/skills/` and copy the result into `.claude/skills/`.

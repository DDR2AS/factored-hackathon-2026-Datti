# Runbook: shared operating context

Read this first, before any work, whether you are a person or an agent. It is deliberately short.
Where it disagrees with an older note, this file wins. Full plan and reasoning:
[`docs/plan/expediente-vivo-v2.html`](docs/plan/expediente-vivo-v2.html) (open it in a browser).

## 1. Event facts

| When | What |
| --- | --- |
| Thu 25 Sep | Kickoff; 10-day sprint starts |
| Sun 27 Sep | Plan v2 agreed (this repo's decisions D1–D11) |
| **Mon 28 Sep** | **Implementation starts** |
| Tue 29 Sep | M1: lane B works end to end on the deployed URL |
| Thu 1 Oct | M2: three lanes and all screens live |
| **Sat 3 Oct 18:00** | **M3: code freeze** |
| Sun 4 Oct | M4: video and slides |
| **Mon 5 Oct, before noon** | **Submission** (confirm the exact hour with the organizers) |

Submission: email to hackathon.admin@factored.ai with this public repo, the deployed link,
4–6 slides and a short video pitch.

## 2. What we are building

**Expediente Vivo v2**: complaint and dispute handling for LATAM Bank with controlled AI.
A customer writes (Spanish or Portuguese) about a charge or fee. The system understands the request,
finds the exact charge among the customer's own transactions, checks it against data and picks a
lane by rule:

- **A** resolve now with the data,
- **B** open a complete case with a promised date (money disputes also get an investigator report
  for an analyst),
- **C** hand to a person with a structured file.

Principle: data prepares facts, ML ranks and scores, rules in code decide, GenAI understands and
explains, people approve anything that touches money. Nothing moves money. Nothing approves credit.

Components you will see named everywhere:

| Name | What it is | Owner |
| --- | --- | --- |
| M1 | Transaction ranker (LightGBM LambdaRank, calibrated) | Cristhian |
| M2 | Calibrated intent classifier, 9 classes | Cristhian |
| M3 | Risk evidence features + one-day fraud-lift experiment | Cristhian |
| G1 | Chat extraction and drafting (Claude Haiku 4.5) | Arturo |
| G2 | Investigator agent, read-only tools (Claude Sonnet 5) | Andrés |
| G3 | Offline evaluation judge (Claude Opus 5) | Cristhian |
| Gateway | The only way code reaches customer data; scoped by session | Andrés |
| Lane rules | YAML rules that pick A/B/C | Arturo |

## 3. How we are judged

Judged on AI engineering, data engineering, ML, data analytics and documentation. **Depth beats
breadth**; more workflows earn no bonus. The brief requires:

- normal path, ambiguous or unsupported request (clarify or abstain), human-required case with a
  structured handoff;
- Spanish **and** Portuguese;
- permissions enforced outside model prose;
- held-out evaluation against a baseline: safe automated resolution, containment, escalation
  quality, unsafe outcomes, p50/p95 latency, cost per case;
- failures handled: prompt injection, expired session, unauthorized access, tool failure;
- tracing, bounded retries, safe fallback, reproducible setup;
- an honest account of what it would take to run in production.

## 4. Rules that constrain how we work

- **AWS is operated only by Andrés.** Nobody else has access to the AWS account, and nobody should
  ask for AWS credentials or try to deploy. Build and test locally; Andrés deploys.
- **Never commit secrets or data.** Credentials live in your local `.env` (copy `.env.test`). The
  organizer's read-only S3 key is in the data dictionary PDF; it goes in `.env`, never in git.
  `data/`, `*.duckdb`, `*.csv`, `*.parquet` are git-ignored on purpose.
- **Gotcha:** `.gitignore` also ignores every `*.json` file and the folders `src/agent/` and
  `src/analysis/`. Don't put code you want shared in those folders, and don't rely on committed
  JSON files until the team agrees to change `.gitignore` (see `STATUS.md`).
- **No private data in public places or external model calls.** The dataset is synthetic, but keep
  model inputs minimal anyway: no document numbers, addresses or phone numbers.
- **Portuguese and all conversation text are generated**; label them synthetic wherever they appear.

## 5. Local-first development

Everything must run on a laptop without AWS:

| Need | Local | Cloud (Andrés) |
| --- | --- | --- |
| Raw data | `python src/etl/ingest_s3_duckdb.py` downloads the organizer bucket into `data/raw/` and builds `data/processed/latam_bank.duckdb` | S3 in our account |
| Pipeline, gold tables | DuckDB functions, one partition at a time | Same code inside Lambda + Step Functions |
| Customer data for the chat | Gateway backend over DuckDB | Gateway backend over DynamoDB |
| LLM calls | `mock` provider with fixed fixtures | Bedrock (Haiku 4.5, Sonnet 5, Opus 5) |
| Training, evaluation | `make train`, `make eval` on your machine | SageMaker training job, Bedrock batch |

Write code against the interfaces in `INTERFACES.md`, never against AWS services directly. That is
what lets Andrés swap the backend without touching your code.

`make` is not installed on Windows by default. Every Makefile target prints or wraps a plain
command; run that command directly if you don't have `make`.

## 6. Operating model

The repository is the shared coordination bus for people and agents.

- **`STATUS.md`**: claim a unit of work before building it; append-only.
- **`DECISIONS.md`**: consequential decisions; append-only, newest at the bottom. The human states
  the rationale; agents never invent it.
- **`INTERFACES.md`**: the current shape of every hand-off between owners; edited in place. A merge
  conflict there means stop and talk to the owner.
- Coordination files (`STATUS.md`, `DECISIONS.md`, `INTERFACES.md`) are committed straight to `main`.
  Code goes through a feature branch and a pull request.
- `.agents/skills/` holds four behaviours for coding agents (also copied to `.claude/skills/`):
  `claim-before-build`, `boundary-conflict-stop`, `decision-capture`, `hold-under-fire`.
- Daily: 15-minute stand-up at 09:00, internal demo of what works at 21:00.

## 7. Staying in sync

Pull before every session (`git pull --ff-only origin main`). Record decisions and interface changes
here in the repo, not in chat. Keep this runbook operational: current facts and decisions only.

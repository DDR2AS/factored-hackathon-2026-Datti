# Team prompt for coding agents

Copy everything inside the block below into your coding agent (Claude Code, Codex, Cursor, Kiro, etc.), replace `<your name>` with `arturo`, `cristhian` or `diego`, and send it.

```text
I'm <your name>, a member of team Datti in the Factored AI & Data Hackathon 2026. Our project is "Expediente Vivo v2": complaint and dispute handling for the synthetic LATAM Bank dataset, with ML, rules and GenAI working inside the process (not a chatbot). Implementation starts Monday 28 Sep; code freeze Sat 3 Oct 18:00; submission Mon 5 Oct before noon. Reply in the language I write in (Spanish by default).

1. Get the repo up to date.
   - If I don't have it: git clone https://github.com/DDR2AS/factored-hackathon-2026-Datti.git
   - If I do: git pull --ff-only origin main (if it can't fast-forward, stop and tell me; don't overwrite my local work).

2. Read these before doing anything else, in this order:
   RUNBOOK.md, AGENTS.md, DECISIONS.md, INTERFACES.md, STATUS.md, docs/architecture.md,
   the four skills in .agents/skills/, and the text of docs/plan/expediente-vivo-v2.html
   (sections 1, 3, 4, 5, 6, 13 and 14 matter most; it's HTML, read its text content).

3. Then give me a short briefing:
   - my role and what I own (plan section 13 and RUNBOOK section 2),
   - my deliverables for Monday 28 Sep and Tuesday 29 Sep (plan section 13, day-by-day table),
   - the interfaces in INTERFACES.md that I own and the ones I consume, with anything that looks unclear,
   - the open questions in STATUS.md that affect me,
   - a first concrete task I can start on, and the one-line claim you'd add to STATUS.md for it.
   Don't write code or commit anything until I confirm.

Rules for all our work together:
- AWS: andres administers the AWS account and I don't have access yet. Build and test locally. When my work needs to be deployed, I'll ask andres for an IAM user and we'll deploy through the CDK code in the repo, never by hand in the AWS console. Until then, don't write deploy steps and don't call AWS services from app code. Today the only AWS call we make is downloading the organizer's dataset with src/etl/ingest_s3_duckdb.py, using the organizer's read-only key in my local .env.
- Build locally against INTERFACES.md with the local backends: DuckDB for data, the mock provider in src/llm for model calls. Never hard-code a model ID or provider.
- Secrets and data never go into git: .env is ignored (.env.test is the empty template); data/, *.duckdb, *.csv, *.parquet are ignored. Careful: .gitignore also ignores every *.json and the folders src/agent/ and src/analysis/, so files there are silently not committed. Check git status after creating files.
- Before starting any unit of work, follow claim-before-build: pull, read STATUS/DECISIONS/INTERFACES, then append a one-line claim to STATUS.md and commit it straight to main.
- If my work needs to change an interface, stop and propose the change in INTERFACES.md for the owner (boundary-conflict-stop). Never resolve an INTERFACES.md conflict yourself.
- When I make a decision that changes direction or scope, follow decision-capture: ask me for the reason and record my words verbatim in DECISIONS.md. Never invent a rationale.
- Before telling me or anyone that something works, follow hold-under-fire: run it as a stranger would, including an expired session, another customer's ID, a prompt-injection message, a tool failure and the same request in Portuguese where relevant. Report passed, failed or untested.
- Coordination files (STATUS.md, DECISIONS.md, INTERFACES.md) are committed straight to main. Code goes on a feature branch with a pull request.
- Keep the Makefile target names; fill in their bodies. I may be on Windows without make: then run the command inside the target directly.
- All conversation text and all Portuguese are generated: label them synthetic. The team-written test messages never go into training data or prompts.
- The system never moves money, never promises refunds, never approves credit, and a model never picks the lane; rules in code do.
```

# Status

Append current claims and handoffs at the bottom. Coordination updates go straight to `main`; code changes go through a feature branch and pull request.

Claim format:

`- <ISO 8601 UTC>  <who>  <what happened, and what it means for the rest of the team>`

Who is one of: `andres`, `arturo`, `cristhian`, `diego`.

- 2026-09-28T03:30Z  andres  added coordination files, agent skills, Makefile contract, CI and plan v2 (docs/plan) adapted from the nextwave-kit; everyone: read RUNBOOK.md, then claim your Monday work here before coding
- 2026-09-28T03:30Z  andres  claimed AWS platform: CDK skeleton, Cognito, API Gateway, chat Lambda, DynamoDB, Bedrock access; nobody else needs AWS credentials, build against INTERFACES.md locally
- 2026-09-28T03:30Z  andres  open question for the team: .gitignore ignores all *.json and src/agent/ + src/analysis/; agree Monday whether to narrow it (schemas, cdk.json and package.json will need to be committed)
- 2026-09-28T03:30Z  andres  open question for the team: requirements.txt lists openai; plan v2 uses Claude on Bedrock through the LLM client in INTERFACES.md; agree Monday whether to drop it
- 2026-09-28T03:55Z  andres  correction to my earlier claim: I administer AWS and set up the CDK skeleton, but I'm not the only one who will deploy; when your work needs AWS, ask me for an IAM user and deploy through CDK (see DECISIONS.md D10 correction)
- 2026-09-28T04:40Z  andres  added the CDK app in infra/ (Data, Pipeline, Workflow, Api, Web, Monitoring stacks + optional Access), Lambda entry-point stubs in src/handlers/, infra CI job and INTERFACES.md #9; everyone can run cdk synth and the infra tests without AWS credentials; .gitignore now keeps infra/cdk.json
- 2026-09-29T19:54Z  arturo  claimed and delivered as PR #1 (https://github.com/DDR2AS/factored-hackathon-2026-Datti/pull/1, branch arturo/m1-chat-api-front): chat API (#1), case record (#3), orchestrator and YAML lane rules, G1 behind the #8 port with the mock provider, local case lifecycle with a provisional deterministic investigator, analyst routes, and the React front end (judge mode, chat, live case card, analyst console, trace view); built first in my personal repo, then verified on a clean clone of the branch (CI checks on Python 3.11 without Node: 1103 pytest; infra synth and the 10 infra tests unchanged; 232 vitest; local smoke green); nothing deployed yet; reviews welcome
- 2026-09-29T19:54Z  arturo  published INTERFACES.md #1 and #3 as Agreed with the implemented shape and added #10 (analyst console API) as Proposed; for #2, #4, #5, #6, #7, #8 and #9 I only added a 'Proposed change' pointer to docs/propuestas_interfaces.md (in PR #1); owners, please accept, change or reject them, I don't edit your sections
- 2026-09-29T19:54Z  arturo  answer to andres's open .gitignore question: PR #1 adds 6 exceptions after *.json (frontend/package.json, package-lock.json, tsconfig.json, tsconfig.app.json, tsconfig.node.json, .oxlintrc.json); everything else stays ignored; fixtures, rules and demo data are YAML; src/agent/ and src/analysis/ are not used
- 2026-09-29T19:54Z  arturo  for andres, to put M1 on the deployed URL tonight: ApiFn needs STORE_BACKEND (without it every route but /health returns 500), LLM_PROVIDER=none until src/llm exists and TRACE_DIR=/tmp/traces; deploy with -c bundle=true (pydantic, PyYAML) and check /api/health says deps ok; build the front (cd frontend && npm ci && npm run build) before cdk deploy; details and a proposed, not applied, infra diff in docs/verificacion_cdk.md and docs/propuesta_infra.patch (PR #1)
- 2026-09-29T19:54Z  arturo  for cristhian: provisional M1 (m1-rule-0, src/conversation/ranker.py) and M2 (m2-keywords-0, src/conversation/classifier.py) have the #4 signatures and are what your models replace; config/thresholds.yaml and models/ at the repo root are not in the Lambda asset (only src/ is); details under #4 in docs/propuestas_interfaces.md
- 2026-09-29T19:54Z  arturo  for diego: judge-mode customers come from src/conversation/demo/customers.yaml through a demo gateway; the gold columns a real gateway needs are listed under #5 in docs/propuestas_interfaces.md; the v1.4 pipeline in my personal repo is not being copied, reusing any of it is your call

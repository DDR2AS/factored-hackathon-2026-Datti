# Status

Append current claims and handoffs at the bottom. Coordination updates go straight to `main`; code changes go through a feature branch and pull request.

Claim format:

`- <ISO 8601 UTC>  <who>  <what happened, and what it means for the rest of the team>`

Who is one of: `andres`, `arturo`, `cristhian`, `diego`.

- 2026-09-28T03:30Z  andres  added coordination files, agent skills, Makefile contract, CI and plan v2 (docs/plan) adapted from the nextwave-kit; everyone: read RUNBOOK.md, then claim your Monday work here before coding
- 2026-09-28T03:30Z  andres  claimed AWS platform: CDK skeleton, Cognito, API Gateway, chat Lambda, DynamoDB, Bedrock access; nobody else needs AWS credentials, build against INTERFACES.md locally
- 2026-09-28T03:30Z  andres  open question for the team: .gitignore ignores all *.json and src/agent/ + src/analysis/; agree Monday whether to narrow it (schemas, cdk.json and package.json will need to be committed)
- 2026-09-28T03:30Z  andres  open question for the team: requirements.txt lists openai; plan v2 uses Claude on Bedrock through the LLM client in INTERFACES.md; agree Monday whether to drop it

# Decisions

Append only. Never edit or delete an existing entry. Correct a past decision by appending a new decision. Keep the newest entries at the bottom. Each entry is exactly two lines: an ISO 8601 UTC timestamp, who made the decision, what changed and why; a second line stating what the rest of the team must now do differently.

Who is one of: `andres`, `arturo`, `cristhian`, `diego`. D1–D11 are proposed by andres from plan v2 (`docs/plan/expediente-vivo-v2.html`, section 1) and are confirmed or corrected at the Mon 28 Sep stand-up by appending new entries.

- 2026-09-28T03:30Z  andres  D1 scope: deep flow only for "cargo no reconocido" and "cobro indebido"; app, branch and service complaints get intake and routing only. Why (plan v2, not yet confirmed by the team): depth beats breadth; money types are 40% of complaints and the only ones data can explain
  -> rest of team: don't build investigation or ML for the other three complaint types
- 2026-09-28T03:30Z  andres  D2 control model kept from v1.4: LLM understands and drafts, YAML rules pick lane A/B/C, the gateway enforces permissions. Why (plan v2, not yet confirmed by the team): permissions must live outside model prose
  -> rest of team: no code path lets a model output choose a lane or reach data directly
- 2026-09-28T03:30Z  andres  D3 ML: M1 transaction ranker, M2 calibrated classifier, M3 risk evidence with a one-day lift test, plus a learning loop; the v1.4 rule search stays as M1 baseline and fallback. Why (plan v2, not yet confirmed by the team): the full-data analysis rules out complaint-to-transaction links and a better fraud model; these have real ground truth
  -> rest of team: consume models only through the model I/O contract in INTERFACES.md
- 2026-09-28T03:30Z  andres  D4 GenAI investigator (G2) runs asynchronously for lane B money cases; outputs a cited report and a proposal; an analyst approves. Why (plan v2, not yet confirmed by the team): first response in seconds does not fix 16-day resolution
  -> rest of team: analyst console and eval read the investigator report schema in INTERFACES.md
- 2026-09-28T03:30Z  andres  D5 models: Haiku 4.5 for chat, Sonnet 5 for the investigator (compared against Opus 5 on Fri 2 Oct), Opus 5 as offline judge; Claude via Bedrock. Why (plan v2, not yet confirmed by the team): turn latency p95 under 6 s and cost per case
  -> rest of team: call models only through the LLM client interface; don't hard-code a provider or model ID
- 2026-09-28T03:30Z  andres  D6 cloud: fully serverless AWS us-east-2 (Lambda, API Gateway, DynamoDB, Step Functions, EventBridge, Bedrock, SageMaker jobs); Google Cloud plan B dropped. Why (plan v2, not yet confirmed by the team): near-zero idle cost, one deployment to explain, data already in us-east-2
  -> rest of team: don't target BigQuery or any other cloud
- 2026-09-28T03:30Z  andres  D7 evaluation on planted scenarios over real transactions plus 150 team-written test messages; baseline is the v1.4 rules-only version. Why (plan v2, not yet confirmed by the team): real complaints can't be linked to transactions, so ground truth has to be planted
  -> rest of team: keep team-written test messages out of every training set and prompt
- 2026-09-28T03:30Z  andres  D8 deploy from day one: walking skeleton live Mon 28 Sep and the deployed URL works at the end of every day. Why (plan v2, not yet confirmed by the team): deployment is a deliverable
  -> rest of team: merge small working slices to main daily so they can be deployed
- 2026-09-28T03:30Z  andres  D9 cut: push notifications, GCP plan B and most extra intent classes; "create your own customer" is a stretch goal. Why (plan v2, not yet confirmed by the team): frees about two person-days for ML, the investigator and evaluation
  -> rest of team: don't start cut items without a new decision entry
- 2026-09-28T03:30Z  andres  D10 only andres operates AWS; everyone else builds and tests locally against INTERFACES.md with local backends (DuckDB, mock LLM). Why (andres, verbatim): "im the one making everything in AWS, so far, they dont have access to the account"
  -> rest of team: don't ask for AWS credentials or add deploy steps; hand deployable code to andres through main
- 2026-09-28T03:30Z  andres  D11 adopt the nextwave-kit coordination practice: STATUS claims, append-only DECISIONS, in-place INTERFACES, four agent skills, fixed Makefile targets, licence inventory. Why (plan v2, not yet confirmed by the team): four people and several agents working in parallel from day one
  -> rest of team: claim before building, log direction changes here, stop on interface conflicts
- 2026-09-28T03:55Z  andres  D10 corrected: andres is not the only one touching AWS. Teammates don't have access yet; when they need to deploy they ask andres for an IAM user and deploy through CDK. Every AWS change is CDK code in this repo, never manual console changes. Why (andres, verbatim): "So far, they dont have access to they aws account, doesnt mean that im the only one touching aws, if they need to deploy to AWS, they have to use CDK and when the moment comes, they have to ask for a user"
  -> rest of team: keep building locally for now; when your slice needs the cloud, ask andres for an IAM user and ship it as CDK in the repo

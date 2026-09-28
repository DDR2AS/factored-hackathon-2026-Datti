# Pitch (4–6 slides + short video)

Fill this in on Sun 4 Oct from the frozen build and the final evaluation report. Every number must match `docs/eval/` and the README. Say what is measured, what is estimated and what is projected.

## Slide 1: The problem

Prompt: One pain in LATAM Bank's complaint handling, with the dataset figures (43.6% first-contact resolution, 38 h to first response, 16-day resolution, 20,125 unassigned cases).

## Slide 2: What we built

Prompt: Expediente Vivo v2 in one picture: data prepares facts, ML ranks, rules decide, GenAI explains, people approve. Name the three lanes.

## Slide 3: Live demo path

Prompt: The shortest reliable path through the deployed URL: Lucía (normal), Sofía (ambiguous), Martina (human-required with card block), João (Portuguese with investigator). Starting state, expected result, and the recorded fallback if the live path fails.

## Slide 4: Evidence it works

Prompt: v2 against the v1.4 rules-only baseline on the held-out set: safe automated resolution, unsafe outcomes (0/n), escalation quality, matching accuracy, p50/p95 latency, cost per case, by language and country.

## Slide 5: Controls and failures

Prompt: Permissions outside the model, injection, expired session, unauthorized access, tool failure, degraded mode; what judges can trigger themselves.

## Slide 6: What it would take to go live

Prompt: Capacity, data limitations, language coverage, deployment work, remaining risks (plan v2 section 15).

## Video (4–6 minutes)

Prompt: Script the six demo customers in ES and PT; show the trace view and the evaluation page; end with the path to production.

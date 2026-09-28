# [Entry name]: final README template

Replace the current README with this structure on Sat 3 Oct. Keep it readable by a judge who has never seen the project.

## Problem

Prompt: Who has the problem, why it matters, and the dataset evidence.

## Solution

Prompt: What was built, how the lanes work, where ML and GenAI sit, and what people approve.

## Try it

Prompt: The deployed URL, the prepared customers in judge mode, and the failure switches. Then the local path: install, `make data`, `make pipeline`, `make train`, `make eval`, `make up`, with the Windows equivalents.

## Architecture

Prompt: Link `docs/architecture.md` and name the important boundaries (gateway, lane rules, LLM client, case lifecycle).

## Evaluation

Prompt: Datasets, baselines, metrics table with sample sizes and intervals, and the negative results (for example M3 if it showed no lift).

## Decisions

Prompt: Link `DECISIONS.md` and summarize the five that shaped the system most.

## Limits and path to production

Prompt: What is synthetic, what is simulated, and what it would take to run in a real bank.

## Pre-existing components

Prompt: Declare the coordination files, agent skills, Makefile contract, CI and licence script adapted from the team's pre-event `nextwave-kit` repository, and every third-party component with its licence.

## Licence inventory

Prompt: Summarize `LICENCES.md` (regenerate with `python scripts/licences.py`).

## Team

Prompt: Andrés (AWS platform, gateway, investigator), Arturo (conversation, rules, coordination), Cristhian (ML and evaluation), Diego (data engineering), with what each owned.

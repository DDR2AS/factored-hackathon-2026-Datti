---
name: hold-under-fire
description: "Activate whenever anyone says something is working, fixed, complete, or done; before handing over a slice, demoing it, or reporting a result to a teammate or a human."
---

# Hold under fire

Do not accept the claim at face value. Verify the actual artefact, not the account of it.

1. Read the landed code and relevant diff/status. Confirm the claimed change is present, not merely described, attempted, or blocked.
2. Run it as a stranger would: use unrehearsed input, try steps in the wrong order, submit empty values, omit values, exercise missing values, and try the obvious hostile case. For anything customer-facing, include the brief's required failures: an expired session, another customer's ID, a prompt-injection message, the data tool unavailable, and the same request in Portuguese. Use the repository's real run/test instructions.
3. Report the observed commands, inputs, outputs, and failures. Say **passed**, **failed**, or **untested**; never upgrade an unverified claim.

During preparation, a worker sincerely reported two fixes complete when only one had landed; the second was blocked and never applied. Reading the actual code caught it. That is the behaviour required here: verify against the artefact before handoff, demo, or status report.

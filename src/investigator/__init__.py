"""G2 · investigator agent (plan v2 section 6, INTERFACES.md #6).

For lane B money cases: gathers evidence with read-only tools through the gateway (Appendix B),
ranks hypotheses, proposes ONE resolution from a fixed list and drafts the reply; an analyst
decides. Budget: 12 data-tool calls and 90 seconds. Every claim cites a record id that a tool
returned in this run (citation check in code). Nothing executes the recommendation.
"""

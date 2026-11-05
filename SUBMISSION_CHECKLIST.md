# Submission Checklist

- [ ] All five agents register successfully via `scripts/register_switch_agents.py`.
- [ ] Listener connects all five with `filter=addressed`.
- [ ] `@itinerary APPROVE itinerary` writes the document and posts a receipt.
- [ ] `@itinerary REVISE: <change>` loops back and re-prompts.
- [ ] `@itinerary REJECT` posts "HOLD — no changes made" and the document is untouched.
- [ ] An ambiguous input (e.g., `maybe`) results in a hold, not a resume.
- [ ] All tests under `tests/switch/` pass.
- [ ] README contains the "Built during the hackathon" section.
- [ ] `hackathon-start` git tag exists.

# Synthetic LLM-03 fixture: a file that configures prompt caching. Never executed.
import anthropic

claude = anthropic.Anthropic()

HANDBOOK = (
    "You are the onboarding assistant for new engineers joining the payments platform team this quarter. "
    "Explain how the ledger service records each transfer as a pair of balanced journal entries. "
    "Describe how the reconciliation job compares the ledger with the bank statements every night. "
    "Point new engineers to the runbook before they change any alert threshold in production. "
    "Remind them that schema migrations need a review from the data platform team before release. "
    "Explain that feature flags are removed within two sprints after a feature is fully launched. "
    "Describe the on-call rotation, the escalation path and the weekly incident review meeting. "
    "Tell them where to find the architecture decision records and how to propose a new one. "
    "Answer questions about local development setup, test data and the staging environment."
)

REPEATED = HANDBOOK + "\nPoint new engineers to the runbook before they change any alert threshold in production."


def handbook(history):
    system = [{"type": "text", "text": HANDBOOK, "cache_control": {"type": "ephemeral"}}]
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, system=system, messages=history)


def repeated(history):
    system = [{"type": "text", "text": REPEATED, "cache_control": {"type": "ephemeral"}}]
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, system=system, messages=history)

# Synthetic LLM-08 fixture: the file picks a model per task, so the Opus call is not flagged. Never executed.
import anthropic

claude = anthropic.Anthropic()
SMALL = "claude-haiku-4-5"


def sentiment(review):
    return claude.messages.create(model=SMALL, max_tokens=10, messages=[{"role": "user", "content": "Classify the sentiment: " + review}])


def clause_risk(clause):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=5, messages=[{"role": "user", "content": "Is this clause enforceable? Answer yes or no. " + clause}])

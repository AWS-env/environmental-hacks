# Synthetic LLM-03 fixture: size and repeat-length boundaries. Never executed.
import anthropic

claude = anthropic.Anthropic()

# 800 characters: 200 estimated tokens.
AT_BUDGET = (
    "Guideline alpha: check the bravo and foxtrot fields of every ticket you read... "
    "Guideline bravo: check the charlie and golf fields of every ticket you read.... "
    "Guideline charlie: check the delta and hotel fields of every ticket you read... "
    "Guideline delta: check the echo and india fields of every ticket you read...... "
    "Guideline echo: check the foxtrot and juliett fields of every ticket you read.. "
    "Guideline foxtrot: check the golf and kilo fields of every ticket you read..... "
    "Guideline golf: check the hotel and lima fields of every ticket you read....... "
    "Guideline hotel: check the india and mike fields of every ticket you read...... "
    "Guideline india: check the juliett and november fields of every ticket you read "
    "Guideline juliett: check the kilo and oscar fields of every ticket you read.... "
)
OVER_BUDGET = AT_BUDGET + "Done"  # 804 characters: 201 estimated tokens

# Normalised lengths: 40 and 39 characters.
AT_LENGTH = "Write the reply in plain British English.\n- write the reply in plain British English"
BELOW_LENGTH = "Write a reply in formal British English.\nWrite a reply in formal British English."


def at_budget(history):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, system=AT_BUDGET, messages=history)


def over_budget(history):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, system=OVER_BUDGET, messages=history)


def repeats(history):
    first = claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, system=AT_LENGTH, messages=history)
    second = claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, system=AT_LENGTH, messages=history)
    return first, second


def short_repeats(history):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, system=BELOW_LENGTH, messages=history)

import anthropic

def summarise(text:
    return anthropic.Anthropic().messages.create(model="claude-haiku-4-5")

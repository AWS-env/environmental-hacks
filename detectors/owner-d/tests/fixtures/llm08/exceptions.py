# Synthetic LLM-08 fixture: models chosen at runtime, opaque requests and suppressions. Never executed.
import os

import anthropic

from .config import settings

claude = anthropic.Anthropic()
CLASSIFIER = os.environ.get("CLASSIFIER_MODEL", "claude-opus-5-5")


def from_parameter(review, model="claude-opus-5-5"):
    return claude.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": review}])


def from_environment(review):
    return claude.messages.create(model=CLASSIFIER, max_tokens=10, messages=[{"role": "user", "content": review}])


def from_settings(review):
    return claude.messages.create(model=settings.classifier_model, max_tokens=10, messages=[{"role": "user", "content": review}])


def conditional(review, hard):
    model = "claude-opus-5-5" if hard else MODELS["default"]
    return claude.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": review}])


def reassigned(review, hard):
    model = "claude-opus-5-5"
    if not hard:
        model = MODELS["default"]
    return claude.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": review}])


def opaque_kwargs(review, **options):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": review}], **options)


def opaque_extra_body(review):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": review}], extra_body={"x": 1})


def suppressed(review):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": review}])  # noqa: LLM-08


def not_suppressed(review):
    return claude.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": review}])  # noqa: E501


MODELS = {"default": os.environ.get("DEFAULT_MODEL")}

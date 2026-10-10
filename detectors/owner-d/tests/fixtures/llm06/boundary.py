# Synthetic LLM-06 fixture: repeated anchors and fan-outs warmed by an earlier one. Never executed.
import asyncio
import os

import anthropic

claude = anthropic.AsyncAnthropic()

if os.environ.get("FAST"):
    async def run_all(questions):
        return await asyncio.gather(*[claude.messages.create(model="claude-opus-5-5", max_tokens=64, system=[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}], messages=q) for q in questions])
else:
    async def run_all(questions):
        return await asyncio.gather(*[claude.messages.create(model="claude-opus-5-5", max_tokens=99, system=[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}], messages=q) for q in questions])


async def twice(first_batch, second_batch):
    one = await asyncio.gather(*[claude.messages.create(model="claude-opus-5-5", max_tokens=64, system=[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}], messages=q) for q in first_batch])
    two = await asyncio.gather(*[claude.messages.create(model="claude-opus-5-5", max_tokens=64, system=[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}], messages=q) for q in second_batch])
    return one + two


PROMPT = (
    "You review pull requests for a payments service. Check correctness first, then error handling, then tests. "
    "Flag any change that touches money amounts, currency conversion, rounding or idempotency keys. Quote the line. "
    "Ignore formatting and naming unless it hides a bug. Keep each comment under three sentences and actionable. "
    "Never approve a change that removes a test without replacing it. Ask for a migration plan for schema changes. "
    "Prefer small, reversible changes and say so when a change could be split. End with a one-line overall verdict. "
    "You review pull requests for a payments service. Check correctness first, then error handling, then tests. "
    "Flag any change that touches money amounts, currency conversion, rounding or idempotency keys. Quote the line. "
    "Ignore formatting and naming unless it hides a bug. Keep each comment under three sentences and actionable. "
    "Never approve a change that removes a test without replacing it. Ask for a migration plan for schema changes. "
    "Prefer small, reversible changes and say so when a change could be split. End with a one-line overall verdict. "
    "You review pull requests for a payments service. Check correctness first, then error handling, then tests. "
    "Flag any change that touches money amounts, currency conversion, rounding or idempotency keys. Quote the line. "
    "Ignore formatting and naming unless it hides a bug. Keep each comment under three sentences and actionable. "
    "Never approve a change that removes a test without replacing it. Ask for a migration plan for schema changes. "
    "Prefer small, reversible changes and say so when a change could be split. End with a one-line overall verdict. "
    "You review pull requests for a payments service. Check correctness first, then error handling, then tests. "
    "Flag any change that touches money amounts, currency conversion, rounding or idempotency keys. Quote the line. "
    "Ignore formatting and naming unless it hides a bug. Keep each comment under three sentences and actionable. "
    "Never approve a change that removes a test without replacing it. Ask for a migration plan for schema changes. "
    "Prefer small, reversible changes and say so when a change could be split. End with a one-line overall verdict. "
)

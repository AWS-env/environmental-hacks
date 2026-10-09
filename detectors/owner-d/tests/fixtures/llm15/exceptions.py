# Synthetic LLM-15 fixture: tool lists that cannot be counted statically, and noqa. Never executed.
import anthropic
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()


def from_caller(task, tools):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=tools, messages=task)


def forwarded(messages, **options):
    return client.chat.completions.create(model="gpt-5.5", messages=messages, **options)


async def from_mcp(session, task):
    listed = await session.list_tools()
    tools = [{"name": t.name, "input_schema": t.inputSchema} for t in listed.tools]
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=tools, messages=task)


def mutated(task):
    tools = list(TOOLS)
    tools.remove(TOOLS[0])
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=tools, messages=task)


def extra_body(messages, extra):
    return client.chat.completions.create(model="gpt-5.5", messages=messages, tools=TOOLS, extra_body=extra)


def suppressed(task):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=TOOLS, messages=task)  # noqa: LLM-15


def not_suppressed(task):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=TOOLS, messages=task)  # noqa: E501


TOOLS = [
    {"name": "get_order", "description": "Get order for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_orders", "description": "List orders for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "cancel_order", "description": "Cancel order for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "refund_order", "description": "Refund order for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "track_parcel", "description": "Track parcel for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "update_address", "description": "Update address for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_invoice", "description": "Get invoice for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_invoice", "description": "Send invoice for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apply_coupon", "description": "Apply coupon for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_coupons", "description": "List coupons for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_customer", "description": "Get customer for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "update_customer", "description": "Update customer for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "delete_customer", "description": "Delete customer for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "export_customer_data", "description": "Export customer data for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "reset_password", "description": "Reset password for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_products", "description": "List products for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_product", "description": "Get product for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_stock", "description": "Check stock for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "reserve_stock", "description": "Reserve stock for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "create_return_label", "description": "Create return label for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "open_ticket", "description": "Open ticket for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "close_ticket", "description": "Close ticket for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "escalate_ticket", "description": "Escalate ticket for the current customer.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_help_center", "description": "Search help center for the current customer.", "input_schema": {"type": "object", "properties": {}}},
]

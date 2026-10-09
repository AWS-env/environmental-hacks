# Synthetic LLM-15 fixture: large tool registries sent with every call. Never executed.
import json

import anthropic
import boto3
from openai import OpenAI

claude = anthropic.Anthropic()
client = OpenAI()
bedrock = boto3.client("bedrock-runtime")
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def plan(task):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, tools=TOOLS, messages=task)


def summarise(task):
    return claude.messages.create(model="claude-haiku-4-5", max_tokens=256, tools=TOOLS, messages=task)


def chat(messages):
    functions = [{"type": "function", "function": spec} for spec in SPECS.values()]
    return client.chat.completions.create(model="gpt-5.5", messages=messages, tools=functions)


def respond(text):
    return client.responses.create(model="gpt-5.5", input=text, tools=[*BASE, *EXTRA])


def converse(messages):
    config = {"tools": [{"toolSpec": {"name": tool["name"], "inputSchema": {"json": {}}}} for tool in TOOLS]}
    return bedrock.converse(
        modelId=BEDROCK_MODEL,
        messages=messages,
        toolConfig=config,
    )


def invoke(messages):
    body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 256, "tools": list(SPECS.values()), "messages": messages}
    return bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(body))


def injected(llm, messages):
    return llm.chat.completions.create(model="gpt-5.5", messages=messages, functions=list(SPECS.values()))


BASE = [{"type": "web_search"}, {"type": "file_search", "vector_store_ids": ["vs_1"]}]
EXTRA = [{"type": "function", "name": tool["name"], "parameters": {}} for tool in TOOLS]
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
SPECS = {
    "get_order": {"name": "get_order", "parameters": {"type": "object", "properties": {}}},
    "list_orders": {"name": "list_orders", "parameters": {"type": "object", "properties": {}}},
    "cancel_order": {"name": "cancel_order", "parameters": {"type": "object", "properties": {}}},
    "refund_order": {"name": "refund_order", "parameters": {"type": "object", "properties": {}}},
    "track_parcel": {"name": "track_parcel", "parameters": {"type": "object", "properties": {}}},
    "update_address": {"name": "update_address", "parameters": {"type": "object", "properties": {}}},
    "get_invoice": {"name": "get_invoice", "parameters": {"type": "object", "properties": {}}},
    "send_invoice": {"name": "send_invoice", "parameters": {"type": "object", "properties": {}}},
    "apply_coupon": {"name": "apply_coupon", "parameters": {"type": "object", "properties": {}}},
    "list_coupons": {"name": "list_coupons", "parameters": {"type": "object", "properties": {}}},
    "get_customer": {"name": "get_customer", "parameters": {"type": "object", "properties": {}}},
    "update_customer": {"name": "update_customer", "parameters": {"type": "object", "properties": {}}},
    "delete_customer": {"name": "delete_customer", "parameters": {"type": "object", "properties": {}}},
    "export_customer_data": {"name": "export_customer_data", "parameters": {"type": "object", "properties": {}}},
    "reset_password": {"name": "reset_password", "parameters": {"type": "object", "properties": {}}},
    "list_products": {"name": "list_products", "parameters": {"type": "object", "properties": {}}},
    "get_product": {"name": "get_product", "parameters": {"type": "object", "properties": {}}},
    "check_stock": {"name": "check_stock", "parameters": {"type": "object", "properties": {}}},
    "reserve_stock": {"name": "reserve_stock", "parameters": {"type": "object", "properties": {}}},
    "create_return_label": {"name": "create_return_label", "parameters": {"type": "object", "properties": {}}},
    "open_ticket": {"name": "open_ticket", "parameters": {"type": "object", "properties": {}}},
    "close_ticket": {"name": "close_ticket", "parameters": {"type": "object", "properties": {}}},
    "escalate_ticket": {"name": "escalate_ticket", "parameters": {"type": "object", "properties": {}}},
    "search_help_center": {"name": "search_help_center", "parameters": {"type": "object", "properties": {}}},
}

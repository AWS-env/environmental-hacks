# Synthetic LLM-15 fixture: small, subset or unrecognised tool lists. Never executed.
import anthropic
from groq import Groq
from openai import OpenAI
from twilio.rest import Client as TwilioClient

claude = anthropic.Anthropic()
client = OpenAI()
groq_client = Groq()
sms = TwilioClient()
SMALL = TOOLS[:3]


def few_tools(task):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=[TOOLS[0], TOOLS[1], TOOLS[2]], messages=task)


def subset(task, names):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=[t for t in TOOLS if t["name"] in names], messages=task)


def sliced(messages):
    return client.chat.completions.create(model="gpt-5.5", messages=messages, tools=SMALL)


def no_tools(messages):
    return client.responses.create(model="gpt-5.5", input=messages)


def other_sdk(messages):
    return groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages, tools=TOOLS)


def look_alike(text):
    return sms.messages.create(body=text, to="+15550100", tools=TOOLS)


def registry_only():
    return {tool["name"]: tool for tool in TOOLS}


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

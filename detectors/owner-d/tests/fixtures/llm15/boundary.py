# Synthetic LLM-15 fixture: count boundary, repeated anchors and a large definition. Never executed.
import anthropic

claude = anthropic.Anthropic()


def at_limit(task):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=TWENTY, messages=task)


def over_limit(task):
    first = claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=[*TWENTY, EXTRA], messages=task)
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=TWENTY + [EXTRA], messages=[first])


def few_but_large(task):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=[REPORT, EXTRA], messages=task)


EXTRA = {"name": "close_account", "description": "Close the account.", "input_schema": {"type": "object", "properties": {}}}
REPORT = {
    "name": "run_report",
    "description": (
        "Run one of the predefined business reports and return the rows as JSON. Reports cover orders, refunds, "
        "returns, stock levels, carrier performance, customer satisfaction, coupon usage and payment failures. "
        "Each report accepts a date range, a region, a sales channel and an optional product category. Use the "
        "smallest date range that answers the question, because large ranges are slow and return many rows. "
        "Never run more than one report per question unless the user explicitly asks for a comparison."
    ),
    "input_schema": {"type": "object", "properties": {"report": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}},
}
TWENTY = [
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
]

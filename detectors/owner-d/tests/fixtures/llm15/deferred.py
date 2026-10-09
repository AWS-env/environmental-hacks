# Synthetic LLM-15 fixture: large registries loaded on demand with tool search. Never executed.
import anthropic

claude = anthropic.Anthropic()
SEARCH = {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"}


def search_first(task):
    deferred = [{**tool, "defer_loading": True} for tool in TOOLS]
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=256, tools=[SEARCH, *deferred], messages=task)


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

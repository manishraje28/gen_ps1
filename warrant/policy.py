"""Tool policies: what kind of effect each tool has and which arguments are
*authority sinks* (arguments whose value decides who/what is affected).

The gate never trusts the planner's choice of these values: each one must be
backed by a warrant (see gate.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Effect(str, Enum):
    READ = "READ"          # no side effects
    WRITE = "WRITE"        # creates / modifies user data (calendar, files)
    BOOK = "BOOK"          # commits the user to a third party: hotel / car / restaurant reservations
    EGRESS = "EGRESS"      # data leaves the user (email, share, post, DM)
    PAY = "PAY"            # money moves
    DELETE = "DELETE"      # destroys data
    ACCOUNT = "ACCOUNT"    # changes credentials / identity
    FORM = "FORM"          # submits data to a web form (log in, pay, register) on a site


class Sink(str, Enum):
    RECIPIENT = "recipient"    # email address / user / channel that receives data
    IBAN = "iban"              # account that receives money
    AMOUNT = "amount"
    RESOURCE = "resource"      # file / email / event id being acted on
    URL = "url"
    SECRET = "secret"          # passwords
    PROFILE = "profile"        # identity fields (name, address)
    TARGET = "target"          # booking target (hotel, restaurant, company)
    RECORD = "record"          # must come from the user (typed) or their own records: ssh keys, order ids


@dataclass(frozen=True)
class ToolPolicy:
    effect: Effect
    sinks: dict[str, Sink] = field(default_factory=dict)
    # Output of a trusted tool comes from the user's own records (contacts,
    # account history). Free-text inside it is still audited span by span.
    trusted_source: bool = False


P = ToolPolicy
E = Effect
S = Sink

POLICIES: dict[str, ToolPolicy] = {
    # ---------------- demo environment (Acme workspace) ----------------
    "list_emails": P(E.READ),
    "read_email": P(E.READ),
    "search_emails": P(E.READ),
    "send_email": P(E.EGRESS, {"recipients": S.RECIPIENT, "cc": S.RECIPIENT, "bcc": S.RECIPIENT, "attachments": S.RESOURCE}),
    "list_files": P(E.READ),
    "read_file": P(E.READ),
    "share_file": P(E.EGRESS, {"file_id": S.RESOURCE, "email": S.RECIPIENT}),
    "delete_file": P(E.DELETE, {"file_id": S.RESOURCE}),
    "get_balance": P(E.READ, trusted_source=True),
    "get_transactions": P(E.READ, trusted_source=True),
    "send_money": P(E.PAY, {"recipient_iban": S.IBAN, "amount": S.AMOUNT, "recipient": S.IBAN}),
    "search_contacts": P(E.READ, trusted_source=True),
    "create_calendar_event": P(E.WRITE, {"participants": S.RECIPIENT}),
    # ---------------- AgentDojo: banking ----------------
    "get_iban": P(E.READ, trusted_source=True),
    "schedule_transaction": P(E.PAY, {"recipient": S.IBAN, "amount": S.AMOUNT}),
    "update_scheduled_transaction": P(E.PAY, {"recipient": S.IBAN, "amount": S.AMOUNT}),
    "get_most_recent_transactions": P(E.READ, trusted_source=True),
    "get_scheduled_transactions": P(E.READ, trusted_source=True),
    "get_user_info": P(E.READ, trusted_source=True),
    "update_password": P(E.ACCOUNT, {"password": S.SECRET}),
    "update_user_info": P(E.ACCOUNT, {"first_name": S.PROFILE, "last_name": S.PROFILE, "street": S.PROFILE, "city": S.PROFILE}),
    # ---------------- AgentDojo: slack ----------------
    "get_channels": P(E.READ, trusted_source=True),
    "add_user_to_channel": P(E.WRITE, {"user": S.RECIPIENT, "channel": S.RECIPIENT}),
    "read_channel_messages": P(E.READ),
    "read_inbox": P(E.READ),
    "send_direct_message": P(E.EGRESS, {"recipient": S.RECIPIENT}),
    "send_channel_message": P(E.EGRESS, {"channel": S.RECIPIENT}),
    "get_users_in_channel": P(E.READ, trusted_source=True),
    "invite_user_to_slack": P(E.WRITE, {"user": S.RECIPIENT, "user_email": S.RECIPIENT}),
    "remove_user_from_slack": P(E.DELETE, {"user": S.RECIPIENT}),
    "get_webpage": P(E.READ, {"url": S.URL}),  # URLs can smuggle data out
    "post_webpage": P(E.EGRESS, {"url": S.URL}),
    # ---------------- AgentDojo: workspace ----------------
    "delete_email": P(E.DELETE, {"email_id": S.RESOURCE}),
    "get_unread_emails": P(E.READ),
    "get_sent_emails": P(E.READ),
    "get_received_emails": P(E.READ),
    "get_draft_emails": P(E.READ),
    "search_contacts_by_name": P(E.READ, trusted_source=True),
    "search_contacts_by_email": P(E.READ, trusted_source=True),
    "get_current_day": P(E.READ, trusted_source=True),
    "search_calendar_events": P(E.READ),
    "get_day_calendar_events": P(E.READ),
    "cancel_calendar_event": P(E.DELETE, {"event_id": S.RESOURCE}),
    "reschedule_calendar_event": P(E.WRITE, {"event_id": S.RESOURCE}),
    "add_calendar_event_participants": P(E.WRITE, {"event_id": S.RESOURCE, "participants": S.RECIPIENT}),
    "append_to_file": P(E.WRITE, {"file_id": S.RESOURCE}),
    "search_files_by_filename": P(E.READ),
    "create_file": P(E.WRITE),
    "get_file_by_id": P(E.READ),
    "search_files": P(E.READ),
    # ---------------- AgentDojo: travel ----------------
    "get_user_information": P(E.READ, trusted_source=True),
    "reserve_hotel": P(E.BOOK, {"hotel": S.TARGET}),
    "reserve_car_rental": P(E.BOOK, {"company": S.TARGET}),
    "reserve_restaurant": P(E.BOOK, {"restaurant": S.TARGET}),
    # ---------------- AgentDyn (github / shopping / dailylife) ----------------
    # Cross-checked against ROPE's audited parameter table (github.com/xhOwenMa/ROPE, scopes/_floor).
    # Logins and one-time-password steps are procedural: AgentDyn pages legitimately ask for them
    # ("please log in first"), so they are READ (no user data changes); every state change after them
    # is still gated.
    "login_github_account": P(E.READ),
    "verify_github_account": P(E.READ),
    "login_shopping_account": P(E.READ),
    "verify_shopping_account": P(E.READ),
    "verify_transaction": P(E.READ),
    "browse_webpage": P(E.READ, {"url": S.URL}),  # URLs can smuggle data out
    "input_to_webpage": P(E.FORM, {"url": S.URL}),
    "get_github_linked_unread_emails": P(E.READ),
    "get_shopping_linked_unread_emails": P(E.READ),
    "get_shopping_linked_received_emails": P(E.READ),
    "get_logged_in_github_user_information": P(E.READ, trusted_source=True),
    "get_shopping_account_user_information": P(E.READ, trusted_source=True),
    "git_get_linked_ssh_keys": P(E.READ, trusted_source=True),
    "view_order_history": P(E.READ, trusted_source=True),
    "view_cart": P(E.READ, trusted_source=True),
    "cart_total": P(E.READ),
    "view_available_discounts": P(E.READ, trusted_source=True),
    "search_product": P(E.READ),
    "get_invoice": P(E.READ),
    # local files
    "list_directory": P(E.READ),
    # Local staging: nothing outside the user's own machine or cart changes. They are READ for the
    # effect check, but their URL / id arguments are still checked (a download URL can carry data out).
    "create_dir": P(E.READ),
    "copy_node": P(E.WRITE, {"source_path": S.RESOURCE}),
    "move_node": P(E.WRITE, {"source_path": S.RESOURCE}),
    "delete_node": P(E.DELETE, {"path": S.RESOURCE}),
    "download_file_through_url": P(E.READ, {"url": S.URL}),
    "download_file_through_id": P(E.READ, {"file_id": S.RESOURCE}),
    # github
    "git_create_repo": P(E.WRITE),
    "git_delete_repo": P(E.DELETE, {"repo_name": S.RESOURCE}),
    "git_transfer_repo_ownership": P(E.EGRESS, {"repo_name": S.RESOURCE, "new_owner_username": S.RECIPIENT, "new_owner_email": S.RECIPIENT}),
    "git_clone": P(E.WRITE, {"repo_name": S.RESOURCE}),
    "git_invite_collaborators": P(E.WRITE, {"repo_name": S.RESOURCE, "collaborator_email": S.RECIPIENT}),
    "git_push": P(E.WRITE, {"local_path": S.RESOURCE}),
    "git_pull": P(E.WRITE, {"repo_name": S.RESOURCE}),
    "git_issue": P(E.EGRESS, {"repo_name": S.RECIPIENT}),
    "git_star": P(E.WRITE, {"repo_name": S.RESOURCE}),
    "git_unstar": P(E.WRITE, {"repo_name": S.RESOURCE}),
    "git_add_ssh_key": P(E.ACCOUNT, {"ssh_key": S.RECORD}),
    "git_delete_ssh_key": P(E.ACCOUNT, {"ssh_key": S.RECORD}),
    "update_github_account_password": P(E.ACCOUNT, {"password": S.SECRET}),
    # shopping
    # The cart is staging; money moves only at checkout (PAY).
    "cart_add_product": P(E.READ, {"product_id": S.RESOURCE}),
    "cart_remove_product": P(E.READ, {"product_id": S.RESOURCE}),
    "clear_cart": P(E.READ),
    "checkout_selected_cart": P(E.PAY, {"product_ids": S.RESOURCE}),
    "refund_order": P(E.PAY, {"order_id": S.RECORD}),
    "update_shopping_account_password": P(E.ACCOUNT, {"password": S.SECRET}),
    "update_shopping_account_user_info": P(E.ACCOUNT, {
        "first_name": S.PROFILE, "last_name": S.PROFILE, "id_number": S.PROFILE, "email": S.PROFILE,
        "phone_number": S.PROFILE, "address": S.PROFILE, "passport_number": S.PROFILE, "bank_account_number": S.IBAN}),
}

_READ_PREFIXES = ("get_", "list_", "read_", "search_", "check_")


def get_policy(tool: str) -> ToolPolicy:
    if tool in POLICIES:
        return POLICIES[tool]
    if tool.startswith(_READ_PREFIXES):
        return ToolPolicy(Effect.READ)
    # Unknown side-effecting tool: fail closed, it must be explicitly in the contract.
    return ToolPolicy(Effect.WRITE)


def tool_catalog(tool_names: list[str]) -> list[dict]:
    """Effect-annotated tool list shown to the contract compiler (no data, just names)."""
    out = []
    for name in tool_names:
        pol = get_policy(name)
        out.append({"tool": name, "effect": pol.effect.value, "sink_args": {k: v.value for k, v in pol.sinks.items()}})
    return out

"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Retail online-banking surface: an account index and a per-account transaction
ledger, modelled on a real bank dashboard.

Deliberately read-only. Nothing an agent can click mutates the seeded state,
so ``/openbanking_all`` is byte-identical at episode start and end. That is
load-bearing: ``AppStateComparison`` builds every task's target by deep-copying
the *initial* cross-app state, so an app that drifted on its own (auto
timestamps, nondeterministic row order) would inject a spurious diff into every
unrelated todo/calendar task's reward. Scorable work therefore lands in another
app -- the bank supplies the information-retrieval half of a ``CompositeTask``
and the delta shows up in todo/calendar/messenger.
"""

from fasthtml.common import *
from dataclasses import dataclass
import json
from typing import List, Optional
from src.open_apps.apps.start_page.helper import create_logo_header
from src.open_apps.frontend import local_hdrs
from src.open_apps.theme import theme_style


@dataclass
class Account:
    id: int
    name: str
    holder: str
    # Stored in full; the page masks all but the last four digits until the
    # figure is clicked. Kept as strings, not ints, so leading zeros survive.
    account_number: str
    routing_number: str
    available_balance: float
    present_balance: float
    available_credit: float


@dataclass
class Transaction:
    id: int
    account_id: int
    # Ledger order as configured. Preserved explicitly because a statement is
    # ordered newest-first by posting, which the human-readable `date` string
    # ("Aug 25, 2026", "2026年8月24日") cannot be sorted on.
    position: int
    date: Optional[str]  # None => pending, rendered under `pending_label`
    description: str
    type: str
    amount: float
    balance: Optional[float]  # None => no posted balance yet (pending)


app, rt = fast_app(default_hdrs=False, hdrs=local_hdrs())
logo_title_container = None
accounts = None
transactions = None

# Static, theme-agnostic component styles. All colors/fonts are design tokens
# resolved per-request via `theme_style()`, so this block never needs rebuilding
# when the theme or app config changes.
#
# The banking-specific tokens (--color-header-bg and friends) are only defined
# by the `openbanking*` themes, so every use here carries a fallback to a token
# from the shared 15-token contract. That keeps the app legible under `default`,
# `dark`, `mono`, `challenging_font`, `solarized`, `material` and `bootstrap`
# instead of collapsing to unstyled text.
styles = Style("""
    body {
        font-family: var(--font-family);
        font-size: var(--font-size-base);
        color: var(--color-fg);
        background-color: var(--color-bg);
        margin: 0;
    }
    a { color: var(--color-link, var(--color-accent)); text-decoration: none; }
    a:hover { text-decoration: underline; }

    .ob-masthead {
        display: flex;
        align-items: center;
        gap: 1rem;
        background-color: var(--color-header-bg, var(--color-primary));
        color: var(--color-header-fg, var(--color-on-primary));
        padding: 0.75rem 1.5rem;
    }
    /* create_logo_header sets `margin-bottom: 1rem` inline for the standalone
       page-header case; inside the masthead bar that leaves a gap under the
       wordmark. A stylesheet `!important` outranks a non-important inline
       declaration, so this wins without forking the shared helper. */
    .ob-masthead > div, .ob-masthead > a {
        margin-bottom: 0 !important;
        color: var(--color-header-fg, var(--color-on-primary)) !important;
    }
    .ob-masthead-spacer { flex: 1 1 auto; }
    .ob-menu-icon {
        font-size: 1.5rem;
        line-height: 1;
        color: var(--color-header-fg, var(--color-on-primary));
    }
    .ob-masthead-actions { display: flex; align-items: center; gap: 1rem; }
    .ob-icon-btn {
        font-size: 1.1rem;
        color: var(--color-header-fg, var(--color-on-primary));
        background: none;
        border: none;
        padding: 0.25rem;
        cursor: pointer;
    }
    .ob-ghost-btn {
        border: 1px solid var(--color-header-fg, var(--color-on-primary));
        border-radius: var(--radius);
        padding: 0.4rem 0.9rem;
        font-weight: 600;
        background-color: var(--color-bg);
        color: var(--color-header-bg, var(--color-primary));
    }
    .ob-signout { color: var(--color-header-fg, var(--color-on-primary)); }

    .ob-page { padding: 1.5rem; }
    .ob-summary { background-color: var(--color-bg); padding: 1.5rem; }
    .ob-account-name { font-size: 1.05rem; margin: 0; font-weight: 600; }
    .ob-account-holder { color: var(--color-muted); margin: 0.25rem 0 1rem 0; }
    .ob-balance { font-size: 2.75rem; font-weight: 400; margin: 0; }
    /* Dotted underline marks the figures the real dashboard footnotes. */
    .ob-balance-label {
        border-bottom: 1px dotted var(--color-muted);
        display: inline-block;
        margin-bottom: 1.5rem;
    }
    .ob-summary-head { display: flex; justify-content: space-between; align-items: flex-start; }
    .ob-numbers { display: flex; flex-direction: column; gap: 0.75rem; text-align: right; }
    .ob-number-row { display: flex; flex-direction: column; align-items: flex-end; }
    /* The figure is the control, so it has to read as one: link-coloured, with
       the dotted underline this dashboard uses for click-to-disclose text. */
    .ob-number-toggle {
        background: none;
        border: none;
        padding: 0;
        margin: 0;
        width: auto;
        font-family: inherit;
        font-size: 1rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        color: var(--color-link, var(--color-accent));
        border-bottom: 1px dotted var(--color-link, var(--color-accent));
        border-radius: 0;
        cursor: pointer;
    }
    .ob-number-toggle:hover { text-decoration: none; opacity: 0.8; }
    .ob-figures { display: flex; gap: 4rem; flex-wrap: wrap; }
    .ob-figure-value { font-weight: 600; }
    .ob-figure-label { color: var(--color-muted); }

    .ob-band { background-color: var(--color-surface); padding: 1.5rem; }
    .ob-band-heading { margin: 0 0 1rem 0; font-size: 1.1rem; font-weight: 600; }
    .ob-card {
        background-color: var(--color-bg);
        border-radius: var(--radius);
        border: 1px solid var(--color-border);
        padding: 1rem 1.25rem;
    }
    .ob-controls {
        display: flex;
        align-items: center;
        gap: 1rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }
    .ob-controls label { margin: 0; }
    .ob-controls select, .ob-controls input { margin: 0; }
    .ob-controls select { min-width: 20rem; }

    .ob-table { width: 100%; border-collapse: collapse; }
    .ob-table th {
        text-align: left;
        border-bottom: 2px solid var(--color-rule, var(--color-fg));
        padding: 0.5rem 0.75rem;
        font-weight: 600;
    }
    .ob-table td {
        padding: 0.75rem;
        border-bottom: 1px solid var(--color-border);
        vertical-align: top;
    }
    .ob-num { text-align: right; white-space: nowrap; }
    .ob-credit { color: var(--color-credit, var(--color-accent)); font-weight: 600; }
    .ob-debit { color: var(--color-debit, var(--color-fg)); font-weight: 700; }
    .ob-pending td { color: var(--color-muted); }

    .ob-txn-card {
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        padding: 0.75rem 1rem;
        margin-bottom: 0.75rem;
    }
    .ob-txn-card-desc { margin-bottom: 0.5rem; }
    .ob-txn-card-meta {
        display: flex;
        gap: 1.5rem;
        flex-wrap: wrap;
        color: var(--color-muted);
        font-size: 0.9rem;
    }

    .ob-see-more { display: block; text-align: center; margin-top: 1rem; font-weight: 600; }
    .ob-account-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid var(--color-border);
        padding: 1rem 0;
    }
    .ob-account-row:last-child { border-bottom: none; }
    .ob-empty { color: var(--color-muted); padding: 1rem 0; }
""")


def set_environment(config):
    """Set environment variables for the openbanking app"""
    global app, logo_title_container, accounts, transactions
    app.config = config
    db = database(config.openbanking.database_path)
    accounts = db.create(Account, pk="id")
    transactions = db.create(Transaction, pk="id")

    print("Populating initial accounts and transactions from config")
    txn_id = 0
    for account_id, account_cfg in enumerate(config.openbanking.accounts):
        accounts.insert(
            Account(
                id=account_id,
                name=account_cfg.name,
                holder=account_cfg.holder,
                account_number=str(account_cfg.account_number),
                routing_number=str(account_cfg.routing_number),
                available_balance=float(account_cfg.available_balance),
                present_balance=float(account_cfg.present_balance),
                available_credit=float(account_cfg.available_credit),
            )
        )
        for position, txn_cfg in enumerate(account_cfg.transactions):
            balance = txn_cfg.balance
            transactions.insert(
                Transaction(
                    id=txn_id,
                    account_id=account_id,
                    position=position,
                    date=txn_cfg.date,
                    description=txn_cfg.description,
                    type=txn_cfg.type,
                    amount=float(txn_cfg.amount),
                    balance=None if balance is None else float(balance),
                )
            )
            txn_id += 1

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.openbanking,
        base_url="/openbanking",
        current_file_path=__file__,
    )


def openbanking_theme():
    """The active theme's `:root` token block, resolved per-request so live
    `reconfigure` theme swaps take effect."""
    return theme_style(app.config, "openbanking")


def cfg():
    """The app's own config node."""
    return app.config.openbanking


def current_layout():
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.openbanking, "layout", "default")


def mask_number(value: str, visible: int = 4) -> str:
    """Hide all but the trailing ``visible`` digits behind bullets.

    Bullets rather than asterisks to match what the real dashboard prints, and
    one bullet per hidden digit rather than a fixed-width run so the masked
    figure still reveals its length -- an account number and a routing number
    are told apart that way before either is clicked.
    """
    value = str(value)
    if len(value) <= visible:
        return value
    return "•" * (len(value) - visible) + value[-visible:]


def fmt_money(value: Optional[float]) -> str:
    """Render a signed currency figure, or an em dash when there is none.

    Uses an ASCII hyphen-minus rather than the typographic minus the real
    dashboard prints: agents read these figures back out of the accessibility
    tree and into task answers, and U+2212 would not round-trip through a
    string comparison against a plainly-typed goal.
    """
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def account_rows() -> List[Account]:
    return sorted(accounts(), key=lambda a: a.id)


def txns_for(account_id: int) -> List[Transaction]:
    return sorted(
        (t for t in transactions() if t.account_id == account_id),
        key=lambda t: t.position,
    )


def get_account(account_id: int) -> Optional[Account]:
    for account in account_rows():
        if account.id == account_id:
            return account
    return None


# ---------------------------------------------------------------------------
# Filtering.
#
# The "Showing" options are configurable strings (and translated in the
# german/mandarin variants), so the predicate is keyed off the option's
# *index* in `showing_options`, not its text.


def filter_txns(
    txns: List[Transaction], showing_index: int, query: str
) -> List[Transaction]:
    if showing_index == 1:  # deposits / money in
        txns = [t for t in txns if t.amount > 0]
    elif showing_index == 2:  # withdrawals / money out
        txns = [t for t in txns if t.amount < 0]
    if query:
        needle = query.strip().lower()
        txns = [
            t
            for t in txns
            if needle in t.description.lower() or needle in t.type.lower()
        ]
    return txns


# ---------------------------------------------------------------------------
# Components.


def masthead():
    c = cfg()
    actions = [
        Button(label, cls="ob-ghost-btn", type="button") for label in c.nav_actions
    ]
    return Div(
        Span("☰", cls="ob-menu-icon"),
        logo_title_container,
        Span(cls="ob-masthead-spacer"),
        Div(
            Button("⌕", cls="ob-icon-btn", type="button", aria_label=c.search_label),
            Button("?", cls="ob-icon-btn", type="button"),
            Button("◉", cls="ob-icon-btn", type="button"),
            *actions,
            Span(c.sign_out_label, cls="ob-signout"),
            cls="ob-masthead-actions",
        ),
        cls="ob-masthead",
    )


def figure(value: str, label: str):
    return Div(
        Div(value, cls="ob-figure-value"),
        Div(label, cls="ob-figure-label"),
    )


def number_row(account: Account, kind: str, revealed: bool, other_revealed: bool):
    """One label + click-to-reveal figure.

    The figure is the control -- clicking it toggles between the masked and
    full value. Each row carries the *other* row's current state in its query
    string so a swap of the shared container does not silently re-hide the
    row the agent already opened.
    """
    c = cfg()
    label = c.account_number_label if kind == "account" else c.routing_number_label
    value = account.account_number if kind == "account" else account.routing_number
    flags = {
        "account": other_revealed if kind == "routing" else not revealed,
        "routing": other_revealed if kind == "account" else not revealed,
    }
    query = f"account={int(flags['account'])}&routing={int(flags['routing'])}"
    return Div(
        Div(label, cls="ob-figure-label"),
        Button(
            value if revealed else mask_number(value),
            type="button",
            cls="ob-number-toggle",
            title=c.hide_number_label if revealed else c.show_number_label,
            aria_expanded="true" if revealed else "false",
            hx_get=f"/openbanking/accounts/{account.id}/numbers?{query}",
            hx_target="#ob-numbers",
            hx_swap="outerHTML",
        ),
        id=f"ob-number-{kind}",
        cls="ob-number-row",
    )


def number_panel(
    account: Account, show_account: bool = False, show_routing: bool = False
):
    """The two stacked, independently revealable figures."""
    return Div(
        number_row(account, "account", show_account, show_routing),
        number_row(account, "routing", show_routing, show_account),
        id="ob-numbers",
        cls="ob-numbers",
    )


def account_summary(
    account: Account, show_account: bool = False, show_routing: bool = False
):
    c = cfg()
    return Div(
        Div(
            Div(
                H2(account.name, cls="ob-account-name"),
                P(account.holder, cls="ob-account-holder"),
            ),
            number_panel(account, show_account, show_routing),
            cls="ob-summary-head",
        ),
        P(fmt_money(account.available_balance), cls="ob-balance"),
        Div(c.available_balance_label, cls="ob-balance-label"),
        Div(
            figure(fmt_money(account.present_balance), c.present_balance_label),
            figure(fmt_money(account.available_credit), c.available_credit_label),
            figure(
                fmt_money(account.present_balance + account.available_credit),
                c.available_plus_credit_label,
            ),
            cls="ob-figures",
        ),
        cls="ob-summary",
    )


def amount_cell(txn: Transaction):
    cls = "ob-num ob-debit" if txn.amount < 0 else "ob-num ob-credit"
    return Td(fmt_money(txn.amount), cls=cls)


def txn_table(txns: List[Transaction]):
    c = cfg()
    labels = c.column_labels
    head = Thead(
        Tr(
            Th(labels.date),
            Th(labels.description),
            Th(labels.type),
            Th(labels.amount, cls="ob-num"),
            Th(labels.balance, cls="ob-num"),
        )
    )
    rows = []
    for txn in txns:
        rows.append(
            Tr(
                Td(txn.date if txn.date else c.pending_label),
                Td(txn.description),
                Td(txn.type),
                amount_cell(txn),
                Td(fmt_money(txn.balance), cls="ob-num"),
                cls="ob-pending" if txn.date is None else None,
                id=f"txn-{txn.id}",
            )
        )
    return Table(head, Tbody(*rows), cls="ob-table")


def txn_cards(txns: List[Transaction]):
    c = cfg()
    labels = c.column_labels
    cards = []
    for txn in txns:
        cards.append(
            Div(
                Div(txn.description, cls="ob-txn-card-desc"),
                Div(
                    Span(f"{labels.date}: {txn.date if txn.date else c.pending_label}"),
                    Span(f"{labels.type}: {txn.type}"),
                    Span(f"{labels.amount}: {fmt_money(txn.amount)}"),
                    Span(f"{labels.balance}: {fmt_money(txn.balance)}"),
                    cls="ob-txn-card-meta",
                ),
                cls="ob-txn-card",
                id=f"txn-{txn.id}",
            )
        )
    return Div(*cards)


def ledger(account: Account, showing_index: int, query: str):
    """The swappable part of the transactions panel."""
    c = cfg()
    txns = filter_txns(txns_for(account.id), showing_index, query)
    if not txns:
        body = Div(c.no_results_label, cls="ob-empty")
    elif current_layout() == "card_list":
        body = txn_cards(txns)
    else:
        body = txn_table(txns)
    return Div(
        body,
        A(
            c.see_more_label,
            href=f"/openbanking/accounts/{account.id}",
            cls="ob-see-more",
        ),
        id="ob-ledger",
    )


def transactions_panel(account: Account, showing_index: int, query: str):
    c = cfg()
    target = f"/openbanking/accounts/{account.id}/ledger"
    options = [
        Option(label, value=str(i), selected=(i == showing_index))
        for i, label in enumerate(c.showing_options)
    ]
    controls = Div(
        Label(c.showing_label, fr="showing"),
        Select(
            *options,
            id="showing",
            name="showing",
            hx_get=target,
            hx_target="#ob-ledger",
            hx_swap="outerHTML",
            hx_include="#ob-search",
            hx_push_url="true",
        ),
        Input(
            id="ob-search",
            name="q",
            type="search",
            value=query,
            placeholder=c.search_placeholder,
            aria_label=c.search_label,
            hx_get=target,
            hx_target="#ob-ledger",
            hx_swap="outerHTML",
            hx_include="#showing",
            hx_trigger="search, keyup changed delay:300ms",
            hx_push_url="true",
        ),
        cls="ob-controls",
    )
    return Div(
        H2(c.transactions_heading, cls="ob-band-heading"),
        Div(controls, ledger(account, showing_index, query), cls="ob-card"),
        cls="ob-band",
    )


def page(*content):
    return Div(openbanking_theme(), styles, masthead(), *content)


# ---------------------------------------------------------------------------
# Routes.


@rt("/openbanking")
def openbanking_index():
    c = cfg()
    rows = [
        Div(
            Div(
                Div(account.name, style="font-weight: 600;"),
                Div(account.holder, cls="ob-figure-label"),
            ),
            Div(
                Div(fmt_money(account.available_balance), cls="ob-num ob-figure-value"),
                Div(c.available_balance_label, cls="ob-num ob-figure-label"),
            ),
            cls="ob-account-row",
            id=f"account-{account.id}",
            hx_get=f"/openbanking/accounts/{account.id}",
            hx_push_url="true",
            hx_target="body",
            style="cursor: pointer;",
        )
        for account in account_rows()
    ]
    return page(
        Div(
            H2(c.accounts_heading, cls="ob-band-heading"),
            Div(*rows, cls="ob-card")
            if rows
            else Div(c.no_results_label, cls="ob-empty"),
            A(
                "Return to List of Apps",
                href="/",
                role="button",
                cls="contrast",
                style="margin-top: 1rem;",
            ),
            cls="ob-page",
        )
    )


@rt("/openbanking/accounts/{account_id}")
def openbanking_account_detail(
    account_id: int, showing: int = 0, q: str = "", account: int = 0, routing: int = 0
):
    acct = get_account(account_id)
    if acct is None:
        return page(Div(cfg().no_results_label, cls="ob-page"))
    return page(
        account_summary(acct, bool(account), bool(routing)),
        transactions_panel(acct, showing, q),
        Div(
            A(cfg().back_to_accounts_label, href="/openbanking"),
            " · ",
            A("Return to List of Apps", href="/"),
            cls="ob-page",
        ),
    )


@rt("/openbanking/accounts/{account_id}/ledger")
def openbanking_account_ledger(account_id: int, showing: int = 0, q: str = ""):
    """htmx partial: the filtered/searched ledger only."""
    account = get_account(account_id)
    if account is None:
        return Div(cfg().no_results_label, cls="ob-empty", id="ob-ledger")
    return ledger(account, showing, q)


@rt("/openbanking/accounts/{account_id}/numbers")
def openbanking_account_numbers(account_id: int, account: int = 0, routing: int = 0):
    """htmx partial: the account/routing figures at their requested visibility.

    Disclosure only -- reveals nothing that is not already seeded and writes
    nothing back, so the app stays read-only and ``/openbanking_all`` stays
    byte-stable regardless of what the agent has clicked open.
    """
    acct = get_account(account_id)
    if acct is None:
        return Div(cfg().no_results_label, cls="ob-empty", id="ob-numbers")
    return number_panel(acct, bool(account), bool(routing))


@app.get("/openbanking_all")
def get_all():
    """Used for rewards.

    Sorted by primary key on both sub-lists so the payload is byte-stable
    across calls -- see the module docstring on why drift here would corrupt
    every other app's reward.
    """
    payload = {
        "accounts": [account.__dict__ for account in account_rows()],
        "transactions": [
            txn.__dict__ for txn in sorted(transactions(), key=lambda t: t.id)
        ],
    }
    return Response(json.dumps(payload), headers={"Content-Type": "application/json"})


def get_openbanking_routes():
    return app.routes


if __name__ == "__main__":
    print("Warning: Running openbanking app in standalone mode")
    app.routes = get_openbanking_routes()
    serve()

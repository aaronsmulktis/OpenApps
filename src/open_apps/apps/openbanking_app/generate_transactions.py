"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Entry point: ``python -m open_apps.apps.openbanking_app.generate_transactions``
/ ``openbanking-gen-txns``.

Generates extra ledger rows for one seeded OpenBanking account and either
prints them or splices them into the content file they came from.

Why a generator rather than hand-written YAML: the ledger has to stay
*arithmetically coherent* to be worth reading. Each row's ``balance`` is the
running balance after that posting, so a row appended to the end has to chain
off the oldest existing one -- ``balance[i+1] = balance[i] - amount[i]`` -- and
its date has to fall on or before the oldest existing date. Getting that wrong
by hand is easy and invisible: the page still renders, and only an agent asked
to reconcile the account notices.

Two properties the app depends on are preserved by construction:

* **Determinism.** ``--seed`` fixes the whole draw, so re-running with the same
  arguments produces byte-identical YAML. The app seeds its tables from config
  at startup and ``/openbanking_all`` must be byte-stable (see
  ``openbanking_app/main.py``), so a generator that drifted between runs would
  make the config itself a source of nondeterminism.
* **Config, not database.** The output is seed YAML. Nothing here writes to
  ``openbanking.db``: the app is read-only at runtime, and mutating the live
  database out from under it would inject a diff into every unrelated
  todo/calendar task's reward.

What it will *not* check for you: ``config/tasks/openbanking.yaml`` reads
specific figures off specific accounts, and some of its goals quantify over
the ledger ("exactly two transactions of type 'Card'", "the one of type 'Card'
with the largest amount"). Use ``--types`` and ``--max-amount`` to stay inside
those, and re-run ``pytest tests/test_openbanking.py`` after writing.

Examples::

    # Preview three rows for the checking account
    openbanking-gen-txns --account "BUS COMPLETE CHK (...5555)" --count 3

    # Write them into the German variant, keeping the amounts small
    openbanking-gen-txns --content german --account "BUS SELECT SAVINGS (...8891)" \\
        --count 2 --max-amount 500 --in-place
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

from open_apps import config_dir

CONTENT_DIR = config_dir() / "apps" / "openbanking" / "content"

# The date format the seeded ledger prints. The translated variants use their
# own ("15. Sep. 2026", "2026年9月15日"), so a generated row for those is dated
# with `--date-format` -- or with this one, which at least sorts correctly and
# is obvious to fix by hand.
DATE_FORMAT = "%b %d, %Y"

# Business days between generated postings, drawn per row. Small enough that a
# handful of rows stay inside one statement cycle.
DAY_STEP = (1, 6)


@dataclass(frozen=True)
class Merchant:
    """One row's wording and type, and the sign its amount takes.

    `credit` is what makes a draw coherent: "PAYROLL" has to be money out of a
    checking account and "PAYMENT THANK YOU" money off a card, so the sign
    belongs to the merchant, not to a coin flip.
    """

    description: str
    type: str
    credit: bool


# Deposit-account wording, modelled on the seeded rows: real statements print
# the originator block, not a tidy merchant name.
DEPOSIT_MERCHANTS = [
    Merchant(
        "ORIG CO NAME:{payer} ORIG ID:{oid} DESC DATE:{yymmdd} CO ENTRY DESCR:"
        "SETTLEMENT SEC:CCD TRACE#:{trace} EED:{yymmdd} IND ID: IND NAME:{holder} "
        "TRN: {trn}",
        "ACH credit",
        True,
    ),
    Merchant(
        "ORIG CO NAME:{payer} ORIG ID:{oid} DESC DATE:{yymmdd} CO ENTRY DESCR:"
        "PAYABLE SEC:CCD TRACE#:{trace} EED:{yymmdd} IND ID: IND NAME:{holder} "
        "TRN: {trn}",
        "ACH debit",
        False,
    ),
    Merchant(
        "DEPOSIT REMOTE CAPTURE BATCH {batch} ITEM COUNT {items} PROCESSED "
        "{mmdd} 09:41:12 CT",
        "Deposit",
        True,
    ),
    Merchant("TRANSFER FROM CHK ...5555", "Transfer", True),
    Merchant("TRANSFER TO CHK ...5555", "Transfer", False),
    Merchant("{merchant} {domain} TX {mmdd} (...{tail})", "Card", False),
    Merchant("MONTHLY MAINTENANCE FEE", "Fee", False),
    Merchant("INTEREST PAYMENT", "Interest", True),
]

# Card wording. A card ledger is charges, payments and finance costs; there is
# no routing traffic on it.
CARD_MERCHANTS = [
    Merchant("{merchant} {domain} {state} {mmdd} (...{tail})", "Card", False),
    Merchant("PAYMENT THANK YOU", "Payment", True),
    Merchant("ANNUAL MEMBERSHIP FEE", "Fee", False),
    Merchant("INTEREST CHARGE ON PURCHASES", "Interest", False),
    Merchant("RETURN CREDIT {merchant} (...{tail})", "Refund", True),
]

# Invented businesses. Deliberately generic and clearly synthetic -- these end
# up in a public config file.
MERCHANT_NAMES = [
    "CLOUD HOST LLC",
    "PRINT SHOP 44",
    "FLEET UNIFORM SUPPLY",
    "NORTHSIDE COURIER",
    "DEPOT HARDWARE CO",
    "BLUE LINE TELECOM",
    "PARKVIEW CATERING",
    "SUMMIT OFFICE PARK",
]
MERCHANT_DOMAINS = [
    "CLOUDHOST.IO",
    "PRINTSHOP44.COM",
    "FLEETUNI.COM",
    "NSCOURIER.COM",
    "DEPOTHW.COM",
    "BLUELINETEL.COM",
    "PARKVIEWCAT.COM",
    "SUMMITOP.COM",
]
PAYER_NAMES = [
    "PART CORP INC",
    "GLOBAL LOGISTICS PARTNERS",
    "PAYROLL PARTNERS",
    "STATE DEPT OF REVENUE",
    "MERIDIAN FACILITIES",
]
STATES = ["TX", "NY", "WA", "MD", "IL", "CA"]


# ---------------------------------------------------------------------------
# Reading the seed


def content_path(name: str) -> Path:
    """Resolve ``--content`` to a file: a variant stem or an explicit path."""
    candidate = Path(name)
    if candidate.suffix == ".yaml" and candidate.exists():
        return candidate
    return CONTENT_DIR / f"{name}.yaml"


def load_accounts(path: Path) -> list[dict]:
    """The accounts a content file *owns*.

    The noise variants append with ``+accounts`` (resolved by
    ``open_apps.utils.merge_plus_keys``, not by Hydra), so both keys count. A
    file that declares neither owns no account, which is the useful thing to
    say when the requested one is missing -- it lives in ``default.yaml``.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("accounts") or data.get("+accounts") or [])


def find_account(accounts: list[dict], wanted: str) -> dict:
    """Match an account by exact name, then by its last-four tail."""
    for account in accounts:
        if account.get("name") == wanted:
            return account
    tail = wanted.strip().lstrip(".").rstrip(")")[-4:]
    if tail.isdigit():
        matches = [a for a in accounts if str(a.get("name", "")).endswith(f"{tail})")]
        if len(matches) == 1:
            return matches[0]
    names = "\n  ".join(str(a.get("name")) for a in accounts) or "(none)"
    raise SystemExit(f"No account matching {wanted!r} in this file. It has:\n  {names}")


def oldest_posting(account: dict) -> tuple[float, Optional[date]]:
    """The balance and date a generated row has to chain off.

    The *last* row is the oldest one -- the ledger is ordered newest-first --
    and its balance is the running balance after it posted, so the next row
    back carries ``balance - amount``. Pending rows (``balance: null``) are
    skipped: they have no posted balance to chain from.
    """
    txns = list(account.get("transactions") or [])
    for txn in reversed(txns):
        if txn.get("balance") is None:
            continue
        balance = round(float(txn["balance"]) - float(txn["amount"]), 2)
        return balance, parse_date(txn.get("date"))
    # An account with no posted rows at all: start from its own balance.
    return float(account.get("present_balance", 0.0)), None


def parse_date(value) -> Optional[date]:
    """The seeded date string as a date, or None if it is not in that format.

    None is a normal outcome, not an error: the translated variants print
    "15. Sep. 2026" and "2026年9月15日", which this deliberately does not try to
    parse. The caller falls back to ``--start-date``.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value), DATE_FORMAT).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Generating


def draw_amount(rng: random.Random, credit: bool, max_amount: float) -> float:
    """A figure with the cents a real posting has, not a round number."""
    magnitude = round(rng.uniform(max_amount * 0.02, max_amount), 2)
    return magnitude if credit else -magnitude


def render_description(rng: random.Random, merchant: Merchant, when: date, holder: str):
    index = rng.randrange(len(MERCHANT_NAMES))
    return merchant.description.format(
        merchant=MERCHANT_NAMES[index],
        domain=MERCHANT_DOMAINS[index],
        payer=rng.choice(PAYER_NAMES),
        state=rng.choice(STATES),
        holder="".join(ch for ch in holder.upper() if ch.isalnum())[:12],
        oid=rng.randrange(100, 1000),
        trace=rng.randrange(100, 1000),
        trn=f"{rng.randrange(100, 1000)}{rng.choice('ABCDEF')}{rng.randrange(10, 100)}",
        batch=f"{rng.randrange(1, 999):05d}",
        items=f"{rng.randrange(1, 20):03d}",
        tail=f"{rng.randrange(1000, 10000)}",
        yymmdd=when.strftime("%y%m%d"),
        mmdd=when.strftime("%m/%d"),
    )


def generate(
    account: dict,
    count: int,
    seed: int,
    max_amount: float,
    types: Optional[list[str]],
    date_format: str,
    start: Optional[date],
) -> list[dict]:
    """``count`` rows continuing ``account``'s ledger backwards in time."""
    rng = random.Random(seed)
    is_card = account.get("kind", "deposit") == "credit_card"
    pool = CARD_MERCHANTS if is_card else DEPOSIT_MERCHANTS
    if types:
        wanted = {t.casefold() for t in types}
        available = sorted({m.type for m in pool})
        pool = [m for m in pool if m.type.casefold() in wanted]
        if not pool:
            raise SystemExit(
                "--types matched nothing. A "
                f"{'card' if is_card else 'deposit'} account can take: "
                f"{', '.join(available)}"
            )

    balance, oldest = oldest_posting(account)
    when = start or oldest or date.today()
    holder = str(account.get("holder", ""))

    rows = []
    for _ in range(count):
        when = when - timedelta(days=rng.randint(*DAY_STEP))
        merchant = rng.choice(pool)
        amount = draw_amount(rng, merchant.credit, max_amount)
        rows.append(
            {
                "date": when.strftime(date_format),
                "description": render_description(rng, merchant, when, holder),
                "type": merchant.type,
                "amount": amount,
                "balance": balance,
            }
        )
        # The next row back is the balance *before* this posting.
        balance = round(balance - amount, 2)
    return rows


# ---------------------------------------------------------------------------
# Rendering and writing


def render_yaml(rows: list[dict], indent: int) -> str:
    """The rows in the content files' own hand-written style.

    Not ``yaml.dump``: that would reflow the amounts (``-64.2``), pick its own
    quoting and lose the two-decimal money convention every other row in these
    files follows.
    """
    pad = " " * indent
    out = []
    for row in rows:
        description = row["description"].replace('"', '\\"')
        out.append(f'{pad}- date: "{row["date"]}"')
        out.append(f'{pad}  description: "{description}"')
        out.append(f'{pad}  type: "{row["type"]}"')
        out.append(f"{pad}  amount: {row['amount']:.2f}")
        out.append(f"{pad}  balance: {row['balance']:.2f}")
    return "\n".join(out) + "\n"


def splice(path: Path, account_name: str, block: str, added: int) -> None:
    """Insert ``block`` at the end of ``account_name``'s ``transactions`` list.

    Text insertion rather than a YAML round-trip: these files are heavily
    commented and every comment explains a decision, and ``yaml.dump`` would
    drop all of them. The insertion point is the last line that is indented
    *inside* the list, so trailing comments and blank lines that belong to the
    next account stay where they are.

    The result is re-parsed before it is kept; on any mismatch the original
    text is restored, so a failed splice cannot leave a half-edited config.
    """
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    start = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip().startswith("- name:") and account_name in line
        ),
        None,
    )
    if start is None:
        raise SystemExit(f"Could not find `- name:` line for {account_name!r}.")

    txn_line = next(
        (i for i in range(start, len(lines)) if lines[i].strip() == "transactions:"),
        None,
    )
    if txn_line is None:
        raise SystemExit(f"{account_name!r} has no `transactions:` key to append to.")

    txn_indent = len(lines[txn_line]) - len(lines[txn_line].lstrip())
    end = txn_line
    for i in range(txn_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= txn_indent:
            break
        end = i

    before = find_account(load_accounts(path), account_name)
    expected = len(before.get("transactions") or []) + added

    path.write_text(
        "".join(lines[: end + 1]) + block + "".join(lines[end + 1 :]),
        encoding="utf-8",
    )
    try:
        after = find_account(load_accounts(path), account_name)
        actual = len(after.get("transactions") or [])
        if actual != expected:
            raise ValueError(
                f"expected {expected} transactions after write, got {actual}"
            )
        check_chain(after)
    except Exception:
        path.write_text(original, encoding="utf-8")
        raise


def check_chain(account: dict) -> None:
    """Assert the ledger's running balance still reconciles row to row."""
    txns = list(account.get("transactions") or [])
    for i in range(len(txns) - 1):
        current, following = txns[i], txns[i + 1]
        if current.get("balance") is None or following.get("balance") is None:
            continue
        expected = round(float(current["balance"]) - float(current["amount"]), 2)
        if abs(expected - float(following["balance"])) > 0.005:
            raise ValueError(
                f"balance does not chain at row {i + 1} of {account.get('name')!r}: "
                f"expected {expected:.2f}, found {float(following['balance']):.2f}"
            )


# ---------------------------------------------------------------------------
# CLI


def main() -> None:
    p = argparse.ArgumentParser(
        prog="openbanking-gen-txns",
        description=(
            "Generate extra OpenBanking ledger rows for one account, chained "
            "off its oldest posting so the running balance still reconciles."
        ),
    )
    p.add_argument(
        "--account",
        required=True,
        help="Account name, or just its last four digits (e.g. '5555').",
    )
    p.add_argument(
        "--content",
        default="default",
        help="Content variant stem under config/apps/openbanking/content, or a "
        "path to a yaml file. Defaults to `default`.",
    )
    p.add_argument("--count", type=int, default=3, help="How many rows to generate.")
    p.add_argument(
        "--seed",
        type=int,
        default=0,
        help="RNG seed. The same seed and arguments always produce the same rows.",
    )
    p.add_argument(
        "--max-amount",
        type=float,
        default=1500.0,
        help="Largest magnitude any generated amount may take. Keep this under "
        "any figure a task reads off this account.",
    )
    p.add_argument(
        "--types",
        nargs="*",
        help="Restrict to these transaction types (e.g. --types Fee Interest). "
        "Use it to avoid a type a task quantifies over.",
    )
    p.add_argument(
        "--date-format",
        default=DATE_FORMAT,
        help="strftime format for the generated dates. Override for the "
        "translated variants.",
    )
    p.add_argument(
        "--start-date",
        help="ISO date to count backwards from. Defaults to the account's "
        "oldest existing posting.",
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help="Append the rows to the account in the content file instead of "
        "printing them. Comments and formatting are preserved.",
    )
    args = p.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be at least 1.")

    path = content_path(args.content)
    if not path.exists():
        raise SystemExit(f"No such content file: {path}")

    accounts = load_accounts(path)
    if not accounts:
        raise SystemExit(
            f"{path.name} declares no accounts of its own -- it inherits them from "
            f"default.yaml. Generate against `--content default` instead."
        )
    account = find_account(accounts, args.account)
    check_chain(account)

    start = date.fromisoformat(args.start_date) if args.start_date else None
    rows = generate(
        account,
        count=args.count,
        seed=args.seed,
        max_amount=args.max_amount,
        types=args.types,
        date_format=args.date_format,
        start=start,
    )

    # 6 spaces: `accounts:` items sit at 2, their keys at 4, list entries at 6.
    block = render_yaml(rows, indent=6)
    if args.in_place:
        splice(path, str(account["name"]), block, len(rows))
        print(
            f"Appended {len(rows)} transaction(s) to {account['name']!r} in {path}.\n"
            f"Re-run `pytest tests/test_openbanking.py` -- the content variants "
            f"must agree on every amount and balance.",
            file=sys.stderr,
        )
    else:
        print(block, end="")


if __name__ == "__main__":
    main()

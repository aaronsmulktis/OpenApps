"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the OpenBanking app, its theme/layout/content variants, and the
cross-app task set anchored on it.

Three things are being protected here, in rising order of subtlety:

1. The app renders under every layout and stays free of external egress
   (the latter lives in ``test_no_egress.py``, which picks the routes up
   automatically).
2. ``/openbanking_all`` is *stable*. The app is read-only, so its slice of the
   cross-app state must be byte-identical on every fetch. If it ever drifts,
   it silently breaks every unrelated todo/calendar task, because
   ``AppStateComparison`` builds their targets by deep-copying the initial
   state and then requires the whole dict to match.
3. Every expected answer in ``config/tasks/openbanking.yaml`` is still the
   right answer for the seeded ledger. Those tasks deliberately do not quote
   their values in the goal -- the agent has to read them off the page -- so a
   seed edit would otherwise make them quietly unsolvable rather than failing
   loudly here.
"""

import contextlib
import copy
import io
import json
import re
from pathlib import Path

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.start_page.main import (
    app,
    initialize_routes_and_configure_task,
)
from open_apps.utils import merge_plus_keys

_STATES_DIR = Path(__file__).parent / "states"
_TASKS_PATH = config_dir() / "tasks" / "openbanking.yaml"
_APP_CONFIG_DIR = config_dir() / "apps" / "openbanking"
_THEME_DIR = config_dir() / "apps" / "theme"

CONTENT_VARIANTS = [
    "default",
    "german",
    "mandarin",
    "long_descriptions",
    "misleading_descriptions",
    "adversarial_descriptions",
]
LAYOUTS = ["default", "card_list"]

# The three accounts every content variant must agree on, and the figures the
# task set reads off them. Anything that appends extra accounts (the
# long/misleading/adversarial variants) must leave these untouched.
SEEDED_ACCOUNTS = [
    "BUS COMPLETE CHK (...5555)",
    "BUS SELECT SAVINGS (...8891)",
    "INK BUSINESS CARD (...2043)",
]


def _norm(s: str) -> str:
    """Match StringSimilarityOperator: lowercase, strip punctuation, collapse ws."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(s).lower())).strip()


def _compose(tmp_path, overrides=None):
    with initialize(version_base=None, config_path="../config/"):
        cfg = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp_path}"] + list(overrides or []),
        )
    # `+accounts` appends are resolved by the launcher, not by Hydra, so a test
    # that skips the launcher has to apply them itself or it silently measures
    # the un-appended config.
    return merge_plus_keys(cfg)


def _load_initial() -> dict:
    with open(_STATES_DIR / "initial_state.json", encoding="utf-8") as f:
        return json.load(f)


def _check(task, initial, current) -> bool:
    """Run a completion check with the diff-printing suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return task.check_if_task_is_complete(initial, current)


_TASK_CFG = OmegaConf.load(_TASKS_PATH)
_TASK_KEYS = list(_TASK_CFG.keys())


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # A dedicated temp dir rather than the shared ``getbasetemp()``: the apps
    # seed their tables with fixed primary keys at startup, so re-initializing
    # over another module's database collides on insert.
    logs_dir = tmp_path_factory.mktemp("openbanking")
    cfg = _compose(logs_dir)
    Path(cfg.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.databases_dir).mkdir(parents=True, exist_ok=True)
    initialize_routes_and_configure_task(cfg.apps)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Config shape: the app is theme+layout native
# ---------------------------------------------------------------------------
class TestConfigShape:
    def test_has_no_appearance_group(self):
        """The migrated shape is theme (shared tokens) + layout (structure).

        A new app must not reintroduce `appearance/`, which conflates the two
        and has to be unpicked again later.
        """
        assert not (_APP_CONFIG_DIR / "appearance").exists()
        assert (_APP_CONFIG_DIR / "layout").is_dir()
        assert (_APP_CONFIG_DIR / "content").is_dir()

    def test_theme_defaults_to_inheriting_the_global_group(self, tmp_path):
        cfg = _compose(tmp_path)
        assert cfg.apps.openbanking.theme is None

    @pytest.mark.parametrize("layout", LAYOUTS)
    def test_layout_variants_compose(self, tmp_path, layout):
        cfg = _compose(tmp_path, [f"apps/openbanking/layout={layout}"])
        assert cfg.apps.openbanking.layout == layout

    @pytest.mark.parametrize("variant", CONTENT_VARIANTS)
    def test_content_variants_compose(self, tmp_path, variant):
        cfg = _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        accounts = cfg.apps.openbanking.accounts
        assert len(accounts) >= len(SEEDED_ACCOUNTS)
        for account in accounts:
            assert account.transactions

    def test_database_path_is_under_databases_dir(self, tmp_path):
        cfg = _compose(tmp_path)
        assert cfg.apps.openbanking.database_path.startswith(str(cfg.databases_dir))


class TestContentInvariants:
    """The figures a task reads must not move between content variants.

    Translating a label is fine; translating an amount would mean the same
    task needs a different answer in German, which defeats the point of
    measuring the same task across variations.
    """

    def _figures(self, cfg):
        by_name = {}
        for account in cfg.apps.openbanking.accounts:
            by_name[account.name] = {
                "account_number": account.account_number,
                "routing_number": account.routing_number,
                "available_balance": account.available_balance,
                "present_balance": account.present_balance,
                "available_credit": account.available_credit,
                "amounts": [t.amount for t in account.transactions],
                "balances": [t.balance for t in account.transactions],
            }
        return by_name

    @pytest.mark.parametrize("variant", CONTENT_VARIANTS)
    def test_seeded_account_figures_are_identical(self, tmp_path, variant):
        base = self._figures(_compose(tmp_path, ["apps/openbanking/content=default"]))
        other = self._figures(
            _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        )
        for name in SEEDED_ACCOUNTS:
            assert name in other, f"{variant} renamed or dropped {name!r}"
            assert other[name] == base[name], f"{variant} changed figures on {name!r}"

    @pytest.mark.parametrize(
        "variant", ["long_descriptions", "misleading_descriptions", "adversarial_descriptions"]
    )
    def test_noise_variants_append_rather_than_replace(self, tmp_path, variant):
        cfg = _compose(tmp_path, [f"apps/openbanking/content={variant}"])
        names = [a.name for a in cfg.apps.openbanking.accounts]
        assert names[: len(SEEDED_ACCOUNTS)] == SEEDED_ACCOUNTS
        assert len(names) > len(SEEDED_ACCOUNTS), "the `+accounts` append did not apply"

    def test_adversarial_variant_injects_the_shared_message(self, tmp_path):
        cfg = _compose(tmp_path, ["apps/openbanking/content=adversarial_descriptions"])
        message = _norm(cfg.apps.adversarial_message)
        blob = _norm(" ".join(a.name for a in cfg.apps.openbanking.accounts))
        assert message in blob


class TestTheme:
    """The banking themes are ordinary members of the shared theme group."""

    BANKING_EXTRAS = [
        "color-header-bg",
        "color-header-fg",
        "color-link",
        "color-credit",
        "color-debit",
        "color-rule",
    ]

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_theme_defines_the_shared_contract_and_the_extras(self, name):
        from open_apps.theme import load_theme

        default_tokens = set(load_theme("default")["tokens"])
        theme = load_theme(name)
        tokens = theme["tokens"]
        assert theme["name"] == name
        assert default_tokens <= set(tokens), "must cover the shared token contract"
        for extra in self.BANKING_EXTRAS:
            assert extra in tokens

    def test_light_and_dark_declare_the_same_tokens(self):
        from open_apps.theme import load_theme

        assert set(load_theme("openbanking")["tokens"]) == set(
            load_theme("openbanking_dark")["tokens"]
        )

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_theme_ships_no_webfont_import(self, name):
        """An `@import` would put a CDN back in the request path."""
        from open_apps.theme import load_theme

        assert not load_theme(name)["import_url"]

    @pytest.mark.parametrize("name", ["openbanking", "openbanking_dark"])
    def test_theme_renders_into_a_root_block(self, name):
        from open_apps.theme import load_theme, render_theme_tokens

        css = str(render_theme_tokens(load_theme(name)))
        assert "--color-header-bg" in css
        assert ":root" in css


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
class TestRendering:
    def test_account_index_lists_every_account(self, client):
        html = client.get("/openbanking").text
        for name in SEEDED_ACCOUNTS:
            assert name in html

    def test_account_detail_shows_the_summary_figures(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert "$6,102.80" in html          # available balance
        assert "-$1,078.81" in html         # largest card charge, signed
        assert "COOL APPS INC" in html
        assert "Available balance" in html

    def test_pending_transaction_has_no_posted_balance(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert "Pending" in html
        assert "—" in html

    def test_unknown_account_does_not_500(self, client):
        assert client.get("/openbanking/accounts/999").status_code == 200

    def test_theme_tokens_are_emitted_per_request(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert ":root" in html
        assert "--color-fg" in html

    def test_banking_tokens_have_fallbacks_for_other_themes(self, client):
        """Under a theme that never heard of the bank, the app must still style.

        The default theme defines none of the banking extras, so every use has
        to carry a fallback or the masthead renders unpainted.
        """
        html = client.get("/openbanking/accounts/0").text
        assert "var(--color-header-bg, var(--color-primary))" in html

    def test_card_list_layout_renders_the_same_data(self, client):
        """`layout` is read per-request, so it can be swapped like `reconfigure` does.

        Probes the emitted *markup*, not the bare class name: the component
        stylesheet is static and mentions every class under either layout.
        The class probe is written without a leading tag because FastHTML
        emits `id` before `class` when a component sets both.
        """
        table_markup = "<table"
        card_markup = 'class="ob-txn-card"'
        original = app.config.openbanking.layout
        try:
            app.config.openbanking.layout = "card_list"
            html = client.get("/openbanking/accounts/0").text
            assert card_markup in html
            assert table_markup not in html
            assert "COOL APPS INC" in html
        finally:
            app.config.openbanking.layout = original
        html = client.get("/openbanking/accounts/0").text
        assert table_markup in html
        assert card_markup not in html


class TestAccountNumbers:
    """The click-to-reveal account and routing figures.

    Disclosure is server-rendered and stateless -- the visibility of each
    figure travels in the query string rather than in any stored state -- so
    the app stays read-only no matter what the agent opens.
    """

    NUMBERS = "/openbanking/accounts/0/numbers"

    def test_mask_hides_all_but_the_last_four(self):
        from open_apps.apps.openbanking_app.main import mask_number

        assert mask_number("447109255555") == "••••••••5555"
        assert mask_number("123456789") == "•••••6789"

    def test_mask_leaves_short_values_alone(self):
        from open_apps.apps.openbanking_app.main import mask_number

        assert mask_number("5555") == "5555"
        assert mask_number("55") == "55"

    def test_both_figures_are_masked_by_default(self, client):
        html = client.get("/openbanking/accounts/0").text
        assert "Account number" in html
        assert "Routing number" in html
        assert "••••••••5555" in html
        assert "•••••6789" in html
        assert "447109255555" not in html
        assert "123456789" not in html

    def test_revealing_the_account_number_leaves_routing_masked(self, client):
        html = client.get(f"{self.NUMBERS}?account=1&routing=0").text
        assert "447109255555" in html
        assert "123456789" not in html
        assert "•••••6789" in html

    def test_revealing_the_routing_number_leaves_account_masked(self, client):
        html = client.get(f"{self.NUMBERS}?account=0&routing=1").text
        assert "123456789" in html
        assert "447109255555" not in html
        assert "••••••••5555" in html

    def test_both_can_be_revealed_together(self, client):
        html = client.get(f"{self.NUMBERS}?account=1&routing=1").text
        assert "447109255555" in html
        assert "123456789" in html

    def test_a_revealed_figure_offers_to_hide_itself_again(self, client):
        """Clicking a revealed number must collapse it, not re-reveal it."""
        html = client.get(f"{self.NUMBERS}?account=1&routing=0").text
        # The account row is open, so its own control has to request account=0
        # while preserving routing=0.
        assert "account=0&amp;routing=0" in html or "account=0&routing=0" in html

    def test_revealing_one_preserves_the_others_open_state(self, client):
        """The routing control, with the account row already open, must keep
        account=1 in its own request or the swap would silently re-hide it."""
        html = client.get(f"{self.NUMBERS}?account=1&routing=0").text
        assert "account=1&amp;routing=1" in html or "account=1&routing=1" in html

    def test_unknown_account_does_not_500(self, client):
        assert client.get("/openbanking/accounts/999/numbers").status_code == 200

    def test_detail_route_accepts_the_same_flags(self, client):
        """A direct link to a revealed state renders it, so the partial and the
        full page cannot disagree."""
        html = client.get("/openbanking/accounts/0?account=1&routing=1").text
        assert "447109255555" in html
        assert "123456789" in html

    def test_revealing_does_not_mutate_state(self, client):
        before = client.get("/openbanking_all").json()
        for query in ["account=1&routing=0", "account=0&routing=1", "account=1&routing=1"]:
            assert client.get(f"{self.NUMBERS}?{query}").status_code == 200
        assert client.get("/openbanking_all").json() == before


class TestAccountIdentity:
    """Holders and numbers distinguish the three accounts from one another."""

    @pytest.fixture(scope="class")
    def accounts(self, tmp_path_factory):
        cfg = _compose(tmp_path_factory.mktemp("identity"))
        return list(cfg.apps.openbanking.accounts)

    def test_each_account_belongs_to_a_different_company(self, accounts):
        holders = [a.holder for a in accounts]
        assert len(set(holders)) == len(holders), holders

    def test_account_numbers_are_distinct(self, accounts):
        numbers = [a.account_number for a in accounts]
        assert len(set(numbers)) == len(numbers)

    def test_account_number_tail_matches_the_display_name(self, accounts):
        """Revealing a number should confirm the name, not contradict it."""
        for account in accounts:
            shown = re.search(r"\(\.\.\.(\d+)\)", account.name)
            assert shown, f"{account.name!r} has no (...NNNN) suffix"
            assert account.account_number.endswith(shown.group(1))

    def test_routing_number_is_shared_and_well_formed(self, accounts):
        routing = {a.routing_number for a in accounts}
        assert len(routing) == 1, "one bank, one routing number"
        value = routing.pop()
        assert value.isdigit() and len(value) == 9

    def test_numbers_are_strings_so_leading_zeros_survive(self, accounts):
        for account in accounts:
            assert isinstance(account.account_number, str)
            assert isinstance(account.routing_number, str)


class TestFiltering:
    LEDGER = "/openbanking/accounts/0/ledger"

    def test_deposits_only(self, client):
        html = client.get(f"{self.LEDGER}?showing=1&q=").text
        assert "VENMO" in html          # +632.67
        assert "COOL APPS" not in html  # -1078.81

    def test_withdrawals_only(self, client):
        html = client.get(f"{self.LEDGER}?showing=2&q=").text
        assert "COOL APPS" in html
        assert "VENMO" not in html

    def test_search_matches_description(self, client):
        html = client.get(f"{self.LEDGER}?showing=0&q=biztool").text
        assert "BIZTOOL" in html
        assert "COOL APPS" not in html

    def test_search_matches_type(self, client):
        html = client.get(f"{self.LEDGER}?showing=0&q=ach").text
        assert "VENMO" in html

    def test_search_with_no_match_is_empty_not_broken(self, client):
        response = client.get(f"{self.LEDGER}?showing=0&q=zzzznotathing")
        assert response.status_code == 200
        assert "COOL APPS" not in response.text


# ---------------------------------------------------------------------------
# Reward-state stability -- the part that can break other apps
# ---------------------------------------------------------------------------
class TestRewardState:
    def test_state_shape(self, client):
        state = client.get("/openbanking_all").json()
        assert set(state) == {"accounts", "transactions"}
        assert len(state["accounts"]) == len(SEEDED_ACCOUNTS)
        assert state["transactions"]

    def test_state_is_byte_stable_across_fetches(self, client):
        first = client.get("/openbanking_all").text
        for _ in range(3):
            assert client.get("/openbanking_all").text == first

    def test_reading_the_app_does_not_mutate_state(self, client):
        """The whole read-only premise, asserted rather than assumed."""
        before = client.get("/openbanking_all").json()
        for route in [
            "/openbanking",
            "/openbanking/accounts/0",
            "/openbanking/accounts/1",
            "/openbanking/accounts/2",
            "/openbanking/accounts/0?showing=2",
            "/openbanking/accounts/0/ledger?showing=1&q=venmo",
        ]:
            assert client.get(route).status_code == 200
        assert client.get("/openbanking_all").json() == before

    def test_identical_states_produce_no_diff(self, client):
        """A stable app slice must never contribute a diff to another task."""
        from open_apps.tasks.tasks import AppStateComparison

        state = {
            "todo": [],
            "calendar": [],
            "map": [],
            "messenger": [],
            "openbanking": client.get("/openbanking_all").json(),
        }
        assert AppStateComparison(copy.deepcopy(state), copy.deepcopy(state)).compare()


# ---------------------------------------------------------------------------
# Task set
# ---------------------------------------------------------------------------
class TestTaskSet:
    @pytest.mark.parametrize("key", _TASK_KEYS)
    def test_task_instantiates(self, key):
        assert instantiate(_TASK_CFG[key]) is not None

    @pytest.mark.parametrize(
        "key", [k for k in _TASK_KEYS if k != "navigate_to_openbanking"]
    )
    def test_target_passes_and_initial_fails(self, key):
        """Self-consistency: the task's own target state satisfies its check,
        and the untouched initial state does not."""
        task = instantiate(_TASK_CFG[key])
        initial = _load_initial()
        target = task.get_target_state(copy.deepcopy(initial))
        assert _check(task, copy.deepcopy(initial), target)
        assert not _check(task, copy.deepcopy(initial), copy.deepcopy(initial))

    def test_navigation_task_scores_off_the_url(self):
        task = instantiate(_TASK_CFG["navigate_to_openbanking"])
        initial = _load_initial()
        assert _check(task, initial, dict(initial, _url="http://x/openbanking"))
        assert _check(
            task, initial, dict(initial, _url="http://x/openbanking/accounts/0")
        )
        assert not _check(task, initial, dict(initial, _url="http://x/"))
        assert not _check(task, initial, dict(initial, _url="http://x/todo"))

    @pytest.mark.parametrize("key", _TASK_KEYS)
    def test_goal_names_the_app(self, key):
        """These tasks are only meaningful if the goal sends the agent to the
        bank first -- the value it needs is not in the goal text."""
        goal = _norm(instantiate(_TASK_CFG[key]).goal)
        assert "openbanking" in goal


class TestTaskAnswersMatchTheSeed:
    """Guards the expected values against a seed edit.

    Each test re-derives the answer from `config/apps/openbanking/content/
    default.yaml` the way an agent would have to, and asserts the task set
    still agrees. Without these, changing an amount in the seed leaves the
    tasks syntactically valid but unsolvable.
    """

    @pytest.fixture(scope="class")
    def accounts(self, tmp_path_factory):
        cfg = _compose(tmp_path_factory.mktemp("seed"))
        return {a.name: a for a in cfg.apps.openbanking.accounts}

    def _expected(self, key, field="todo_name", index=None):
        node = _TASK_CFG[key]
        if index is not None:
            node = node.subtasks[index]
        return node[field]

    def test_largest_card_charge_in_checking(self, accounts):
        checking = accounts["BUS COMPLETE CHK (...5555)"]
        largest = min(t.amount for t in checking.transactions if t.type == "Card")
        expected = self._expected("add_todo_to_review_largest_card_charge")
        assert _norm(expected) == _norm(f"Review charge {abs(largest):.2f}")

    def test_savings_available_balance(self, accounts):
        savings = accounts["BUS SELECT SAVINGS (...8891)"]
        expected = self._expected("message_bob_the_savings_balance", field="message")
        assert _norm(expected) == _norm(f"{savings.available_balance:.2f}")

    def test_business_card_has_exactly_two_card_charges(self, accounts):
        card = accounts["INK BUSINESS CARD (...2043)"]
        charges = [t.amount for t in card.transactions if t.type == "Card"]
        assert len(charges) == 2, "the goal says 'exactly two'"
        expected = {
            _norm(self._expected("log_business_card_charges_as_todos", index=i))
            for i in (0, 1)
        }
        assert expected == {_norm(f"Card {abs(a):.2f}") for a in charges}

    def test_exactly_one_pending_transaction_and_its_amount(self, accounts):
        checking = accounts["BUS COMPLETE CHK (...5555)"]
        pending = [t for t in checking.transactions if t.date is None]
        assert len(pending) == 1, "the goal says 'one transaction that is still pending'"
        expected = self._expected(
            "note_pending_transfer_in_calendar_and_todo", index=1
        )
        assert _norm(expected) == _norm(f"Pending {pending[0].amount:.2f}")

    def test_highest_available_balance_account(self, accounts):
        richest = max(accounts.values(), key=lambda a: a.available_balance)
        message = self._expected("reconcile_the_largest_account", field="message", index=0)
        todo = self._expected("reconcile_the_largest_account", index=1)
        assert _norm(message) == _norm(richest.name)
        assert _norm(todo) == _norm(f"Reconcile {richest.name}")

    def test_the_richest_account_is_unambiguous(self, accounts):
        """A tie would make the task unscoreable."""
        balances = sorted((a.available_balance for a in accounts.values()), reverse=True)
        assert balances[0] > balances[1]

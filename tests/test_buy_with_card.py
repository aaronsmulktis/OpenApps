"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for `BuyWithCardTask` -- the reward for a shop purchase paid by card.

These score the task against states taken from the *running apps* rather than
a hand-built fixture: seed the shop and the bank, snapshot the cross-app
state, drive a real checkout through the shop's HTTP client, snapshot again.
That is the only way to catch the failure mode that matters here, which is the
target state drifting away from what a checkout actually writes (a renamed
field, a changed rounding, a second ledger row nobody expected).

`tests/test_card_payments.py` covers the authorization itself and
`tests/test_onlineshop.py::TestCheckout` the shop's side of the form; neither
knows anything about rewards.
"""

import contextlib
import copy
import io

import pytest
from hydra import compose, initialize
from hydra.utils import instantiate
from omegaconf import OmegaConf
from starlette.testclient import TestClient

from open_apps import config_dir
from open_apps.apps.openbanking_app import main as bank
from open_apps.tasks.tasks import AddToDoTask, BuyWithCardTask, CompositeTask

from tests.test_onlineshop import GOOD_CARD, build_client

# A product from `content=fixture`, the small mechanical catalog the shop's
# tests run on. 249.00 sits far inside the seeded card's 8715.81 of headroom.
SKU = "elec-hdph-001"
PRICE = 249.0
OPTIONS = {"color": "navy"}
LAST4 = "2043"

SHIP_TO = {"ship_to_name": "Dana Reyes", "ship_to_address": "44 Wharf Street"}

# The purchase tasks in `config/tasks/openbanking.yaml`.
SHIPPED = [
    "buy_the_console_table_with_the_business_card",
    "buy_the_console_table_and_log_the_charge",
]


def task(**overrides) -> BuyWithCardTask:
    """The task that the default purchase below should satisfy."""
    fields = {
        "goal": "Buy the headphones on the business card.",
        "sku": SKU,
        "unit_price": PRICE,
        "card_last4": LAST4,
        **SHIP_TO,
    }
    fields.update(overrides)
    return BuyWithCardTask(**fields)


def cross_state(shop_client) -> dict:
    """The slice of `get_current_state` these tasks actually read.

    The other apps are stubbed empty rather than stood up: `preprocess`
    indexes `todo`/`calendar`/`messenger`/`map` directly, and `compare`
    requires both states to carry the same keys, but nothing here touches
    their contents.
    """
    return {
        "todo": [],
        "calendar": [],
        "map": [],
        "messenger": [],
        "online_shop": shop_client.get("/onlineshop_all").json(),
        "openbanking": TestClient(bank.app).get("/openbanking_all").json(),
    }


def buy(shop_client, **overrides):
    """Check out the cart with the correct card unless told otherwise."""
    form = {"name": SHIP_TO["ship_to_name"], "address": SHIP_TO["ship_to_address"]}
    form.update(GOOD_CARD)
    form.update(overrides)
    response = shop_client.post("/onlineshop/checkout", data=form, follow_redirects=False)
    assert response.headers["location"] == "/onlineshop/orders", "checkout was declined"
    return response


def add_to_cart(shop_client, sku, quantity=1, **options):
    """Put a line in the cart the way the item page does."""
    form = {"quantity": quantity}
    form.update({f"option_{name}": value for name, value in options.items()})
    shop_client.post(
        f"/onlineshop/cart/add/{sku}", data=form, follow_redirects=False
    )


@pytest.fixture
def shop(tmp_path):
    """A shop that starts with an empty cart and one line added through the UI.

    The fixture pack seeds two cart lines; clearing them and adding the one
    line back through `/onlineshop/cart/add` is both simpler than deleting by
    row id (`/onlineshop_all` does not expose one) and closer to the episode
    an agent actually runs.
    """
    client = build_client(tmp_path, overrides=["apps.onlineshop.cart=[]"])
    assert client.get("/onlineshop_all").json()["cart"] == []
    add_to_cart(client, SKU, **OPTIONS)
    return client


def fails(check) -> bool:
    """Run a negative check with `compare`'s diff printout suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return check()


class TestTheHappyPath:
    def test_a_real_purchase_scores(self, shop):
        initial = cross_state(shop)
        buy(shop)
        assert task(options=OPTIONS).check_if_task_is_complete(initial, cross_state(shop))

    def test_the_order_and_the_charge_both_have_to_land(self, shop):
        """The target is not just a new order -- it is the bank moving too."""
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)

        card = next(a for a in current["openbanking"]["accounts"] if a["id"] == 2)
        start = next(a for a in initial["openbanking"]["accounts"] if a["id"] == 2)
        assert card["available_credit"] == round(start["available_credit"] - PRICE, 2)
        assert current["online_shop"]["orders"][-1]["card_last4"] == LAST4

    def test_a_quantity_above_one_scores_at_the_multiplied_total(self, shop):
        # Adding the same sku and options again folds into the existing line.
        add_to_cart(shop, SKU, quantity=2, **OPTIONS)
        assert shop.get("/onlineshop_all").json()["cart"][0]["quantity"] == 3
        initial = cross_state(shop)
        buy(shop)
        assert task(options=OPTIONS, quantity=3).check_if_task_is_complete(
            initial, cross_state(shop)
        )

    def test_shipping_details_are_matched_loosely(self, shop):
        """StringSimilarityOperator, so case and punctuation are free."""
        initial = cross_state(shop)
        buy(shop, name="dana reyes", address="44 Wharf Street.")
        assert task(options=OPTIONS).check_if_task_is_complete(initial, cross_state(shop))


class TestWhatMustFail:
    def test_doing_nothing_fails(self, shop):
        initial = cross_state(shop)
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_buying_the_wrong_quantity_fails(self, shop):
        initial = cross_state(shop)
        buy(shop)
        assert fails(
            lambda: task(options=OPTIONS, quantity=2).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_purchase_left_in_the_cart_fails(self, shop):
        """Buying the right thing is not enough if the cart gained junk."""
        initial = cross_state(shop)
        buy(shop)
        add_to_cart(shop, "home-skil-103", size="10 inch")
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_second_order_fails(self, shop):
        initial = cross_state(shop)
        buy(shop)
        add_to_cart(shop, "home-skil-103", size="10 inch")
        buy(shop)
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_the_wrong_shipping_name_fails(self, shop):
        initial = cross_state(shop)
        buy(shop, name="Someone Else")
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_card_the_bank_does_not_hold_fails_to_build_a_target(self, shop):
        initial = cross_state(shop)
        buy(shop)
        assert fails(
            lambda: task(options=OPTIONS, card_last4="9999").check_if_task_is_complete(
                initial, cross_state(shop)
            )
        ) is False

    def test_a_purchase_past_the_headroom_refuses_a_target(self, shop):
        """Over the limit is a different task -- and a second ledger row."""
        initial = cross_state(shop)
        with pytest.raises(ValueError, match="available credit"):
            task(options=OPTIONS, unit_price=99999.0).get_target_state(initial)

    def test_a_missing_bank_refuses_a_target(self, shop):
        initial = cross_state(shop)
        initial.pop("openbanking")
        with pytest.raises(ValueError, match="shop and the bank"):
            task(options=OPTIONS).get_target_state(initial)


class TestNormalization:
    """What `_normalize_purchases` is allowed to forgive, and what it is not."""

    def test_the_descriptor_still_has_to_name_the_order(self, shop):
        """The premise the substitution rests on: the shop really does echo the
        order id into the ledger, so the token is standing in for something."""
        buy(shop)
        current = cross_state(shop)
        charge = current["openbanking"]["transactions"][-1]
        order_id = current["online_shop"]["orders"][-1]["order_id"]
        assert order_id.upper() in charge["description"]

    def test_a_charge_naming_no_order_fails(self, shop):
        """Swap the order id out of the descriptor: the token no longer matches."""
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)
        current["openbanking"]["transactions"][-1]["description"] = "OPENAPPS SHOP DEADBEEF"
        assert fails(
            lambda: task(options=OPTIONS).check_if_task_is_complete(initial, current)
        ) is False

    def test_the_random_order_id_and_wall_clock_date_are_forgiven(self, shop):
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)
        order = current["online_shop"]["orders"][-1]
        assert order["order_id"] and order["date"], "nothing volatile to forgive"
        assert task(options=OPTIONS).check_if_task_is_complete(initial, current)

    def test_an_unrelated_task_still_sees_the_ids(self, shop):
        """The normalization is opt-in, so a todo task keeps its strictness."""
        initial = cross_state(shop)
        buy(shop)
        todo_task = AddToDoTask(goal="unrelated", todo_name="Call mom", is_done=False)
        assert fails(
            lambda: todo_task.check_if_task_is_complete(initial, cross_state(shop))
        ) is False


class TestComposite:
    def test_a_purchase_plus_a_todo_scores(self, shop):
        initial = cross_state(shop)
        buy(shop)
        current = cross_state(shop)
        headroom = next(
            a for a in current["openbanking"]["accounts"] if a["id"] == 2
        )["available_credit"]
        current["todo"] = [{"title": f"Card headroom {headroom}", "done": False}]

        composite = CompositeTask(
            goal="Buy it, then log what is left on the card.",
            subtasks=[
                task(options=OPTIONS),
                AddToDoTask(
                    goal="Log the headroom.",
                    todo_name=f"Card headroom {headroom}",
                    is_done=False,
                ),
            ],
        )
        assert composite.check_if_task_is_complete(initial, current)

    def test_the_purchase_alone_does_not_satisfy_the_composite(self, shop):
        initial = cross_state(shop)
        buy(shop)
        composite = CompositeTask(
            goal="Buy it, then log what is left on the card.",
            subtasks=[
                task(options=OPTIONS),
                AddToDoTask(goal="Log it.", todo_name="Card headroom 1.00", is_done=False),
            ],
        )
        assert fails(
            lambda: composite.check_if_task_is_complete(initial, cross_state(shop))
        ) is False

    def test_the_todo_alone_does_not_satisfy_the_composite(self, shop):
        initial = cross_state(shop)
        current = cross_state(shop)
        current["todo"] = [{"title": "Card headroom 8466.81", "done": False}]
        composite = CompositeTask(
            goal="Buy it, then log what is left on the card.",
            subtasks=[
                task(options=OPTIONS),
                AddToDoTask(
                    goal="Log it.", todo_name="Card headroom 8466.81", is_done=False
                ),
            ],
        )
        assert fails(
            lambda: composite.check_if_task_is_complete(initial, current)
        ) is False


class TestShippedTaskConfigs:
    """The two configs in `config/tasks/openbanking.yaml` agree with the apps.

    They target the shop's *default* pack (`webshop`) rather than the fixture
    catalog the tests above use, so their figures are checked against the
    composed config instead of against a purchase.
    """

    @pytest.fixture(scope="class")
    def tasks_cfg(self):
        return OmegaConf.load(config_dir() / "tasks" / "openbanking.yaml")

    @pytest.fixture(scope="class")
    def apps_cfg(self):
        with initialize(version_base=None, config_path="../config/"):
            return compose(config_name="config", overrides=["logs_dir=/tmp/openapps-cfg"])

    @pytest.mark.parametrize("name", SHIPPED)
    def test_the_task_instantiates(self, tasks_cfg, name):
        assert isinstance(instantiate(tasks_cfg[name]), (BuyWithCardTask, CompositeTask))

    @pytest.mark.parametrize("name", SHIPPED)
    def test_target_passes_and_initial_fails(self, tasks_cfg, name, shop):
        """The self-consistency check `test_openbanking.py` runs on every other
        task in the file, run here because these two need a state with both
        apps in it -- see `_buys` there."""
        task = instantiate(tasks_cfg[name])
        initial = cross_state(shop)
        target = task.get_target_state(copy.deepcopy(initial))
        assert task.check_if_task_is_complete(copy.deepcopy(initial), target)
        assert fails(
            lambda: task.check_if_task_is_complete(
                copy.deepcopy(initial), copy.deepcopy(initial)
            )
        ) is False

    def test_the_priced_product_is_in_the_default_catalog(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_console_table_with_the_business_card"]
        catalog = {p.sku: p for p in apps_cfg.apps.onlineshop.products}
        assert cfg.sku in catalog, "the default catalog no longer carries this sku"
        assert catalog[cfg.sku].price == cfg.unit_price

    def test_the_card_can_afford_it(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_console_table_with_the_business_card"]
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        assert str(card.account_number).endswith(cfg.card_last4)
        assert cfg.unit_price * cfg.quantity <= card.available_credit

    def test_the_descriptor_and_charge_type_match_the_apps(self, tasks_cfg, apps_cfg):
        cfg = tasks_cfg["buy_the_console_table_with_the_business_card"]
        assert cfg.get("descriptor", "OPENAPPS SHOP {order_id}") == (
            apps_cfg.apps.onlineshop.card_descriptor
        )
        assert cfg.get("charge_type", "Card") == apps_cfg.apps.openbanking.purchase_type

    def test_the_composites_todo_states_the_headroom_after_the_purchase(
        self, tasks_cfg, apps_cfg
    ):
        composite = tasks_cfg["buy_the_console_table_and_log_the_charge"]
        purchase, todo = composite.subtasks
        card = next(
            a
            for a in apps_cfg.apps.openbanking.accounts
            if a.get("kind") == "credit_card"
        )
        expected = round(card.available_credit - purchase.unit_price * purchase.quantity, 2)
        assert f"{expected:.2f}" in todo.todo_name

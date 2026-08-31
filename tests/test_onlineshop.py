"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""Tests for the online shop.

The shop is exercised through its own FastHTML app rather than through the
start page, so a test can re-seed it with a different `content`, `layout` or
`theme` selection without re-registering every other app's routes.
"""

import json
from pathlib import Path

import pytest
from hydra import compose, initialize
from starlette.testclient import TestClient

from open_apps.apps.onlineshop_app import main as shop


def build_client(tmp_path, overrides=None):
    """Compose a config, seed the shop from it, and return a client."""
    with initialize(version_base=None, config_path="../config/"):
        config = compose(
            config_name="config",
            overrides=[f"logs_dir={tmp_path}"] + list(overrides or []),
        )
    Path(config.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(config.databases_dir).mkdir(parents=True, exist_ok=True)
    shop.set_environment(config.apps)
    return TestClient(shop.app)


@pytest.fixture
def client(tmp_path):
    return build_client(tmp_path)


def state(client) -> dict:
    return client.get("/onlineshop_all").json()


class TestRoutes:
    @pytest.mark.parametrize(
        "path",
        [
            "/onlineshop",
            "/onlineshop/cart",
            "/onlineshop/orders",
            "/onlineshop/checkout",
            "/onlineshop/item/elec-hdph-001",
            "/onlineshop/search/coffee/1",
            "/onlineshop/category/furniture/1",
            "/onlineshop_all",
        ],
    )
    def test_route_renders(self, client, path):
        assert client.get(path).status_code == 200

    def test_unknown_product_does_not_500(self, client):
        response = client.get("/onlineshop/item/no-such-sku")
        assert response.status_code == 200
        assert "not found" in response.text.lower()

    def test_search_form_redirects_to_canonical_url(self, client):
        response = client.post(
            "/onlineshop/search", data={"search_query": "Oak Table"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/onlineshop/search/oak,table/1"


class TestSearch:
    def test_title_match_ranks_first(self, client):
        results = shop.search_products("oak dining table")
        assert results[0].sku == "furn-table-201"

    def test_option_values_are_searchable(self, client):
        """The original indexed option text alongside the description."""
        skus = {p.sku for p in shop.search_products("walnut")}
        assert "furn-desk-205" in skus  # walnut is a finish option, not in the text

    def test_partial_match_still_returns_results(self, client):
        """Tokens are OR-ed, as the Lucene-backed original behaved."""
        assert shop.search_products("coffee unicorn") != []

    def test_fts_operators_in_user_input_are_not_executed(self, client):
        """An unbalanced quote or a bare NEAR must not raise."""
        for query in ['"', "NEAR(", "*", "a OR", "^%$"]:
            client.get(f"/onlineshop/search/{query}/1")
            shop.search_products(query)

    def test_no_match_renders_empty_state(self, client):
        response = client.get("/onlineshop/search/zzzznotathing/1")
        assert response.status_code == 200
        assert "No products matched" in response.text


class TestCart:
    def test_add_to_cart_records_options(self, client):
        client.post(
            "/onlineshop/cart/add/furn-table-201",
            data={"option_finish": "walnut", "option_size": "seats six", "quantity": 2},
        )
        line = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"][0]
        assert line["quantity"] == 2
        assert line["options"] == {"finish": "walnut", "size": "seats six"}

    def test_same_product_same_options_merges(self, client):
        for _ in range(2):
            client.post(
                "/onlineshop/cart/add/furn-table-201",
                data={"option_finish": "walnut", "option_size": "seats six"},
            )
        lines = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"]
        assert len(lines) == 1
        assert lines[0]["quantity"] == 2

    def test_same_product_different_options_does_not_merge(self, client):
        client.post("/onlineshop/cart/add/furn-table-201",
                    data={"option_finish": "walnut", "option_size": "seats six"})
        client.post("/onlineshop/cart/add/furn-table-201",
                    data={"option_finish": "natural", "option_size": "seats six"})
        lines = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"]
        assert len(lines) == 2

    def test_option_order_does_not_split_the_line(self, client):
        """`_options_key` sorts, so form field order cannot create a duplicate."""
        assert shop._options_key({"size": "m", "color": "navy"}) == shop._options_key(
            {"color": "navy", "size": "m"}
        )

    def test_unavailable_option_is_dropped(self, client):
        client.post("/onlineshop/cart/add/furn-table-201",
                    data={"option_finish": "neon pink"})
        line = [c for c in state(client)["cart"] if c["sku"] == "furn-table-201"][0]
        assert line["options"] == {}

    def test_remove_and_quantity_and_toggle(self, client):
        before = state(client)["cart"]
        assert before, "the default content seeds a non-empty cart"

        client.post("/onlineshop/cart/add/offi-pen-702",
                    data={"option_nib": "fine", "option_finish": "black"})
        row_id = [r.id for r in shop.cart_items() if r.sku == "offi-pen-702"][0]

        client.post(f"/onlineshop/cart/quantity/{row_id}", data={"quantity": 5})
        assert shop._row(shop.cart_items, row_id).quantity == 5

        client.post(f"/onlineshop/cart/toggle/{row_id}")
        assert not shop._row(shop.cart_items, row_id).selected

        client.post(f"/onlineshop/cart/remove/{row_id}")
        assert shop._row(shop.cart_items, row_id) is None

    def test_cart_line_shows_a_line_total(self, client):
        client.post("/onlineshop/cart/add/offi-pen-702",
                    data={"option_nib": "fine", "option_finish": "black", "quantity": 3})
        body = client.get("/onlineshop/cart").text
        assert "$89.00 each" in body
        assert "$267.00" in body  # 89.00 x 3

    def test_lines_differing_only_by_options_are_distinguishable(self, client):
        """Two lines of one product differ *only* by options, so the options
        have to be the most visible thing on the line."""
        for nib in ("fine", "broad"):
            client.post("/onlineshop/cart/add/offi-pen-702",
                        data={"option_nib": nib, "option_finish": "black"})
        body = client.get("/onlineshop/cart").text
        assert body.count('class="option-chip"') >= 4  # 2 options x 2 lines
        assert "nib: fine" in body and "nib: broad" in body

    def test_every_page_can_navigate_back_to_the_shop(self, client):
        """`clickable_logo` defaults to false and is a variation axis, so
        navigation cannot depend on the header."""
        for path in ("/onlineshop/cart", "/onlineshop/orders",
                     "/onlineshop/item/offi-pen-702", "/onlineshop/search/coffee/1"):
            assert 'href="/onlineshop"' in client.get(path).text, path

    def test_adding_unknown_sku_is_a_noop(self, client):
        before = len(state(client)["cart"])
        client.post("/onlineshop/cart/add/no-such-sku", data={"quantity": 1})
        assert len(state(client)["cart"]) == before


class TestCheckout:
    def test_checkout_moves_selected_cart_lines_into_an_order(self, client):
        cart_before = state(client)["cart"]
        expected_total = round(
            sum(line["unit_price"] * line["quantity"] for line in cart_before), 2
        )

        client.post("/onlineshop/checkout",
                    data={"name": "Dana Reed", "address": "9 Mill Lane"})

        after = state(client)
        assert after["cart"] == []
        order = after["orders"][-1]
        assert order["name"] == "Dana Reed"
        assert order["status"] == "Processing"
        assert order["total"] == expected_total
        assert len(order["items"]) == len(cart_before)

    def test_deselected_lines_stay_in_the_cart(self, client):
        row_id = [r.id for r in shop.cart_items()][0]
        client.post(f"/onlineshop/cart/toggle/{row_id}")
        client.post("/onlineshop/checkout", data={"name": "A", "address": "B"})

        remaining = [line["sku"] for line in state(client)["cart"]]
        assert remaining == [shop._row(shop.cart_items, row_id).sku]

    def test_empty_selection_does_not_create_an_order(self, client):
        for row in shop.cart_items():
            client.post(f"/onlineshop/cart/toggle/{row.id}")
        before = len(state(client)["orders"])
        client.post("/onlineshop/checkout", data={"name": "A", "address": "B"})
        assert len(state(client)["orders"]) == before

    def test_rejected_card_does_not_create_an_order(self, tmp_path):
        client = build_client(
            tmp_path, ["apps.onlineshop.enable_credit_card_check=true"]
        )
        before = len(state(client)["orders"])
        client.post(
            "/onlineshop/checkout",
            data={"name": "A", "address": "B", "card_type": "Diners Club"},
        )
        assert len(state(client)["orders"]) == before

    def test_allowed_card_creates_an_order(self, tmp_path):
        client = build_client(
            tmp_path, ["apps.onlineshop.enable_credit_card_check=true"]
        )
        before = len(state(client)["orders"])
        client.post(
            "/onlineshop/checkout",
            data={"name": "A", "address": "B", "card_type": "Visa"},
        )
        assert len(state(client)["orders"]) == before + 1


class TestRewardState:
    def test_state_is_json_serialisable_and_flat(self, client):
        payload = state(client)
        assert set(payload) == {"cart", "orders"}
        # The previous implementation keyed order lines by a stringified
        # Python tuple that needed ast.literal_eval to read back.
        for order in payload["orders"]:
            assert isinstance(order["items"], list)
            for item in order["items"]:
                assert isinstance(item["options"], dict)
        json.dumps(payload)

    def test_seeded_state_matches_the_config(self, client):
        payload = state(client)
        assert [line["sku"] for line in payload["cart"]] == [
            "elec-hdph-001", "home-brew-102"
        ]
        assert [order["order_id"] for order in payload["orders"]] == [
            "0efcf51f", "09bd3dfb"
        ]

    def test_table_names_are_plural_and_not_reserved_words(self, client):
        """The schema is meant to be read directly by other apps and by
        humans. fastlite would otherwise singularise `Order` to `order`, a
        SQL reserved word that breaks `SELECT * FROM order`."""
        names = set(shop.db.table_names())
        assert {"products", "cart_items", "orders", "order_items"} <= names
        assert "order" not in names
        # and the obvious query works unquoted
        assert shop.db.q("SELECT order_id FROM orders LIMIT 1") is not None

    def test_catalog_is_opt_in(self, client):
        assert "catalog" not in state(client)
        payload = client.get("/onlineshop_all?include_catalog=true").json()
        assert len(payload["catalog"]) == 40

    def test_reseeding_is_idempotent(self, tmp_path):
        """set_environment runs again on reset; it must not double-insert."""
        client = build_client(tmp_path)
        first = state(client)
        client = build_client(tmp_path)
        assert state(client) == first


class TestVariations:
    @pytest.mark.parametrize("layout", ["default", "grid", "compact_table"])
    def test_every_layout_renders(self, tmp_path, layout):
        client = build_client(tmp_path, [f"apps/onlineshop/layout={layout}"])
        assert client.get("/onlineshop").status_code == 200
        assert client.get("/onlineshop/search/coffee/1").status_code == 200

    def test_layouts_produce_different_markup(self, tmp_path):
        markup = {}
        for layout in ("default", "grid", "compact_table"):
            client = build_client(tmp_path, [f"apps/onlineshop/layout={layout}"])
            markup[layout] = client.get("/onlineshop").text
        assert 'class="product-grid"' in markup["grid"]
        assert 'class="product-table"' in markup["compact_table"]
        assert 'class="product-row"' in markup["default"]
        assert len({*markup.values()}) == 3

    def test_compact_table_has_no_imagery(self, tmp_path):
        client = build_client(tmp_path, ["apps/onlineshop/layout=compact_table"])
        assert "<svg" not in client.get("/onlineshop").text

    @pytest.mark.parametrize("theme", ["default", "dark", "solarized", "mono"])
    def test_theme_tokens_are_emitted(self, tmp_path, theme):
        client = build_client(tmp_path, [f"apps/theme={theme}"])
        body = client.get("/onlineshop").text
        assert "--color-bg" in body and "--color-primary" in body

    def test_theme_changes_the_rendered_tokens(self, tmp_path):
        light = build_client(tmp_path, ["apps/theme=default"]).get("/onlineshop").text
        dark = build_client(tmp_path, ["apps/theme=dark"]).get("/onlineshop").text
        assert light != dark

    def test_per_app_theme_overrides_the_global_one(self, tmp_path):
        client = build_client(
            tmp_path, ["apps/theme=default", "apps.onlineshop.theme=solarized"]
        )
        # solarized's background token, which `default` never emits.
        assert "#fdf6e3" in client.get("/onlineshop").text

    def test_german_content_translates_the_category_strip(self, tmp_path):
        client = build_client(tmp_path, ["apps/onlineshop/content=german"])
        body = client.get("/onlineshop").text
        assert "Möbel" in body and "Lebensmittel" in body

    def test_adversarial_content_reaches_the_page(self, tmp_path):
        client = build_client(
            tmp_path, ["apps/onlineshop/content=adversarial_descriptions"]
        )
        assert client.get("/onlineshop").status_code == 200


class TestNoEgress:
    """The old shop pointed every thumbnail at an Amazon CDN URL."""

    @pytest.mark.parametrize(
        "path",
        ["/onlineshop", "/onlineshop/cart", "/onlineshop/item/elec-hdph-001"],
    )
    def test_pages_reference_no_external_hosts(self, client, path):
        # `testserver` is TestClient's own base host, `www.w3.org` is the SVG
        # namespace declaration -- neither is a request the browser makes.
        allowed = ("localhost", "testserver", "www.w3.org")
        body = client.get(path).text
        for marker in ("http://", "https://"):
            for chunk in body.split(marker)[1:]:
                host = chunk.split("/")[0].split('"')[0]
                assert host in allowed, f"external host {host}"

- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
<!-- - [If using Conda] Create a fresh venv: `uv venv --python "$(which python)"` -->

0) Clone [repo](https://github.com/facebookresearch/OpenApps)

1) Install packages: `uv sync`

2) Activate environment: `source .venv/bin/activate`

3) Install `playwright install chromium`

That is the whole installation. **Every app, the online shop included, runs
from `uv sync` with no further setup** — no JDK, no dataset download, no
model weights. Launch with:

```
uv run launch.py
```

/// details | Optionally install Java 21 for map route planning

The map app shells out to an [OpenTripPlanner](https://www.opentripplanner.org/)
server, which is Java. Only `apps.maps.allow_planning` needs it — every other
app, and the map app's browsing and saved places, work without it.

4) Install OpenJDK 21: `chmod +x setup.sh` and `./setup.sh` for **Linux X64** or **Mac ARM64** systems

5) Designate Java path: `source setup_javapath.sh` for **Linux X64** or **Mac ARM64** systems

6) Check `java -version` gives you `java version "21.0.1"`

**Remember to run `source setup_javapath.sh` in future shells before launching map-planning tasks.**
///

## The online shop

The shop is enabled by default and needs no setup step:

```bash
uv run launch.py                                # shop included, at /onlineshop
uv run launch.py apps.onlineshop.enable=False   # turn it off
```

/// details | It used to need OpenJDK 21 and a dataset download

Earlier versions were a port of Princeton's
[WebShop](https://github.com/princeton-nlp/WebShop): ~1000 scraped Amazon
products fetched from Google Drive by `gdown`, searched through a Lucene index
built by `pyserini` — a JNI binding, hence the JDK — plus a spaCy model for a
reward function. That is why it shipped disabled.

If you are following an older guide: `setup_pyserini.sh` no longer exists,
`setup.sh` no longer downloads a dataset, and `apps.onlineshop.enable=True` is
no longer needed. The Google Drive links the old `setup.sh` used are also dead,
so that path cannot be followed even on an older checkout.
///

### Varying the shop

It composes the same way as every other app:

```bash
uv run launch.py apps/onlineshop/layout=grid            # default | grid | compact_table
uv run launch.py apps/onlineshop/content=german         # or long_descriptions, adversarial_...
uv run launch.py apps.onlineshop.theme=solarized        # or apps/theme=solarized globally
uv run launch.py apps.onlineshop.products_per_page=5
```

The catalog itself lives in `config/apps/onlineshop/content/default.yaml` — 40
products across 8 categories. Add or edit products there, or override
`apps.onlineshop.products` from another content file to swap the catalog
wholesale.

### Inspecting the shop's data

Each launch writes a fresh SQLite database under the run's `log_outputs`
directory, so the newest one is the run you were just clicking through:

```bash
DB=$(ls -t log_outputs/*/databases/onlineshop.db | head -1)
sqlite3 "$DB" ".tables"
sqlite3 -header -column "$DB" "SELECT * FROM cart_items;"
```

Four plain tables — `products`, `cart_items`, `orders`, `order_items` — plus
the `products_fts*` search index. Nothing needs quoting, and joins read the
way you would expect:

```sql
SELECT p.title, c.options, c.quantity, printf('%.2f', p.price * c.quantity) AS line_total
FROM cart_items c JOIN products p USING(sku);
```

The same state is served as JSON at `/onlineshop_all`, which is what rewards
are computed from.

### How it works

| Concern | Implementation |
| --- | --- |
| Catalog | Seeded from Hydra `content` config into the `products` table on launch |
| Search | SQLite **FTS5** with its built-in `bm25()`, weighted title > options > bullets > description |
| Persistence | One SQLite file, four relational tables, via `fastlite` |
| Rendering | FastHTML against the shared design tokens (`apps/theme=`) with a `layout` group |
| Imagery | A deterministic inline SVG swatch per sku — no files, no outbound requests |
| Reward surface | `GET /onlineshop_all` → `{"cart": [...], "orders": [...]}` |

Search is the part worth knowing about. FTS5 is compiled into Python's
`sqlite3` module, so BM25 ranking costs no dependency at all — that is what
replaced Lucene. User input is tokenised and each token quoted before being
OR-ed into a `MATCH` expression, so FTS5 operators typed into the search box
(`*`, `NEAR`, a stray quote) are matched literally instead of being executed.
Tokens are OR-ed rather than AND-ed so a query returns its best partial
matches, which is how the Lucene-backed original behaved.

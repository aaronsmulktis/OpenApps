
- Pre-requisite: install uv (a much faster pip): `pip install uv` (or from [source](https://docs.astral.sh/uv/getting-started/installation/))
<!-- - [If using Conda] Create a fresh venv: `uv venv --python "$(which python)"` -->

0) Clone [repo](https://github.com/facebookresearch/OpenApps)

1) Install packages: `uv sync`

2) Activate environment: `source .venv/bin/activate`

3) Install `playwright install chromium`

That is the whole installation — every app, the online shop included, runs
from `uv sync` with no further setup.

/// details | Optionally install Java 21 for map route planning

The map app shells out to an OpenTripPlanner server, which is Java. Only
`apps.maps.allow_planning` needs it; skip this unless you want route planning.

4) Install OpenJDK 21: `chmod +x setup.sh` and `./setup.sh` for **Linux X64** or **Mac ARM64** systems

5) Designate Java path: `source setup_javapath.sh` for **Linux X64** or **Mac ARM64** systems

6) Check `java -version` gives you `java version "21.0.1"`

**Remember to run `source setup_javapath.sh` in future shells before launching map-planning tasks.**
///

/// details | A note on the online shop

The shop used to need OpenJDK 21, a product dataset downloaded from Google
Drive, a spaCy model, and a Lucene index built with `pyserini`. It now seeds
its catalog from `config/apps/onlineshop/content/` and searches with SQLite
FTS5, so it has no setup step and is enabled by default. Turn it off with:

```
uv run launch.py apps.onlineshop.enable=False
```
///

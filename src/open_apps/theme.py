"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

Shared design-token theming for OpenApps.

A *theme* is a set of design tokens (colors, typography, shape, spacing)
defined once in ``config/apps/theme/<name>.yaml`` and shared across every
app. Selecting a theme emits a ``:root { --token: value }`` block that all
apps consume via ``var(--token)``. This decouples *look* (theme) from
*structure* (each app's ``layout``).

Selection is done with Hydra overrides:

* ``+apps/theme=solarized``      -> global default for every app (requires adding the group if not in defaults)
* ``apps.todo.theme=solarized``  -> override a single app (falls back to
  the global theme when the app's ``theme`` field is null/unset)

A theme file looks like::

    # @package apps.theme
    name: solarized
    import_url: ""          # optional external stylesheet escape hatch
    tokens:
      color-bg: "#fdf6e3"
      color-fg: "#657b83"
      color-primary: "#268bd2"
      font-family: "'Inter', sans-serif"
      radius: "8px"
      ...

The ``tokens`` mapping is open-ended: every ``key: value`` becomes the CSS
custom property ``--key: value``, so apps can introduce new tokens without
touching this module.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fasthtml.common import Style

# Repo-root/config/apps/theme -- this file lives at src/open_apps/theme.py.
_THEME_DIR = Path(__file__).resolve().parents[2] / "config" / "apps" / "theme"

_DEFAULT_THEME = "default"


def _as_plain(value):
    """Coerce an OmegaConf node (or anything mapping-like) to a plain dict."""
    if value is None:
        return {}
    # OmegaConf DictConfig exposes ``items``; so does a plain dict.
    if hasattr(value, "items"):
        return {k: v for k, v in value.items()}
    return dict(value)


def load_theme(name: str) -> dict:
    """Load a theme's tokens from ``config/apps/theme/<name>.yaml``.

    Returns a dict with at least ``name``, ``tokens`` and ``import_url``.
    Falls back to the default theme when ``name`` is unknown so a bad
    override degrades gracefully instead of raising.
    """
    path = _THEME_DIR / f"{name}.yaml"
    if not path.exists():
        path = _THEME_DIR / f"{_DEFAULT_THEME}.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("name", name)
    data.setdefault("tokens", {})
    data.setdefault("import_url", "")
    return data


def resolve_theme(apps_config, app_name: str) -> dict:
    """Resolve the effective theme for ``app_name``.

    ``apps_config`` is the ``config.apps`` node handed to every app as
    ``app.config``. Precedence: per-app ``apps.<app_name>.theme`` (a theme
    name string) overrides the global ``apps.theme`` group; a null/unset
    per-app value inherits the global theme.
    """
    app_cfg = getattr(apps_config, app_name, None)
    per_app = getattr(app_cfg, "theme", None) if app_cfg is not None else None
    if per_app:
        return load_theme(str(per_app))

    global_theme = getattr(apps_config, "theme", None)
    if global_theme is not None:
        # Allow global theme to be provided either as a composed config node
        # (apps/theme=<name>) or as a plain string override (apps.theme=<name>). 
        if isinstance(global_theme, str):
            return load_theme(global_theme)
        theme = _as_plain(global_theme)
        theme.setdefault("tokens", {})
        theme["tokens"] = _as_plain(theme["tokens"])
        return theme

    return load_theme(_DEFAULT_THEME)


def render_theme_tokens(theme: dict) -> Style:
    """Build the ``:root`` CSS-variable block (plus optional import) for a theme.

    ``theme`` is the dict returned by :func:`resolve_theme` / :func:`load_theme`.
    """
    tokens = _as_plain(theme.get("tokens", {}))

    safe_lines: list[str] = []
    for key, value in tokens.items():
        key = str(key)
        # Allow only simple custom-property names to avoid broken CSS/injection.
        if (not key) or any(not (c.isalnum() or c in "-_") for c in key):
            continue
        val = str(value).replace("\n", " ").replace("\r", " ")
        safe_lines.append(f"  --{key}: {val};")

    lines = "\n".join(safe_lines)

    import_url = (theme.get("import_url") or "").strip()
    # Avoid breaking out of the quoted @import string.
    if any(c in import_url for c in ('"', "'", "\n", "\r")):
        import_url = ""

    import_rule = f'@import url("{import_url}");\n' if import_url else ""
    css = f"{import_rule}:root {{\n{lines}\n}}"
    return Style(css)


def theme_style(apps_config, app_name: str) -> Style:
    """Convenience: resolve + render the token block for ``app_name`` in one call.

    Call this per-request so live ``reconfigure`` theme swaps take effect.
    """
    return render_theme_tokens(resolve_theme(apps_config, app_name))

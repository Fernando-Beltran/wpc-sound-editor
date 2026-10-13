"""Fetching things over HTTP, with the request headers under config.

Every HTTP client lets you set the user agent, the timeout and the
referer; this one keeps them in a settings file so they can be changed
without touching code. The defaults identify this program honestly and
name the project, which is what a well-behaved client sends.

Settings live in `ajustes.json` beside the project (or wherever
`WPCIDE_SETTINGS` points), and only the keys present override the
defaults:

    {
      "user_agent": "...",
      "timeout": 20,
      "max_bytes": 8388608,
      "referer": null
    }
"""

import json
import os
import pathlib
import urllib.error
import urllib.request


def _default_settings_file():
    """The user's settings, not a file in the working directory."""
    named = os.environ.get("WPCIDE_SETTINGS")
    if named:
        return named
    try:
        from wpcide import paths
        return str(paths.user_data("ajustes.json"))
    except Exception:          # noqa: BLE001 -- usable without the editor
        return "ajustes.json"


VERSION = "0.1"
DEFAULT_USER_AGENT = (
    "WPCSoundEditor/%s (+https://github.com/; editor de audio para ROM WPC)" % VERSION
)

DEFAULTS = {
    "user_agent": DEFAULT_USER_AGENT,
    "timeout": 20,
    # A ceiling so a mistyped URL cannot pull a DVD into memory.
    "max_bytes": 8 * 1024 * 1024,
    "referer": None,
}


class FetchError(Exception):
    """The request did not come back with something usable."""


def settings(path=None):
    """The current settings, defaults filled in for anything missing."""
    merged = dict(DEFAULTS)
    try:
        with open(str(path or _default_settings_file()), encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass          # no settings file is the normal case
    return merged


def save_settings(values, path=None):
    """Writes the settings back, keeping only the keys we know."""
    target = pathlib.Path(str(path or _default_settings_file()))
    current = settings(target)
    current.update({k: v for k, v in values.items() if k in DEFAULTS})
    target.write_text(json.dumps(current, indent=1, ensure_ascii=False),
                      encoding="utf-8")
    return current


def fetch(url, path=None, **overrides):
    """Gets `url` and returns `(bytes, content_type)`.

    Headers come from the settings file; `overrides` beat it for a
    single call.
    """
    if not str(url).lower().startswith(("http://", "https://")):
        raise FetchError("solo http y https: %r" % url)

    options = settings(path)
    options.update({k: v for k, v in overrides.items() if v is not None})

    headers = {"User-Agent": options["user_agent"]}
    if options.get("referer"):
        headers["Referer"] = options["referer"]

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=options["timeout"]) as answer:
            payload = answer.read(options["max_bytes"] + 1)
            kind = answer.headers.get_content_type()
    except urllib.error.HTTPError as problem:
        raise FetchError("el servidor respondio %s" % problem.code)
    except Exception as problem:                          # noqa: BLE001
        raise FetchError(str(problem))

    if len(payload) > options["max_bytes"]:
        raise FetchError("la respuesta pasa de %d bytes" % options["max_bytes"])
    return payload, kind

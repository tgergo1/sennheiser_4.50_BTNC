"""Fetching correction curves from the AutoEq database.

AutoEq collects headphone frequency-response measurements from a couple of
dozen independent measurers and publishes, for each one, the filters that
correct it towards a target. That covers thousands of models, so the same
machinery that fixes the HD 4.50 BTNC fixes anything else you own.

Only the small parametric text file is fetched, and it is read by the same
parser used for the shipped profile.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import Calibration, parse_parametric

API = "https://api.github.com/repos/jaakkopasanen/AutoEq/contents"
RAW = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master"

#: Measurers worth searching first. oratory1990 is the usual reference for
#: over-ear headphones; the others cover much of what it does not.
DEFAULT_SOURCES = ("oratory1990", "crinacle", "Rtings", "Innerfidelity")

TIMEOUT = 20.0


class AutoEqError(RuntimeError):
    pass


def _get_json(url: str):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise AutoEqError(
                "GitHub is rate-limiting anonymous requests; try again in a few minutes"
            ) from error
        raise AutoEqError(f"AutoEq request failed: {error}") from error
    except urllib.error.URLError as error:
        raise AutoEqError(f"could not reach AutoEq: {error.reason}") from error


def _directories(path: str) -> list[str]:
    url = f"{API}/{urllib.parse.quote(path)}"
    return [entry["name"] for entry in _get_json(url) if entry["type"] == "dir"]


def search(model: str, sources: tuple[str, ...] = DEFAULT_SOURCES) -> list[tuple[str, str, str]]:
    """Find measurements whose name contains ``model``.

    Returns (source, category, model) triples, best match first: an exact name
    beats a prefix, and a prefix beats a mention elsewhere in the name.
    """
    wanted = model.lower().strip()
    if not wanted:
        raise ValueError("give a model name to search for")

    matches = []
    for source in sources:
        try:
            categories = _directories(f"results/{source}")
        except AutoEqError:
            continue
        for category in categories:
            try:
                models = _directories(f"results/{source}/{category}")
            except AutoEqError:
                continue
            for name in models:
                lowered = name.lower()
                if wanted not in lowered:
                    continue
                rank = 0 if lowered == wanted else 1 if lowered.startswith(wanted) else 2
                matches.append((rank, source, category, name))
    matches.sort(key=lambda entry: (entry[0], entry[1], entry[3]))
    return [(source, category, name) for _, source, category, name in matches]


def fetch(source: str, category: str, model: str) -> Calibration:
    """Download one measurement's parametric filters."""
    path = f"results/{source}/{category}/{model}/{model} ParametricEQ.txt"
    url = f"{RAW}/{urllib.parse.quote(path)}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        raise AutoEqError(f"no parametric filters published for {model!r} by {source}") from error
    except urllib.error.URLError as error:
        raise AutoEqError(f"could not reach AutoEq: {error.reason}") from error

    return parse_parametric(
        text,
        name=model,
        source=f"{source} measurement, corrected to their target; via AutoEq ({path})",
    )

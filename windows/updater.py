"""Checks GitHub Releases for a newer version."""

import json
import re
import urllib.request

from version import UPDATE_REPO, __version__


def _parse(version):
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3])


def check():
    """Return {"version", "url"} if a newer release exists, else None. Raises on network errors."""
    if not UPDATE_REPO:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
        headers={"User-Agent": f"ResVibranceSwitch/{__version__}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        release = json.load(response)
    tag = release.get("tag_name", "")
    if _parse(tag) > _parse(__version__):
        return {"version": tag.lstrip("vV"), "url": release.get("html_url")}
    return None

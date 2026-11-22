#!/usr/bin/env python3
"""Keep the profile README's organization section in sync with GitHub."""

import json
import os
from html import escape
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

START = "<!-- organizations:start -->"
END = "<!-- organizations:end -->"
PROFILE = "holylovenia"
README = Path("README.md")


def fetch_organizations():
    # Public memberships are safe to show in a publicly readable README.
    # Private memberships are opt-in because publishing them reveals them.
    include_private = os.getenv("INCLUDE_PRIVATE_ORGS", "false").lower() == "true"
    if include_private:
        token = os.environ.get("METRICS_TOKEN")
        if not token:
            raise RuntimeError("METRICS_TOKEN is required for private organization memberships")
        endpoint = "https://api.github.com/user/orgs"
    else:
        token = None
        endpoint = f"https://api.github.com/users/{quote(PROFILE)}/orgs"

    if include_private:
        # Never publish memberships from an accidentally misconfigured token.
        identity_request = Request(
            "https://api.github.com/user",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "holylovenia-profile-readme",
                     "Authorization": f"Bearer {token}"},
        )
        with urlopen(identity_request, timeout=30) as response:
            identity = json.load(response)
        if identity.get("login", "").lower() != PROFILE.lower():
            raise RuntimeError("METRICS_TOKEN must belong to holylovenia")

    organizations = {}
    for page in range(1, 101):
        request = Request(
            f"{endpoint}?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "holylovenia-profile-readme",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError("Unexpected organization API response")
        for org in batch:
            login = org.get("login")
            if not login:
                raise ValueError("Organization without a login")
            organizations[login.lower()] = org
        if len(batch) < 100:
            break
    else:
        raise RuntimeError("Too many organization pages; update pagination limit")
    return sorted(organizations.values(), key=lambda org: org["login"].lower())


def render(organizations):
    rows = [f"### Organizations ({len(organizations)})", ""]
    if not organizations:
        rows.append("_No organization memberships were returned by GitHub._")
    for org in organizations:
        login = org["login"]
        url = f"https://github.com/{quote(login)}"
        # Generated from GitHub's own organization metadata.
        avatar = org.get("avatar_url") or ""
        logo = (
            f'<img src="{escape(avatar, quote=True)}" width="24" height="24" '
            f'alt="" /> '
            if avatar.startswith("https://")
            else ""
        )
        rows.append(f"- {logo}[{escape(login)}]({url})")
    return "\n".join(rows)


def main():
    text = README.read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise RuntimeError("README must contain exactly one organizations marker pair")
    left, rest = text.split(START, 1)
    _, right = rest.split(END, 1)
    updated = f"{left}{START}\n{render(fetch_organizations())}\n{END}{right}"
    if text != updated:
        README.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Keep organization memberships in the profile SVG up to date."""

import base64
import hashlib
import json
import os
import re
from html import escape
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

PROFILE = "holylovenia"
README = Path("README.md")
METRICS = Path("github-metrics.svg")


def fetch_organizations():
    # Private memberships are opt-in because embedding them in the public SVG
    # makes those memberships visible to everyone.
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



def embedded_avatar(organization):
    """Fetch small GitHub avatars as data URIs so SVG-as-image can display them."""
    url = organization.get("avatar_url", "")
    if urlsplit(url).hostname != "avatars.githubusercontent.com":
        return ""
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}s=48",
        headers={"User-Agent": "holylovenia-profile-readme"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ("image/png", "image/jpeg", "image/webp"):
                return ""
            data = response.read(200_001)
            if len(data) > 200_000:
                return ""
    except (OSError, URLError, TimeoutError):
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    return (
        f'<img class="avatar organization" width="22" height="22" '
        f'src="data:{content_type};base64,{encoded}" alt="" />'
    )


def render_svg_section(organizations):
    lines = [
        '            <section id="profile-organizations">',
        '                <h2 class="field">',
        '                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">',
        '                        <path d="M1.5 2.5h9v12h-9zM10.5 6h4v8.5h-4M4 5h1M7 5h1M4 8h1M7 8h1M4 11h1M7 11h1" fill="none" stroke="#959da5" stroke-width="1.5" stroke-linejoin="round"/>',
        '                    </svg>',
        f'                    Organizations ({len(organizations)})',
        '                </h2>',
    ]
    if not organizations:
        lines.append('                <div class="field">No public memberships found</div>')
    for org in organizations:
        avatar = embedded_avatar(org)
        if not avatar:
            avatar = '<span style="display:inline-block;width:22px;text-align:center">•</span>'
        lines.append(
            f'                <div class="field">{avatar}<span>{escape(org["login"])}</span></div>'
        )
    lines.append('            </section>')
    return "\n".join(lines)


def update_svg(organizations):
    svg = METRICS.read_text(encoding="utf-8")
    # Support rerunning the updater before Metrics regenerates the SVG.
    previous_section = re.compile(
        r'\n            <section id="profile-organizations">.*?\n            </section>',
        re.DOTALL,
    )
    svg = previous_section.sub("", svg)
    old_height = re.search(r' data-profile-organizations-height="(\d+)"', svg[:500])
    previous_extra = int(old_height.group(1)) if old_height else 0
    if old_height:
        svg = svg.replace(old_height.group(0), "", 1)

    header = re.match(r'<svg\b[^>]*>', svg)
    if not header:
        raise RuntimeError("Metrics SVG root element was not found")
    height = re.search(r'\bheight="(\d+)"', header.group())
    if not height:
        raise RuntimeError("Metrics SVG height was not found")
    # 22px avatars plus the existing 2px row gap, and a compact heading.
    # Avoid reserving unnecessary whitespace beneath the final calendar.
    extra = 34 + 24 * max(1, len(organizations))
    new_height = int(height.group(1)) - previous_extra + extra
    new_header = header.group().replace(
        height.group(), f'height="{new_height}"', 1
    )[:-1] + f' data-profile-organizations-height="{extra}">'
    svg = new_header + svg[header.end():]

    # Put organizations where Coding Habits used to appear: after the
    # isometric calendar, immediately before Notable contributions.
    notable = svg.find("Notable contributions")
    position = svg.rfind('            <section>\n', 0, notable)
    if notable < 0 or position < 0:
        raise RuntimeError("Could not locate the Notable contributions section")
    updated = svg[:position] + render_svg_section(organizations) + "\n" + svg[position:]
    if updated != METRICS.read_text(encoding="utf-8"):
        METRICS.write_text(updated, encoding="utf-8")


def main():
    # The README contains just the metrics image; organizations live in the SVG.
    organizations = fetch_organizations()
    update_svg(organizations)
    # Use the final SVG contents as a stable cache-buster for GitHub's image proxy.
    text = README.read_text(encoding="utf-8")
    svg_digest = hashlib.sha256(METRICS.read_bytes()).hexdigest()[:12]
    image_pattern = r'(?m)^!\[Metrics\]\(\./github-metrics\.svg(?:\?v=[^)]+)?\)$'
    updated, replacements = re.subn(
        image_pattern,
        f'![Metrics](./github-metrics.svg?v={svg_digest})',
        text,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError("README Metrics image link was not found")
    if text != updated:
        README.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()

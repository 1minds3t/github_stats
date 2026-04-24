#!/usr/bin/python3
"""
Replacement for generate_images.py
- No dependency on broken /stats/contributors endpoint
- Languages weighted by commit share per repo
- LOC from shallow clones of owned non-fork repos
- Days/commits calculated from account creation date
"""

import asyncio
import os
import re
import sys
import subprocess
import tempfile
import shutil
from datetime import datetime, timezone
from typing import Dict, Tuple

import aiohttp
import requests


###############################################################################
# Config
###############################################################################

OWNER = os.environ.get("GITHUB_ACTOR", "1minds3t")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
EXCLUDE_LANGS = {x.strip().lower() for x in os.environ.get("EXCLUDED_LANGS", "").split(",") if x.strip()}
EXCLUDE_FORKED = os.environ.get("EXCLUDE_FORKED_REPOS", "false").lower() == "true"
MIN_COMMIT_SHARE = 0.01  # ignore repos where you have <1% of commits

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
}

GQL = "https://api.github.com/graphql"


###############################################################################
# API helpers
###############################################################################

def gql(query: str) -> dict:
    r = requests.post(GQL, headers=HEADERS, json={"query": query}, timeout=30)
    r.raise_for_status()
    return r.json()


def rest(path: str, params: dict = None):
    url = f"https://api.github.com/{path.lstrip('/')}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def rest_last_page(path: str, params: dict = None) -> int:
    """Return total count via Link header last page trick."""
    url = f"https://api.github.com/{path.lstrip('/')}"
    p = dict(params or {})
    p["per_page"] = 1
    r = requests.get(url, headers=HEADERS, params=p, timeout=30)
    if r.status_code == 409:  # empty repo
        return 0
    r.raise_for_status()
    link = r.headers.get("Link", "")
    if 'rel="last"' in link:
        m = re.search(r'page=(\d+)>; rel="last"', link)
        return int(m.group(1)) if m else (1 if r.json() else 0)
    return len(r.json())


###############################################################################
# Data collection
###############################################################################

def get_account_info() -> Tuple[datetime, int]:
    """Return (account_created_at, total_contributions)."""
    data = gql("""
    {
      viewer {
        createdAt
        contributionsCollection {
          contributionYears
        }
      }
    }
    """)
    created_at = datetime.fromisoformat(
        data["data"]["viewer"]["createdAt"].replace("Z", "+00:00")
    )
    years = data["data"]["viewer"]["contributionsCollection"]["contributionYears"]

    # Sum contributions across all years
    by_year_q = "{ viewer { " + " ".join(
        f'y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y+1}-01-01T00:00:00Z") {{ contributionCalendar {{ totalContributions }} }}'
        for y in years
    ) + " } }"
    by_year = gql(by_year_q)
    total = sum(
        v["contributionCalendar"]["totalContributions"]
        for v in by_year["data"]["viewer"].values()
    )
    return created_at, total


def get_repos() -> list:
    """Return all owned repos with language edges."""
    data = gql("""
    {
      viewer {
        repositories(first: 100, ownerAffiliations: OWNER) {
          nodes {
            nameWithOwner
            isFork
            isPrivate
            stargazers { totalCount }
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node { name color }
              }
            }
          }
        }
      }
    }
    """)
    return data["data"]["viewer"]["repositories"]["nodes"]


def get_commit_counts(repos: list) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Return (my_commits_per_repo, total_commits_per_repo)."""
    my_counts = {}
    total_counts = {}
    for repo in repos:
        name = repo["nameWithOwner"]
        print(f"  counting commits: {name}")
        my_counts[name] = rest_last_page(f"repos/{name}/commits", {"author": OWNER})
        total_counts[name] = rest_last_page(f"repos/{name}/commits")
    return my_counts, total_counts


def get_loc_from_clones(repos: list, my_counts: dict, total_counts: dict) -> Tuple[int, int]:
    """
    Shallow clone owned non-fork repos and count LOC added/deleted by author.
    For forked repos, apply commit-share estimate using language sizes.
    Returns (additions, deletions).
    """
    additions = 0
    deletions = 0
    tmpdir = tempfile.mkdtemp(prefix="ghstats_")

    try:
        for repo in repos:
            name = repo["nameWithOwner"]
            my = my_counts.get(name, 0)
            if my == 0:
                continue

            if repo["isFork"]:
                # Can't clone uv etc — skip, too large
                print(f"  skipping fork for LOC: {name}")
                continue

            print(f"  cloning for LOC: {name}")
            clone_dir = os.path.join(tmpdir, name.replace("/", "_"))
            try:
                subprocess.run(
                    ["git", "clone", "--depth=500", "--quiet",
                     f"https://x-access-token:{TOKEN}@github.com/{name}.git",
                     clone_dir],
                    check=True, capture_output=True, timeout=120
                )
                result = subprocess.run(
                    ["git", "log", f"--author={OWNER}", "--numstat",
                     "--pretty=format:", "--no-merges"],
                    cwd=clone_dir, capture_output=True, text=True, timeout=60
                )
                for line in result.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        try:
                            additions += int(parts[0])
                            deletions += int(parts[1])
                        except ValueError:
                            pass  # binary files show '-'
            except Exception as e:
                print(f"  LOC clone failed for {name}: {e}")
            finally:
                shutil.rmtree(clone_dir, ignore_errors=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return additions, deletions


def get_languages(repos: list, my_counts: dict, total_counts: dict) -> Dict:
    """Languages weighted by commit share per repo."""
    langs = {}
    for repo in repos:
        name = repo["nameWithOwner"]
        my = my_counts.get(name, 0)
        total = total_counts.get(name, 1)
        if my == 0:
            continue
        share = my / max(total, 1)
        if share < MIN_COMMIT_SHARE:
            continue
        for edge in repo.get("languages", {}).get("edges", []):
            lang = edge["node"]["name"]
            if lang.lower() in EXCLUDE_LANGS:
                continue
            color = edge["node"]["color"] or "#000000"
            weighted = edge["size"] * share
            if lang not in langs:
                langs[lang] = {"size": 0, "color": color, "occurrences": 0}
            langs[lang]["size"] += weighted
            langs[lang]["occurrences"] += 1

    total_size = sum(v["size"] for v in langs.values())
    for v in langs.values():
        v["prop"] = 100 * v["size"] / total_size if total_size > 0 else 0

    return langs


###############################################################################
# SVG generation
###############################################################################

def generate_output_folder():
    os.makedirs("generated", exist_ok=True)


def generate_overview(name: str, stars: int, contributions: int,
                      loc_added: int, loc_deleted: int,
                      total_days: int, total_commits: int) -> None:
    print("🔄 Generating overview SVG...")
    with open("templates/overview.svg", "r") as f:
        output = f.read()

    loc_changed = loc_added + loc_deleted
    loc_per_day = loc_changed // total_days if total_days > 0 else 0
    commits_per_day = f"{total_commits / total_days:.1f}" if total_days > 0 else "0"

    output = re.sub(r"\{\{\s*name\s*\}\}", name, output)
    output = re.sub(r"\{\{\s*stars\s*\}\}", f"{stars:,}", output)
    output = re.sub(r"\{\{\s*contributions\s*\}\}", f"{contributions:,}", output)
    output = re.sub(r"\{\{\s*lines_changed\s*\}\}", f"{loc_changed:,}", output)
    output = re.sub(r"\{\{\s*repos\s*\}\}", "N/A", output)
    output = re.sub(r"\{\{\s*days_coding\s*\}\}", f"{total_days:,}", output)
    output = re.sub(r"\{\{\s*lines_per_day\s*\}\}", f"{loc_per_day:,}", output)
    output = re.sub(r"\{\{\s*commits_per_day\s*\}\}", commits_per_day, output)
    output = re.sub(r"\{\{\s*total_commits\s*\}\}", f"{total_commits:,}", output)

    generate_output_folder()
    with open("generated/overview.svg", "w") as f:
        f.write(output)
    print(f"✅ Overview done — {total_days} days, {loc_per_day:,} LOC/day, {commits_per_day} commits/day")


def generate_languages(langs: Dict) -> None:
    print("🔄 Generating languages SVG...")
    with open("templates/languages.svg", "r") as f:
        output = f.read()

    sorted_langs = sorted(langs.items(), key=lambda t: t[1]["size"], reverse=True)
    progress = ""
    lang_list = ""

    for i, (lang, data) in enumerate(sorted_langs):
        color = data.get("color", "#000000")
        prop = data.get("prop", 0)
        progress += f'<span style="background-color: {color};width: {prop:0.3f}%;" class="progress-item"></span>'
        lang_list += f"""
<li style="animation-delay: {i * 150}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};" viewBox="0 0 16 16" version="1.1" width="16" height="16"><path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang}</span>
<span class="percent">{prop:0.2f}%</span>
</li>
"""

    output = re.sub(r"\{\{\s*progress\s*\}\}", progress, output)
    output = re.sub(r"\{\{\s*lang_list\s*\}\}", lang_list, output)

    generate_output_folder()
    with open("generated/languages.svg", "w") as f:
        f.write(output)
    print("✅ Languages done")


###############################################################################
# Main
###############################################################################

def main() -> None:
    if not TOKEN:
        print("❌ ACCESS_TOKEN or GITHUB_TOKEN not set")
        sys.exit(1)

    print(f"🚀 Generating stats for {OWNER}...")

    print("📅 Fetching account info...")
    created_at, total_contributions = get_account_info()
    total_days = max((datetime.now(timezone.utc) - created_at).days, 1)
    print(f"   Account created: {created_at.date()}, {total_days} days ago")
    print(f"   Total contributions: {total_contributions:,}")

    print("📦 Fetching repos...")
    repos = get_repos()
    stars = sum(r["stargazers"]["totalCount"] for r in repos)
    print(f"   {len(repos)} repos, {stars:,} stars")

    print("🔢 Counting commits per repo...")
    my_counts, total_counts = get_commit_counts(repos)
    total_commits = sum(my_counts.values())
    print(f"   Total your commits: {total_commits:,}")

    print("🌍 Calculating language breakdown...")
    langs = get_languages(repos, my_counts, total_counts)
    for lang, data in sorted(langs.items(), key=lambda x: -x[1]["size"])[:5]:
        print(f"   {lang}: {data['prop']:.1f}%")

    print("📝 Counting lines of code (cloning non-fork repos)...")
    loc_added, loc_deleted = get_loc_from_clones(repos, my_counts, total_counts)
    print(f"   LOC added: {loc_added:,}, deleted: {loc_deleted:,}")

    # Get display name
    user_data = rest(f"users/{OWNER}")
    name = user_data.get("name") or OWNER

    generate_overview(name, stars, total_contributions,
                      loc_added, loc_deleted, total_days, total_commits)
    generate_languages(langs)

    print("\n🎉 Done!")


if __name__ == "__main__":
    main()
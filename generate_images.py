#!/usr/bin/python3

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone

import aiohttp

from github_stats import Stats

# --- Helper Functions ---

def generate_output_folder() -> None:
    """Create the output folder if it does not already exist."""
    if not os.path.isdir("generated"):
        os.mkdir("generated")

async def get_user_creation_date(session, username: str, headers: dict) -> datetime:
    """Fetches the user's account creation date from the GitHub API."""
    url = f"https://api.github.com/users/{username}"
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        user_data = await response.json()
        # The timestamp is in ISO 8601 format, e.g., '2025-08-04T18:43:09Z'
        return datetime.fromisoformat(user_data['created_at'].replace("Z", "+00:00"))

async def safe_get_stat(coroutine, description: str, max_retries: int = 3, delay: int = 5):
    """Safely get a statistic with retry logic for 202 responses."""
    for attempt in range(max_retries + 1):
        try:
            result = await coroutine
            print(f"✓ Successfully got {description}")
            return result
        except Exception as e:
            if "202" in str(e) or "Accepted" in str(e):
                if attempt < max_retries:
                    wait_time = delay * (2 ** attempt)
                    print(f"⏳ {description} returned 202 (processing). Waiting {wait_time}s... (attempt {attempt + 1}/{max_retries + 1})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    print(f"❌ {description} failed after {max_retries + 1} attempts: {e}")
                    raise
            else:
                print(f"❌ {description} failed with error: {e}")
                raise
    return None

# --- Image Generation Functions ---

async def generate_overview(s: Stats, total_days: int) -> None:
    """Generate an SVG badge with summary statistics."""
    print("🔄 Generating enhanced overview badge...")
    try:
        with open("templates/overview.svg", "r") as f:
            output = f.read()

        # Get stats
        name = await safe_get_stat(s.name, "user name")
        stars = await safe_get_stat(s.stargazers, "stargazers count")
        contributions = await safe_get_stat(s.total_contributions, "total contributions")
        lines_changed_data = await safe_get_stat(s.lines_changed, "lines changed")
        repos = await safe_get_stat(s.repos, "repositories list")

        # Calculate enhanced stats
        changed = lines_changed_data[0] + lines_changed_data[1]
        lines_per_day = changed // total_days if total_days > 0 else 0
        
        # Replace template variables
        output = re.sub("{{ name }}", name, output)
        output = re.sub("{{ stars }}", f"{stars:,}", output)
        output = re.sub("{{ contributions }}", f"{contributions:,}", output)
        output = re.sub("{{ lines_changed }}", f"{changed:,}", output)
        output = re.sub("{{ repos }}", f"{len(repos):,}", output)
        output = re.sub("{{ days_coding }}", f"{total_days:,}", output)
        output = re.sub("{{ lines_per_day }}", f"{lines_per_day:,}", output)

        generate_output_folder()
        with open("generated/overview.svg", "w") as f:
            f.write(output)
        
        print(f"✅ Overview badge generated! Days coding: {total_days}, Lines/day: {lines_per_day:,}")
    except Exception as e:
        print(f"❌ Failed to generate overview badge: {e}")
        raise

async def generate_languages(s: Stats) -> None:
    """Generate an SVG badge with summary languages used."""
    print("🔄 Generating languages badge...")
    try:
        with open("templates/languages.svg", "r") as f:
            output = f.read()

        languages_data = await safe_get_stat(s.languages, "languages data")
        progress, lang_list = "", ""
        sorted_languages = sorted(languages_data.items(), reverse=True, key=lambda t: t[1].get("size"))
        
        for i, (lang, data) in enumerate(sorted_languages):
            color = data.get("color", "#000000")
            progress += f'<span style="background-color: {color};width: {data.get("prop", 0):0.3f}%;" class="progress-item"></span>'
            lang_list += f"""
<li style="animation-delay: {i * 150}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};" viewBox="0 0 16 16" version="1.1" width="16" height="16"><path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang}</span>
<span class="percent">{data.get("prop", 0):0.2f}%</span>
</li>
"""
        output = re.sub(r"{{ progress }}", progress, output)
        output = re.sub(r"{{ lang_list }}", lang_list, output)

        generate_output_folder()
        with open("generated/languages.svg", "w") as f:
            f.write(output)
        print("✅ Languages badge generated successfully!")
    except Exception as e:
        print(f"❌ Failed to generate languages badge: {e}")
        raise

# --- Main Function ---

async def main() -> None:
    """Generate all badges with enhanced statistics."""
    print("🚀 Starting enhanced GitHub stats generation...")
    
    access_token = os.getenv("ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not access_token:
        raise Exception("A personal access token is required!")
    
    user = os.getenv("GITHUB_ACTOR")
    if not user:
        raise RuntimeError("Environment variable GITHUB_ACTOR must be set.")
    
    headers = {"Authorization": f"token {access_token}"}
    timeout = aiohttp.ClientTimeout(total=300)
    
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            # STEP 1: Get the real creation date and calculate days
            creation_date = await get_user_creation_date(session, user, headers)
            total_days = max((datetime.now(timezone.utc) - creation_date).days, 1)

            print(f"📅 Account created: {creation_date.date()}. Total days: {total_days}")
            print(f"🔥 Let's calculate the real velocity!")

            # STEP 2: Initialize the Stats object
            exclude_repos = {x.strip() for x in os.getenv("EXCLUDED", "").split(",")} if os.getenv("EXCLUDED") else None
            exclude_langs = {x.strip() for x in os.getenv("EXCLUDED_LANGS", "").split(",")} if os.getenv("EXCLUDED_LANGS") else None
            ignore_forked_repos = os.getenv("EXCLUDE_FORKED_REPOS", "false").lower() == "true"

            s = Stats(user, access_token, session, exclude_repos=exclude_repos, exclude_langs=exclude_langs, ignore_forked_repos=ignore_forked_repos)
            
            # STEP 3: Generate the images, passing in the correct number of days
            print("\n📈 Starting enhanced badge generation...")
            await generate_overview(s, total_days)
            await asyncio.sleep(2) # Brief pause to respect API limits
            await generate_languages(s)
            
            print("\n🎉 All enhanced badges generated successfully!")
            
    except Exception as e:
        print(f"\n💥 Fatal error during generation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/python3

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone

import aiohttp

from github_stats import Stats


################################################################################
# Helper Functions
################################################################################


def generate_output_folder() -> None:
    """
    Create the output folder if it does not already exist
    """
    if not os.path.isdir("generated"):
        os.mkdir("generated")


def calculate_days_since_first_commit() -> int:
    """
    Calculate days since August 4th, 2024 (your first commit)
    """
    first_commit_date = datetime(2024, 8, 4, tzinfo=timezone.utc)
    current_date = datetime.now(timezone.utc)
    days_diff = (current_date - first_commit_date).days
    return max(days_diff, 1)  # At least 1 day to avoid division by zero


async def safe_get_stat(coroutine, description: str, max_retries: int = 3, delay: int = 5):
    """
    Safely get a statistic with retry logic for 202 responses
    """
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


################################################################################
# Individual Image Generation Functions
################################################################################


async def generate_overview(s: Stats) -> None:
    """
    Generate an SVG badge with summary statistics including days since first commit
    :param s: Represents user's GitHub statistics
    """
    print("🔄 Generating enhanced overview badge...")
    
    try:
        with open("templates/overview.svg", "r") as f:
            output = f.read()

        # Get all stats with retry logic
        name = await safe_get_stat(s.name, "user name")
        stars = await safe_get_stat(s.stargazers, "stargazers count")
        forks = await safe_get_stat(s.forks, "forks count")
        contributions = await safe_get_stat(s.total_contributions, "total contributions")
        lines_changed_data = await safe_get_stat(s.lines_changed, "lines changed")
        views = await safe_get_stat(s.views, "views count")
        repos = await safe_get_stat(s.repos, "repositories list")

        # Calculate enhanced stats
        changed = lines_changed_data[0] + lines_changed_data[1]
        days_since_first_commit = calculate_days_since_first_commit()
        lines_per_day = changed // days_since_first_commit if days_since_first_commit > 0 else 0
        
        # Replace template variables
        output = re.sub("{{ name }}", name, output)
        output = re.sub("{{ stars }}", f"{stars:,}", output)
        output = re.sub("{{ forks }}", f"{forks:,}", output)
        output = re.sub("{{ contributions }}", f"{contributions:,}", output)
        output = re.sub("{{ lines_changed }}", f"{changed:,}", output)
        output = re.sub("{{ views }}", f"{views:,}", output)
        output = re.sub("{{ repos }}", f"{len(repos):,}", output)
        
        # Add new stats
        output = re.sub("{{ days_coding }}", f"{days_since_first_commit:,}", output)
        output = re.sub("{{ lines_per_day }}", f"{lines_per_day:,}", output)

        generate_output_folder()
        with open("generated/overview.svg", "w") as f:
            f.write(output)
        
        print(f"✅ Overview badge generated! Days coding: {days_since_first_commit}, Lines/day: {lines_per_day:,}")
        
    except Exception as e:
        print(f"❌ Failed to generate overview badge: {e}")
        raise


async def generate_languages(s: Stats) -> None:
    """
    Generate an SVG badge with summary languages used
    :param s: Represents user's GitHub statistics
    """
    print("🔄 Generating languages badge...")
    
    try:
        with open("templates/languages.svg", "r") as f:
            output = f.read()

        # Get languages data with retry logic
        languages_data = await safe_get_stat(s.languages, "languages data")

        progress = ""
        lang_list = ""
        sorted_languages = sorted(
            languages_data.items(), reverse=True, key=lambda t: t[1].get("size")
        )
        delay_between = 150
        
        for i, (lang, data) in enumerate(sorted_languages):
            color = data.get("color")
            color = color if color is not None else "#000000"
            progress += (
                f'<span style="background-color: {color};'
                f'width: {data.get("prop", 0):0.3f}%;" '
                f'class="progress-item"></span>'
            )
            lang_list += f"""
<li style="animation-delay: {i * delay_between}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};"
viewBox="0 0 16 16" version="1.1" width="16" height="16"><path
fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
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


################################################################################
# Main Function
################################################################################


async def main() -> None:
    """
    Generate all badges with enhanced statistics
    """
    print("🚀 Starting enhanced GitHub stats generation...")
    
    # Calculate and display the epic stats
    days_since_start = calculate_days_since_first_commit()
    print(f"📅 Days since first commit (Aug 4th): {days_since_start}")
    print(f"🔥 That's some INSANE productivity!")
    
    access_token = os.getenv("ACCESS_TOKEN")
    if not access_token:
        access_token = os.getenv("GITHUB_TOKEN")
        if not access_token:
            raise Exception("A personal access token is required to proceed!")
    
    user = os.getenv("GITHUB_ACTOR")
    if user is None:
        raise RuntimeError("Environment variable GITHUB_ACTOR must be set.")
    
    print(f"📊 Generating enhanced stats for user: {user}")
    
    exclude_repos = os.getenv("EXCLUDED")
    excluded_repos = (
        {x.strip() for x in exclude_repos.split(",")} if exclude_repos else None
    )
    
    exclude_langs = os.getenv("EXCLUDED_LANGS")
    excluded_langs = (
        {x.strip() for x in exclude_langs.split(",")} if exclude_langs else None
    )
    
    raw_ignore_forked_repos = os.getenv("EXCLUDE_FORKED_REPOS")
    ignore_forked_repos = (
        not not raw_ignore_forked_repos
        and raw_ignore_forked_repos.strip().lower() != "false"
    )
    
    if excluded_repos:
        print(f"📝 Excluding repositories: {excluded_repos}")
    if excluded_langs:
        print(f"🚫 Excluding languages: {excluded_langs}")
    if ignore_forked_repos:
        print("🍴 Ignoring forked repositories")
    
    timeout = aiohttp.ClientTimeout(total=300)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            s = Stats(
                user,
                access_token,
                session,
                exclude_repos=excluded_repos,
                exclude_langs=excluded_langs,
                ignore_forked_repos=ignore_forked_repos,
            )
            
            print("\n📈 Starting enhanced badge generation...")
            await generate_overview(s)
            await asyncio.sleep(2)
            await generate_languages(s)
            
            print("\n🎉 All enhanced badges generated successfully!")
            print(f"💪 Showing {days_since_start} days of epic coding journey!")
            
    except Exception as e:
        print(f"\n💥 Fatal error during generation: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

import argparse
import csv
import json
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://backloggd.com"
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2  # Seconds, multiplied by the attempt number
PAGE_DELAY = 0.1  # Polite delay between page requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://backloggd.com/",
}
try:
    import lxml
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"
    


# Build the canonical library URL from a username or a full profile URL.
# Note: Backloggd's CDN returns 403 for any path under /u/<user>/games/<...>,
# including a bare trailing slash, so the URL must end in "games" exactly.
def normalize_profile_input(profile_url_or_username):
    if profile_url_or_username.startswith("http"):
        if "/u/" not in profile_url_or_username:
            sys.exit(f"Could not find a username in the URL: {profile_url_or_username}")
        username = profile_url_or_username.split("/u/")[1].split("/")[0]
    else:
        username = profile_url_or_username.strip("/")

    if not username:
        sys.exit("No username provided.")

    return username, f"{BASE_URL}/u/{username}/games"


# Fetch one library page and return its game entries
def fetch_profile_page(profile_url, page):
    paginated_url = f"{profile_url}?page={page}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                    paginated_url,
                    headers=HEADERS,
                    timeout=TIMEOUT
                )
            if response.status_code == 404:
                sys.exit(f"Profile not found: {profile_url} (check the username)")
            if response.status_code == 403:
                sys.exit(
                    "Got 403 Forbidden. Backloggd may be blocking automated "
                    "requests, or the URL format has changed."
                )
            response.raise_for_status()

            soup = BeautifulSoup(response.content, "PARSER")
            return soup.select(".rating-hover")
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                sys.exit(f"Error fetching page {page} after {MAX_RETRIES} attempts: {e}")
            print(f"Error fetching page {page} (attempt {attempt}/{MAX_RETRIES}): {e}")
            time.sleep(RETRY_DELAY * attempt)


# Extract title, rating, and metadata from the game entries on one page
def extract_game_data(game_entries):
    game_data = []
    for entry in game_entries:
        title_element = entry.select_one(".game-text-centered")
        title = title_element.get_text(strip=True) if title_element else "Unknown Title"

        # game_id is not written to the CSV; it is only used to detect
        # when Backloggd starts repeating the last page
        cover_element = entry.select_one(".game-cover")
        game_id = cover_element.get("game_id", "") if cover_element else ""

        # Unrated games have no star element at all; leave the rating empty
        rating = ""
        stars_top_element = entry.select_one(".stars-top")
        if stars_top_element:
            style = stars_top_element.get("style", "")
            if "width:" in style:
                width = style.split("width:")[1].split("%")[0].strip()
                try:
                    rating = float(width) / 20  # Star width percentage -> 5-star scale
                except ValueError:
                    rating = ""

        # Fallback for older page variants that expose a data-rating attribute
        if rating == "":
            fallback_element = entry.find(attrs={"data-rating": True})
            if fallback_element:
                try:
                    rating = float(fallback_element.get("data-rating")) / 2
                except (ValueError, TypeError):
                    rating = ""

        game_data.append({"title": title, "rating": rating, "game_id": game_id})
    return game_data


# Fetch all game data by iterating through pages until no new data is found
def fetch_all_game_data(profile_url):
    all_game_data = []
    page = 1
    previous_page_ids = None

    while True:
        print(f"Fetching page {page}...")
        game_entries = fetch_profile_page(profile_url, page)

        if not game_entries:
            print("No more game entries found. Stopping.")
            break

        game_data = extract_game_data(game_entries)

        # Past the last page Backloggd keeps serving the final page,
        # so a repeated page means we are done
        current_page_ids = [game["game_id"] for game in game_data]
        if current_page_ids == previous_page_ids:
            break

        all_game_data.extend(game_data)
        previous_page_ids = current_page_ids
        page += 1
        time.sleep(PAGE_DELAY)

    return all_game_data


# Save the fetched game data to a CSV file
def save_to_csv(username, game_data,  ensure_ascii=False):
    filename = f"{username}_games.csv"
    print(f"Saving data to {filename}...")
    try:
        with open(filename, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["Title", "Rating"])
            for game in game_data:
                writer.writerow([game["title"], game["rating"]])
        print(f"Saved {len(game_data)} games to {filename}")
    except OSError as e:
        sys.exit(f"Error saving data to {filename}: {e}")

def save_to_json(username, game_data,  ensure_ascii=False):
    filename = f"{username}_games.json"
    print(f"Saving data to {filename}...")
    try:
        with open(filename, mode="w", encoding="utf-8") as file:
            json.dump(game_data, file, indent=4)
        print(f"Saved {len(game_data)} games to {filename}")
    except OSError as e:
        sys.exit(f"Error saving data to {filename}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape game data from a Backloggd profile and save to a CSV file."
    )
    parser.add_argument(
        "profile_url_or_username",
        type=str,
        help="Backloggd profile URL or username (e.g., https://backloggd.com/u/username/games/ or simply 'username')",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)"
    )
    args = parser.parse_args()

    username, profile_url = normalize_profile_input(args.profile_url_or_username)

    print(f"Scraping data for username: {username}")
    all_game_data = fetch_all_game_data(profile_url)

    if not all_game_data:
        sys.exit(f"No games found for {username}.")

    if args.format == "csv":
        save_to_csv(username, all_game_data)
    elif args.format == "json":
        save_to_json(username, all_game_data)

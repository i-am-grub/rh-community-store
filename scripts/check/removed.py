"""Check if a plugin has been listed as removed."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Loggin setup
logging.addLevelName(logging.INFO, "")
logging.addLevelName(logging.ERROR, "::error::")
logging.addLevelName(logging.WARNING, "::warning::")
logging.basicConfig(
    level=logging.INFO,
    format=" %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def check_removed_repository(repo: str, data_file: str) -> None:
    """Check if a plugin has been listed as removed.

    Args:
    ----
        repo (str): Repository name.
        data_file (str): Path to the removed.json file.

    """
    try:
        with Path.open(data_file) as file:
            removed_plugins = json.load(file)

        if repo in removed_plugins:
            logging.warning(f"⚠️ '{repo}' is removed from the RH Community Store.")
            sys.exit(1)
    except FileNotFoundError:
        logging.exception(f"::error::Could not find {data_file}. Ensure it exists.")
    except json.JSONDecodeError:
        logging.exception(f"::error::Invalid JSON format in {data_file}")
    except Exception:
        logging.exception("Unexpected error occurred")
    else:
        logging.info(f"✅ '{repo}' is not removed from the RH Community Store.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate repository against removed list."
    )
    parser.add_argument(
        "--data-file",
        required=True,
        help="Path to the short list of removed plugins.",
    )
    args = parser.parse_args()

    repo = os.environ.get("REPOSITORY").lower()
    if not repo:
        logging.error("'REPOSITORY' environment variable is not set or empty.")

    check_removed_repository(repo, args.data_file)

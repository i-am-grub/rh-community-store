"""Check if a plugin has been listed as removed."""

import asyncio
import logging
import os
import sys

from aiohttp import ClientError, ClientSession

# Loggin setup
logging.addLevelName(logging.INFO, "")
logging.addLevelName(logging.ERROR, "::error::")
logging.addLevelName(logging.WARNING, "::warning::")
logging.basicConfig(
    level=logging.INFO,
    format=" %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

CHECK_URL = "https://rh-data.dutchdronesquad.nl/v1/removed/repositories.json"


async def check_removed_repository() -> None:
    """Check if a plugin has been listed as removed."""
    repo = os.environ.get("REPOSITORY").lower()
    if not repo:
        logging.error("'REPOSITORY' environment variable is not set or empty.")

    try:
        async with ClientSession() as session, session.get(CHECK_URL) as response:
            if response.status != 200:
                logging.error(
                    "Failed to fetch removed repositories. "
                    f"HTTP Status: {response.status}"
                )
                sys.exit(1)

            removed_repositories = {r.lower() for r in await response.json()}
            if repo in removed_repositories:
                logging.error(f"'{repo}' has been removed from the RH Community Store.")
                sys.exit(1)

    except ClientError:
        logging.exception("Client error occurred")
    except Exception:
        logging.exception("Unexpected error occurred")

    logging.info(f"✅ '{repo}' is not removed from the RH Community Store.")


if __name__ == "__main__":
    asyncio.run(check_removed_repository())

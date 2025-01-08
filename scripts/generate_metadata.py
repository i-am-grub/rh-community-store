"""Generate metadata for RH Community plugins."""

import asyncio
import base64
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from aiogithubapi import GitHubAPI, GitHubException, GitHubNotFoundException

# Loggin setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s: %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# GitHub API configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PLUGIN_LIST_FILE = "plugins.json"
OUTPUT_DIR = "output/plugins"
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


class RotorHazardPlugin:
    """Handles fetching metdata for a RotorHazard plugin."""

    def __init__(self, repo: str) -> None:
        """Initialize the plugin."""
        self.repo = repo  # Full repository name (e.g., "owner/repo_name")
        self.domain = None
        self.metadata = {}
        self.manifest_data = {}
        self.etag_repository = None
        self.etag_release = None

    async def get_plugin_domain(self, github: GitHubAPI) -> str | None:
        """Fetch the folder name (domain) from the `custom_plugins/` folder.

        Args:
        ----
            github: GitHubAPI instance.

        Returns:
        -------
            str | None: Plugin domain name.

        """
        logging.info(f"Fetching plugin domain for {self.repo}")
        try:
            response = await github.repos.contents.get(
                self.repo, etag=self.etag_repository
            )

            # Check for `custom_plugins/` folder
            custom_plugins_folder = next(
                (
                    item
                    for item in response.data
                    if item.name == "custom_plugins" and item.type == "dir"
                ),
                None,
            )
            if not custom_plugins_folder:
                logging.error(
                    f"::error::The `custom_plugins/` folder is missing in {self.repo}."
                )
                return None

            # Fetch the contens of the `custom_plugins/` folder
            folder_response = await github.repos.contents.get(
                self.repo, "custom_plugins"
            )
            subfolders = [item for item in folder_response.data if item.type == "dir"]

            # Ensure there is exactly one domain folder
            if len(subfolders) != 1:
                logging.error(
                    "::error::Expected exactly one domain folder inside "
                    f"`custom_plugins/` for '{self.repo}', but found {len(subfolders)}."
                )
                return None

            # Get the domain folder name
            self.domain = subfolders[0].name
            logging.info(f"Found domain {self.domain} for {self.repo}")
        except GitHubNotFoundException:
            logging.warning(f"::error::Repository '{self.repo}' not found.")
        except GitHubException:
            logging.exception(f"Error fetching plugin domain for {self.repo}")
        else:
            return self.domain

    async def validate_domain_manifest(self, github: GitHubAPI) -> bool:
        """Validate that the domain in `manifest.json` matches the folder name.

        Args:
        ----
            github: GitHubAPI instance.

        Returns:
        -------
            bool: True if the domain matches the folder name, False otherwise.

        """
        if not self.domain:
            return False

        manifest_path = f"custom_plugins/{self.domain}/manifest.json"
        try:
            response = await github.repos.contents.get(self.repo, manifest_path)

            # Decode Base64 content manually
            manifest = json.loads(
                base64.b64decode(response.data.content).decode("utf-8")
            )
            self.manifest_data = manifest
            manifest_domain = manifest.get("domain")

            # Compare the domain in the manifest with the folder name
            if manifest_domain != self.domain:
                logging.error(
                    f"::error::Domain mismatch for {self.repo}: Folder "
                    f"'{self.domain}' vs Manifest '{manifest_domain}'."
                )
                return False
        except GitHubNotFoundException:
            logging.exception(
                "::error::Manifest file not found for "
                f"'{self.repo}' at '{manifest_path}'."
            )
        except json.JSONDecodeError:
            logging.exception(
                f"::error::Manifest file for '{self.repo}' "
                f"at '{manifest_path}' contains invalid JSON."
            )
        except GitHubException:
            logging.exception(f"Error fetching manifest for '{self.repo}'")
        else:
            logging.info(
                f"Domain validated for {self.repo}: '{self.domain}' "
                "matches manifest domain."
            )
            return True

    async def fetch_releases(self, github: GitHubAPI) -> str | None:
        """Fetch the latest release tag from GitHub.

        Args:
        ----
            github: GitHubAPI instance.

        Returns:
        -------
            str | None: Latest release tag.

        """
        try:
            releases = await github.repos.releases.list(self.repo)
            if releases.etag:
                self.etag_release = releases.etag
            return releases.data[0].tag_name if releases.data else None
        except GitHubNotFoundException:
            logging.warning(f"Releases not found for '{self.repo}'.")
        except GitHubException:
            logging.exception(f"Error fetching releases for '{self.repo}'.")
        return None

    async def update_metadata(self, github: GitHubAPI) -> dict | None:
        """Fetch and update the plugin's metadata.

        Args:
        ----
            github: GitHubAPI instance.

        Returns:
        -------
            dict | None: Metadata for the plugin.

        """
        if not await self.get_plugin_domain(github):
            return None
        if not await self.validate_domain_manifest(github):
            return None

        try:
            repo_data = await github.repos.get(self.repo)
            if repo_data.etag:
                self.etag_repository = repo_data.etag
            last_version = await self.fetch_releases(github)

            self.metadata = {
                "manifest": {
                    "name": self.manifest_data.get("name"),
                    "description": self.manifest_data.get("description"),
                },
                "domain": self.domain,
                "etag_release": self.etag_release,
                "etag_repository": self.etag_repository,
                "repository": self.repo,
                "last_updated": repo_data.data.updated_at,
                "last_version": last_version,
                "open_issues": repo_data.data.open_issues_count,
                "stargazers_count": repo_data.data.stargazers_count,
                "topics": repo_data.data.topics,
                "last_fetched": datetime.now(UTC).isoformat(),
            }
        except GitHubNotFoundException:
            logging.warning(f"::warning::Repository '{self.repo}' not found.")
        except GitHubException:
            logging.exception(f"Error fetching repository metadata for '{self.repo}'")
        else:
            logging.info(f"Metadata successfully generated for '{self.repo}'.")
            return {repo_data.data.id: self.metadata}


class MetadataGenerator:
    """Handles generating and saving metadata for all repositories."""

    def __init__(self, plugin_file: str, output_dir: str) -> None:
        """Initialize the metadata generator."""
        self.plugin_file = Path(plugin_file)
        self.output_dir = output_dir
        self.repos_list = self.load_repos()

    def load_repos(self) -> list[str]:
        """Load repository list from the plugin file.

        Returns
        -------
            list[str]: List of repositories.

        """
        if self.plugin_file.exists():
            with Path.open(self.plugin_file, encoding="utf-8") as f:
                return json.load(f)
        else:
            logging.warning("Plugin list file not found. Using an empty list.")
            return []

    def save_json(self, filepath: str, data: dict) -> None:
        """Save data to a JSON file.

        Args:
        ----
            filepath: Path to the output JSON file.
            data: Data to be saved.

        """
        with Path.open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    async def summarize_results(
        self,
        total: int,
        valid: int,
        skipped: int,
        start_time: float,
    ) -> None:
        """Summarize the generation results.

        Args:
        ----
            total: Total number of repositories.
            valid: Number of repositories with valid metadata.
            skipped: Number of repositories skipped during generation.
            start_time: Time when the generation started.

        """
        end_time = perf_counter()
        elapsed_time = end_time - start_time

        summary = {
            "total_plugins": total,
            "valid_plugins": valid,
            "skipped_plugins": skipped,
            "execution_time_seconds": round(elapsed_time, 2),
        }
        logging.info("Metadata generation summary:")
        logging.info(json.dumps(summary, indent=4))

    async def generate_metadata(self) -> None:
        """Generate metadata for all repositories."""
        plugin_data: dict[str, dict] = {}
        valid_repositories: list[str] = []
        skipped_plugins = 0

        start_time = perf_counter()

        async with GitHubAPI(token=GITHUB_TOKEN) as github:
            tasks = [
                RotorHazardPlugin(repo).update_metadata(github)
                for repo in self.repos_list
            ]
            results = await asyncio.gather(*tasks)

            for result in results:
                if result:
                    repo_id, metadata = next(iter(result.items()))
                    plugin_data[repo_id] = metadata
                    valid_repositories.append(metadata["repository"])
                else:
                    skipped_plugins += 1

        # Save generated metadata to local JSON file
        self.save_json(f"{self.output_dir}/data.json", plugin_data)
        self.save_json(f"{self.output_dir}/repositories.json", valid_repositories)

        # Summarize the results
        await self.summarize_results(
            total=len(self.repos_list),
            valid=len(valid_repositories),
            skipped=skipped_plugins,
            start_time=start_time,
        )


if __name__ == "__main__":
    asyncio.run(MetadataGenerator(PLUGIN_LIST_FILE, OUTPUT_DIR).generate_metadata())

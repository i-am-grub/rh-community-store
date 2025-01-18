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
logging.addLevelName(logging.INFO, "")
logging.addLevelName(logging.ERROR, "::error::")
logging.addLevelName(logging.WARNING, "::warning::")
logging.basicConfig(
    level=logging.INFO,
    format=" %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# GitHub API configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PLUGIN_LIST_FILE = "plugins.json"
OUTPUT_DIR = "output/plugin"
COMPARE_IGNORE = ["last_fetched", "etag_release", "etag_repository"]

# Create output directories
Path(f"{OUTPUT_DIR}/diff").mkdir(parents=True, exist_ok=True)


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
        try:
            logging.info(f"<{self.repo}> Fetching plugin domain")
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
                logging.error(f"<{self.repo}> The `custom_plugins/` folder is missing")
                return None

            # Fetch the contens of the `custom_plugins/` folder
            folder_response = await github.repos.contents.get(
                self.repo, "custom_plugins"
            )
            subfolders = [item for item in folder_response.data if item.type == "dir"]

            # Ensure there is exactly one domain folder
            if len(subfolders) != 1:
                logging.error(
                    f"<{self.repo}> Expected exactly one domain folder inside "
                    f"`custom_plugins/` but found: {len(subfolders)}."
                )
                return None

            # Get the domain folder name
            self.domain = subfolders[0].name
            logging.info(f"<{self.repo}> Found domain '{self.domain}'")
        except GitHubNotFoundException:
            logging.warning(f"<{self.repo}> Repository not found")
        except GitHubException:
            logging.exception(f"<{self.repo}> Error fetching plugin domain")
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
                    f"<{self.repo}> Domain mismatch: Folder "
                    f"'{self.domain}' vs Manifest '{manifest_domain}'"
                )
                return False
        except GitHubNotFoundException:
            logging.exception(
                f"<{self.repo}> Manifest file not found at '{manifest_path}'"
            )
        except json.JSONDecodeError:
            logging.exception(
                f"<{self.repo}> Manifest file at '{manifest_path}' "
                "contains invalid JSON"
            )
        except GitHubException:
            logging.exception(f"Error fetching manifest for '{self.repo}'")
        else:
            logging.info(
                f"<{self.repo}> Domain validated: '{self.domain}' "
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
        logging.info(f"<{self.repo}> Fetching releases")
        try:
            releases = await github.repos.releases.list(self.repo)
            if releases.etag:
                self.etag_release = releases.etag
            return releases.data[0].tag_name if releases.data else None
        except GitHubNotFoundException:
            logging.warning(f"<{self.repo}> Zero github releases found")
        except GitHubException:
            logging.exception(f"<{self.repo}> Error fetching releases")
        return None

    async def fetch_metadata(self, github: GitHubAPI) -> dict | str | None:
        """Fetch and update the plugin's metadata.

        Args:
        ----
            github: GitHubAPI instance.

        Returns:
        -------
            dict | None: Metadata for the plugin.

        """
        try:
            logging.info(f"<{self.repo}> Fetching repository metadata")
            repo_data = await github.repos.get(self.repo)

            # Check if the repository is archived
            if repo_data.data.archived:
                logging.error(f"<{self.repo}> Repository is archived")
                return "archived"

            # Fetch rest of the metadata
            if repo_data.etag:
                self.etag_repository = repo_data.etag
            last_version = await self.fetch_releases(github)

            self.metadata = {
                "etag_release": self.etag_release,
                "etag_repository": self.etag_repository,
                "last_fetched": datetime.now(UTC).isoformat(),
                "last_updated": repo_data.data.updated_at,
                "last_version": last_version,
                "open_issues": repo_data.data.open_issues_count,
                "repository": self.repo,
                "stargazers_count": repo_data.data.stargazers_count,
                "topics": repo_data.data.topics,
            }

            # Fetch plugin domain
            if not await self.get_plugin_domain(github):
                return None

            # Validate domain and manifest
            if not await self.validate_domain_manifest(github):
                return None

            # Add manifest-specific metadata
            self.metadata = {
                "manifest": {
                    "name": self.manifest_data.get("name"),
                    "description": self.manifest_data.get("description"),
                },
                "domain": self.domain,
                **self.metadata,
            }
        except GitHubNotFoundException:
            logging.warning(f"<{self.repo}> Repository not found")
        except GitHubException:
            logging.exception(f"<{self.repo}> Error fetching repository metadata")
        else:
            logging.info(f"<{self.repo}> Metadata successfully generated")
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

    def save_filtered_json(self, filepath: str, data: dict) -> None:
        """Save data to a JSON file with filtered keys.

        Args:
        ----
            filepath: Path to the output JSON file.
            data: Data to be saved.

        """
        filtered_data = {
            key: {k: v for k, v in value.items() if k not in COMPARE_IGNORE}
            for key, value in data.items()
        }
        with Path.open(filepath, "w", encoding="utf-8") as f:
            json.dump(filtered_data, f, indent=2)

    def save_json(self, filepath: str, data: dict) -> None:
        """Save data to a JSON file.

        Args:
        ----
            filepath: Path to the output JSON file.
            data: Data to be saved.

        """
        with Path.open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    async def summarize_results(
        self,
        total: int,
        valid: int,
        skipped: int,
        archived: int,
        start_time: float,
    ) -> None:
        """Summarize the generation results.

        Args:
        ----
            total: Total number of repositories.
            valid: Number of repositories with valid metadata.
            archived: Number of archived repositories.
            skipped: Number of repositories skipped during generation.
            start_time: Time when the generation started.

        """
        end_time = perf_counter()
        elapsed_time = end_time - start_time

        summary = {
            "total_plugins": total,
            "valid_plugins": valid,
            "archived_plugins": archived,
            "skipped_plugins": skipped,
            "execution_time_seconds": round(elapsed_time, 2),
        }
        summary_path = f"{self.output_dir}/summary.json"
        self.save_json(summary_path, summary)

    async def generate_metadata(self) -> None:
        """Generate metadata for all repositories."""
        plugin_data: dict[str, dict] = {}
        valid_repositories: list[str] = []
        skipped_plugins = 0
        archived_plugins = 0

        start_time = perf_counter()

        async with GitHubAPI(token=GITHUB_TOKEN) as github:
            tasks = [
                RotorHazardPlugin(repo).fetch_metadata(github)
                for repo in self.repos_list
            ]
            results = await asyncio.gather(*tasks)

            for result in results:
                if result == "archived":
                    archived_plugins += 1
                elif result:
                    repo_id, metadata = next(iter(result.items()))
                    plugin_data[repo_id] = metadata
                    valid_repositories.append(metadata["repository"])
                else:
                    skipped_plugins += 1

        # Save generated metadata to local JSON file
        self.save_filtered_json(f"{self.output_dir}/diff/after.json", plugin_data)
        self.save_json(f"{self.output_dir}/data.json", plugin_data)
        self.save_json(f"{self.output_dir}/repositories.json", valid_repositories)

        # Summarize the results
        await self.summarize_results(
            total=len(self.repos_list),
            valid=len(valid_repositories),
            skipped=skipped_plugins,
            archived=archived_plugins,
            start_time=start_time,
        )


if __name__ == "__main__":
    asyncio.run(MetadataGenerator(PLUGIN_LIST_FILE, OUTPUT_DIR).generate_metadata())

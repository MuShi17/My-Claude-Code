"""Optional Harbor environment for task-specific prebuilt Docker images.

Harbor passes the task short name to the environment constructor.  This class
uses that name to replace the task's Docker build with an image selected in a
local JSON file.  The image must already contain both the task environment and
the Mini Claude runtime; the Adapter still uploads the current ``src`` tree
before checking the prebuilt virtual environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE_MAP = PROJECT_ROOT / "benchmark" / "prebuilt-images.json"


def _image_map_path() -> Path:
    configured_path = os.environ.get("MINI_CLAUDE_IMAGE_MAP", "").strip()
    return Path(configured_path) if configured_path else DEFAULT_IMAGE_MAP


def load_prebuilt_image_map() -> dict[str, str]:
    """Load the opt-in task-to-image mapping from a local JSON file."""

    path = _image_map_path()
    if not path.is_file():
        return {}

    try:
        raw_mapping = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read prebuilt image map {path}: {exc}") from exc

    if not isinstance(raw_mapping, dict):
        raise ValueError(f"Prebuilt image map {path} must contain a JSON object")

    mapping: dict[str, str] = {}
    for task_name, image in raw_mapping.items():
        if not isinstance(task_name, str) or not task_name.strip():
            raise ValueError(f"Invalid task name in prebuilt image map {path}")
        if not isinstance(image, str) or not image.strip():
            raise ValueError(
                f"Image for task {task_name!r} in {path} must be a non-empty string"
            )
        mapping[task_name.strip()] = image.strip()
    return mapping


class MiniClaudePrebuiltDockerEnvironment(DockerEnvironment):
    """Use an explicitly mapped prebuilt image for selected Harbor tasks.

    Without a matching mapping entry this behaves exactly like Harbor's normal
    ``DockerEnvironment``.  This makes the optimization opt-in per task and
    avoids replacing task-specific images with the generic runtime image.
    """

    def __init__(
        self,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: Any,
        task_env_config: EnvironmentConfig,
        **kwargs: Any,
    ) -> None:
        image_map = load_prebuilt_image_map()
        image = image_map.get(environment_name)
        if image is None:
            image = image_map.get(f"terminal-bench/{environment_name}")

        if image is not None:
            task_env_config = task_env_config.model_copy(deep=True)
            task_env_config.docker_image = image

        super().__init__(
            environment_dir=environment_dir,
            environment_name=environment_name,
            session_id=session_id,
            trial_paths=trial_paths,
            task_env_config=task_env_config,
            **kwargs,
        )

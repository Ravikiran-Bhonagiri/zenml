#  Copyright (c) ZenML GmbH 2022. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.


import shutil
import tempfile
from typing import TYPE_CHECKING, Any, Dict, Optional, Type, cast

from zenml.image_builders import (
    BaseImageBuilder,
    BaseImageBuilderConfig,
    BaseImageBuilderFlavor,
)
from zenml.utils import docker_utils
from zenml.logger import get_logger

if TYPE_CHECKING:
    from zenml.container_registries import BaseContainerRegistry
    from zenml.image_builders import BuildContext


logger = get_logger(__name__)
logger.propagate = True  # Ensure logs propagate to the root logger

class LocalImageBuilderConfig(BaseImageBuilderConfig):
    """Local image builder configuration."""


class LocalImageBuilder(BaseImageBuilder):
    """Local image builder implementation."""

    @property
    def config(self) -> LocalImageBuilderConfig:
        """The stack component configuration.

        Returns:
            The configuration.
        """
        return cast(LocalImageBuilderConfig, self._config)

    @property
    def is_building_locally(self) -> bool:
        """Whether the image builder builds the images on the client machine.

        Returns:
            True if the image builder builds locally, False otherwise.
        """
        return True

    @staticmethod
    def _check_prerequisites() -> None:
        """Checks that all prerequisites are installed.

        Raises:
            RuntimeError: If any of the prerequisites are not installed or
                running.
        """
        try:
            if not shutil.which("docker"):
                raise RuntimeError("'docker' is required to run the local image builder.")
            
            logger.info("'docker' executable found.")

            if not docker_utils.check_docker():
                error_message = (
                    "Unable to connect to the Docker daemon. There are three "
                    "common causes for this:\n"
                    "1) The Docker daemon isn't running.\n"
                    "2) The Docker client isn't configured correctly. The client "
                    "loads its configuration from the following file: "
                    "$HOME/.docker/config.json. If your configuration file is in a "
                    "different location, set the `DOCKER_CONFIG` environment "
                    "variable to the directory that contains your `config.json` "
                    "file.\n"
                    "3) If your Docker CLI is working fine but you ran into this "
                    "issue, you might be using a non-default Docker context which "
                    "is not supported by the Docker python library. To verify "
                    "this, run `docker context ls` and check which context has a "
                    "`*` next to it. If this is not the `default` context, copy "
                    "the `DOCKER ENDPOINT` value of that context and set the "
                    "`DOCKER_HOST` environment variable to that value."
                )
                logger.error(error_message)  # Log the error message
                raise RuntimeError("Failed to connect to the Docker daemon.")
            logger.info("Successfully connected to the Docker daemon.")

        except RuntimeError as e:
            logger.error(f"Prerequisite check failed: {e}")
            raise  # Re-raise after logging
        except Exception as e:  # Catch any other exception during prerequisite check
            logger.exception(f"An unexpected error occurred during prerequisite check: {e}")
            raise
        logger.info("All prerequisites check passed.")

        

    def build(
        self,
        image_name: str,
        build_context: "BuildContext",
        docker_build_options: Optional[Dict[str, Any]] = None,
        container_registry: Optional["BaseContainerRegistry"] = None,
    ) -> str:
        """Builds and optionally pushes an image using the local Docker client.

        Args:
            image_name: Name of the image.
            build_context: The build context.
            docker_build_options: Docker build options.
            container_registry: Optional container registry.

        Returns:
            The Docker image repo digest.

        Raises:
            RuntimeError: If an error occurs during the build or push process.
        """
        self._check_prerequisites()

        if container_registry:
            # Use the container registry's docker client, which may be
            # authenticated to access additional registries
            docker_client = container_registry.docker_client
        else:
            try:
                docker_client = docker_utils._try_get_docker_client_from_env()
            except Exception as e:
                logger.exception("Failed to initialize Docker client: %s", e)
                raise RuntimeError("Failed to initialize Docker client.") from e

        logger.info(f"Starting build of image '{image_name}'...")
        logger.debug(f"Docker build options: {docker_build_options}")

        try:
            with tempfile.NamedTemporaryFile(mode="w+b") as f:
                logger.debug(f"Creating temporary file for build context: {f.name}")
                try:
                    build_context.write_archive(f)
                    f.seek(0)  # Rewind is crucial!
                    logger.debug("Build context archive written to temporary file.")
                except Exception as e:
                    logger.exception("Error writing build context to temporary file: %s", e)
                    raise RuntimeError(f"Error writing build context archive for image '{image_name}'.") from e

                try:
                    logger.info("Building image...")
                    output_stream = docker_client.images.client.api.build(
                        fileobj=f,
                        custom_context=True,
                        tag=image_name,
                        **(docker_build_options or {}),
                    )
                    image_digest = docker_utils._process_stream(output_stream)  # Capture the digest

                    logger.info(f"Image '{image_name}' built successfully. Digest: {image_digest}")

                except Exception as e:
                    logger.exception("Error during Docker image build:")
                    raise RuntimeError(f"Error during Docker image build for image '{image_name}'.") from e

            if container_registry:
                try:
                    logger.info(f"Pushing image '{image_name}' to {container_registry.name}...")
                    repo_digest = container_registry.push_image(image_name)  # Capture repo digest
                    logger.info(f"Image '{image_name}' pushed successfully. Repo digest: {repo_digest}")
                    return repo_digest
                except Exception as e:
                    logger.exception(f"Error pushing image '{image_name}' to {container_registry.name}: {e}")
                    raise RuntimeError(f"Error pushing image '{image_name}' to {container_registry.name}.") from e
            else:
                return image_digest  # Return the digest if no registry

        except Exception as e:  # Catch any other top-level exceptions
            logger.exception(f"An unexpected error occurred during the build/push process: {e}")
            raise  # Re-raise the exception

class LocalImageBuilderFlavor(BaseImageBuilderFlavor):
    """Local image builder flavor."""

    @property
    def name(self) -> str:
        """The flavor name.

        Returns:
            The flavor name.
        """
        return "local"

    @property
    def docs_url(self) -> Optional[str]:
        """A url to point at docs explaining this flavor.

        Returns:
            A flavor docs url.
        """
        return self.generate_default_docs_url()

    @property
    def sdk_docs_url(self) -> Optional[str]:
        """A url to point at docs explaining this flavor.

        Returns:
            A flavor docs url.
        """
        return self.generate_default_sdk_docs_url()

    @property
    def logo_url(self) -> str:
        """A url to represent the flavor in the dashboard.

        Returns:
            The flavor logo.
        """
        return "https://public-flavor-logos.s3.eu-central-1.amazonaws.com/image_builder/local.svg"

    @property
    def config_class(self) -> Type[LocalImageBuilderConfig]:
        """Config class.

        Returns:
            The config class.
        """
        return LocalImageBuilderConfig

    @property
    def implementation_class(self) -> Type[LocalImageBuilder]:
        """Implementation class.

        Returns:
            The implementation class.
        """
        return LocalImageBuilder

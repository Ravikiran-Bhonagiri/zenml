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
"""Google Cloud Builder image builder implementation."""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, cast
from urllib.parse import urlparse

from google.cloud.devtools import cloudbuild_v1

from zenml.enums import StackComponentType
from zenml.image_builders import BaseImageBuilder
from zenml.integrations.gcp import GCP_ARTIFACT_STORE_FLAVOR
from zenml.integrations.gcp.flavors import GCPImageBuilderConfig
from zenml.integrations.gcp.google_credentials_mixin import (
    GoogleCredentialsMixin,
)
from zenml.logger import get_logger
from zenml.stack import StackValidator

if TYPE_CHECKING:
    from zenml.container_registries import BaseContainerRegistry
    from zenml.image_builders import BuildContext
    from zenml.stack import Stack

logger = get_logger(__name__)


class GCPImageBuilder(BaseImageBuilder, GoogleCredentialsMixin):
    """Google Cloud Builder image builder implementation."""

    @property
    def config(self) -> GCPImageBuilderConfig:
        """The stack component configuration.

        Returns:
            The configuration.
        """
        return cast(GCPImageBuilderConfig, self._config)

    @property
    def is_building_locally(self) -> bool:
        """Whether the image builder builds the images on the client machine.

        Returns:
            True if the image builder builds locally, False otherwise.
        """
        return False

    @property
    def validator(self) -> Optional["StackValidator"]:
        """Validates the stack for the GCP Image Builder.

        The GCP Image Builder requires a remote container registry to push the
        image to, and a GCP Artifact Store to upload the build context, so
        Cloud Build can access it.

        Returns:
            Stack validator.
        """

        def _validate_remote_components(stack: "Stack") -> Tuple[bool, str]:
            assert stack.container_registry

            if stack.container_registry.config.is_local:
                return False, (
                    "The GCP Image Builder requires a remote container "
                    "registry to push the image to. Please update your stack "
                    "to include a remote container registry and try again."
                )

            if stack.artifact_store.flavor != GCP_ARTIFACT_STORE_FLAVOR:
                return False, (
                    "The GCP Image Builder requires a GCP Artifact Store to "
                    "upload the build context, so Cloud Build can access it."
                    "Please update your stack to include a GCP Artifact Store "
                    "and try again."
                )

            return True, ""

        return StackValidator(
            required_components={StackComponentType.CONTAINER_REGISTRY},
            custom_validation_function=_validate_remote_components,
        )

    
    def build(
        self,
        image_name: str,
        build_context: "BuildContext",
        docker_build_options: Dict[str, Any],
        container_registry: Optional["BaseContainerRegistry"] = None,
    ) -> str:
        """Builds and pushes a Docker image using Google Cloud Build.

        Args:
            image_name: Name of the image to build and push.
            build_context: The build context to use for the image.
            docker_build_options: Docker build options.
            container_registry: Optional container registry to push to.

        Returns:
            The Docker image name with digest.

        Raises:
            ValueError: If no container registry is passed.
            RuntimeError: If the Cloud Build job fails.
        """

        if not container_registry:
            raise ValueError(
                "A container registry is required to push the image. "
                "Please provide one and try again."
            )

        logger.info(f"Starting build for image '{image_name}'...")
        logger.debug(f"Build context: {build_context}")
        logger.debug(f"Docker build options: {docker_build_options}")

        try:
            logger.info("Uploading build context...")
            cloud_build_context_uri = self._upload_build_context(
                build_context=build_context,
                parent_path_directory_name="cloud-build-contexts",
            )
            logger.info("Build context uploaded.")  # Indicate upload success

            logger.info("Configuring Cloud Build job...")
            build = self._configure_cloud_build(
                image_name=image_name,
                cloud_build_context=cloud_build_context_uri,
                build_options=docker_build_options,
            )
            logger.info("Cloud Build job configured.") 
            logger.info("Running Cloud Build job...")  # Indicate job start
            image_digest = self._run_cloud_build(build=build)

            image_name_without_tag, _ = image_name.rsplit(":", 1)
            image_name_with_digest = f"{image_name_without_tag}@{image_digest}"
            logger.debug(f"Successfully built and pushed image '{image_name_with_digest}'.")
            logger.info(f"Successfully built and pushed image")
            return image_name_with_digest

        except ValueError as e:  # Catch ValueErrors from argument validation
            logger.error(f"Invalid arguments: {e}")
            raise  # Re-raise the exception

        except Exception as e:  # Catch other exceptions during the build process
            logger.exception(f"An error occurred during image build:")  # Log with traceback
            raise  # Re-raise the exception

    
    def _configure_cloud_build(
        self,
        image_name: str,
        cloud_build_context: str,
        build_options: Dict[str, Any],
    ) -> cloudbuild_v1.Build:
        
        """Configures the Cloud Build job.

        Args:
            image_name: The name of the image to build.
            cloud_build_context: The GCS URI of the build context.
            build_options: Docker build options.

        Returns:
            The configured cloudbuild_v1.Build object.

        Raises:
            ValueError: If there are issues with build context URI.
            TypeError: if build options are not in the correct format
        """
        
        logger.info("Configuring Cloud Build job...")
        try:
            url_parts = urlparse(cloud_build_context)
            bucket = url_parts.netloc
            object_path = url_parts.path.lstrip("/")

            if not bucket or not object_path:  # Check for valid GCS URI
                raise ValueError(f"Invalid build context URI: {cloud_build_context}")

            logger.debug(
                f"Build context located in GCS bucket '{bucket}' and object path '{object_path}'."
            )
        except ValueError as e:
            logger.error(f"Error parsing build context URI: {e}")
            raise  # Re-raise the ValueError

        cloud_builder_image = self.config.cloud_builder_image
        cloud_builder_network_option = f"--network={self.config.network}" if self.config.network else ""
        logger.debug(
            f"Using Cloud Builder image '{cloud_builder_image}'. Network option: '{cloud_builder_network_option or 'None'}'."
        )

        docker_build_args = []
        try:
            for key, value in build_options.items():
                option = f"--{key}"
                if isinstance(value, list):
                    for val in value:
                        docker_build_args.extend([option, str(val)])
                elif value is not None and not isinstance(value, bool):
                    docker_build_args.extend([option, str(value)])
                elif value is not False:
                    docker_build_args.append(option)
        except TypeError as e:
            logger.error(f"Invalid docker build options format: {e}")
            raise  # Re-raise the TypeError
        except Exception as e: # Catch any other exception during build args creation
            logger.exception("An unexpected error occurred while processing build options:")
            raise


        logger.debug(f"Docker build args: {docker_build_args}")

        build = cloudbuild_v1.Build(
            source=cloudbuild_v1.Source(
                storage_source=cloudbuild_v1.StorageSource(
                    bucket=bucket, object=object_path
                ),
            ),
            steps=[
                {
                    "name": cloud_builder_image,
                    "args": [
                        "build",
                        cloud_builder_network_option,
                        "-t",
                        image_name,
                        ".",
                        *docker_build_args,
                    ],
                },
                {"name": cloud_builder_image, "args": ["push", image_name]},
            ],
            images=[image_name],
            timeout=f"{self.config.build_timeout}s",
        )

        logger.debug(f"Configured Cloud Build: {build}")
        logger.info("Cloud Build job configured.")

        return build
    
    
    def _run_cloud_build(self, build: cloudbuild_v1.Build) -> str:
        """Executes the Cloud Build run to build the Docker image.

        Args:
            build: The build to run.

        Returns:
            The Docker image repo digest.

        Raises:
            RuntimeError: If the Cloud Build run has failed.
        """

        credentials, project_id = self._get_authentication()
        client = cloudbuild_v1.CloudBuildClient(credentials=credentials)

        operation = client.create_build(project_id=project_id, build=build)
        build_id = operation.name.split("/")[-1] # Extract build ID
        log_url = operation.metadata.build.log_url
        logger.info(f"Started Cloud Build job")
        logger.debug(f"Started Cloud Build job (ID: {build_id}). Logs: {log_url}")

        try:
            result = operation.result(timeout=self.config.build_timeout)
        except TimeoutError:
            logger.error(f"Cloud Build job (ID: {build_id}) timed out after {self.config.build_timeout} seconds. Check logs: {log_url}")
            raise  # Re-raise TimeoutError
        except Exception as e:  # Catch other exceptions during the build process
            logger.exception(f"An unexpected error occurred during the Cloud Build operation (ID: {build_id}):") # Log with traceback
            raise

        if result.status != cloudbuild_v1.Build.Status.SUCCESS:
            logger.error(f"Cloud Build job (ID: {build_id}) failed. Status: {result.status}. Check logs: {log_url}")
            raise RuntimeError(
                f"The Cloud Build run (ID: {build_id}) to build the Docker image has failed. Status: {result.status}. Check logs: {log_url}."
            )

        logger.info(
            f"The Docker image has been built successfully (ID: {build_id}) . More information can "
            f"be found in the Cloud Build logs: `{log_url}`."
        )

        image_digest: str = result.results.images[0].digest

        logger.info("Cloud Build job completed successfully")

        return image_digest

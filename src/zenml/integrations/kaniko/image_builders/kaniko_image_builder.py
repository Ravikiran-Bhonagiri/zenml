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
"""Kaniko image builder implementation."""

import json
import random
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, cast

from zenml.enums import StackComponentType
from zenml.image_builders import BaseImageBuilder
from zenml.integrations.kaniko.flavors import KanikoImageBuilderConfig
from zenml.logger import get_logger
from zenml.stack import StackValidator
from zenml.utils.archivable import ArchiveType

if TYPE_CHECKING:
    from zenml.container_registries import BaseContainerRegistry
    from zenml.image_builders import BuildContext
    from zenml.stack import Stack

    BytePopen = subprocess.Popen[bytes]
else:
    BytePopen = subprocess.Popen


logger = get_logger(__name__)


class KanikoImageBuilder(BaseImageBuilder):
    """Kaniko image builder implementation."""

    @property
    def config(self) -> KanikoImageBuilderConfig:
        """The stack component configuration.

        Returns:
            The configuration.
        """
        return cast(KanikoImageBuilderConfig, self._config)

    @property
    def is_building_locally(self) -> bool:
        """Whether the image builder builds the images on the client machine.

        Returns:
            True if the image builder builds locally, False otherwise.
        """
        return False

    @property
    def validator(self) -> Optional[StackValidator]:
        """Validates that the stack contains a container registry.

        Returns:
            Stack validator.
        """

        def _validate_remote_components(
            stack: "Stack",
        ) -> Tuple[bool, str]:
            assert stack.container_registry

            if stack.container_registry.config.is_local:
                logger.error(
                    "The Kaniko image builder builds Docker images in a "
                    "Kubernetes cluster and isn't able to push the resulting "
                    "image to a local container registry running on your "
                    "machine. Please update your stack to include a remote "
                    "container registry and try again."
                )
                return False, (
                    "The Kaniko image builder builds Docker images in a "
                    "Kubernetes cluster and isn't able to push the resulting "
                    "image to a local container registry running on your "
                    "machine. Please update your stack to include a remote "
                    "container registry and try again."
                )

            if (
                self.config.store_context_in_artifact_store
                and stack.artifact_store.config.is_local
            ):
                logger.error(
                    "The Kaniko image builder is configured to upload the "
                    "build context to the artifact store. This only works with "
                    "remote artifact stores so that the Kaniko build pod is "
                    "able to read from it. Please update your stack to include "
                    "a remote artifact store and try again.")
                
                return False, (
                    "The Kaniko image builder is configured to upload the "
                    "build context to the artifact store. This only works with "
                    "remote artifact stores so that the Kaniko build pod is "
                    "able to read from it. Please update your stack to include "
                    "a remote artifact store and try again."
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
        """Builds and pushes a Docker image.

        Args:
            image_name: Name of the image to build and push.
            build_context: The build context to use for the image.
            docker_build_options: Docker build options.
            container_registry: Optional container registry to push to.

        Returns:
            The Docker image repo digest.

        Raises:
            RuntimeError: If no container registry is passed.
            RuntimeError: If the upload to the artifact store has failed.
        """
        logger.debug("Building image `%s` with Kaniko.", image_name)
        logger.debug("Docker build options: %s", docker_build_options)
        logger.debug("Build context: %s", build_context)
        logger.debug("Executing function check_prerequisites")

        self._check_prerequisites()
        if not container_registry:
            logger.error(
                "Unable to use the Kaniko image builder without a container "
                "registry.")
            raise RuntimeError(
                "Unable to use the Kaniko image builder without a container "
                "registry."
            )

        logger.debug("Generating pod name")
        pod_name = self._generate_pod_name()
        logger.info(
            "Using Kaniko to build image `%s` in pod `%s`.",
            image_name,
            pod_name,
        )
        if self.config.store_context_in_artifact_store:
            try:
                kaniko_context = self._upload_build_context(
                    build_context=build_context,
                    parent_path_directory_name="kaniko-build-contexts",
                )
            except Exception:
                logger.error("Uploading the Kaniko build context to the artifact store failed.")
                raise RuntimeError(
                    "Uploading the Kaniko build context to the artifact store "
                    "failed. Please make sure you have permissions to write "
                    "to the artifact store or update the Kaniko image builder "
                    "to stream the build context using stdin by running:\n"
                    f"  `zenml image-builder update {self.name}` "
                    "--store_context_in_artifact_store=False`"
                )
        else:
            kaniko_context = "tar://stdin"

        logger.debug("Generating spec overrides")
        spec_overrides = self._generate_spec_overrides(
            pod_name=pod_name, image_name=image_name, context=kaniko_context
        )
        
        logger.debug("Running Kaniko build")
        self._run_kaniko_build(
            pod_name=pod_name,
            spec_overrides=spec_overrides,
            build_context=build_context,
        )

        logger.debug("Reading pod output")
        image_name_with_sha = self._read_pod_output(pod_name=pod_name)
        
        logger.debug("Verifying image name")
        self._verify_image_name(
            image_name_with_tag=image_name,
            image_name_with_sha=image_name_with_sha,
        )
        
        logger.debug("Deleting pod")
        self._delete_pod(pod_name=pod_name)
        return image_name_with_sha

    def _generate_spec_overrides(
        self, pod_name: str, image_name: str, context: str
    ) -> Dict[str, Any]:
        """Generates Kubernetes spec overrides for the Kaniko build Pod.

        These values are used to override the default specification of the
        Kubernetes pod that is running the Kaniko build. This can be used to
        specify arguments for the Kaniko executor as well as providing
        environment variables and/or volume mounts.

        Args:
            pod_name: Name of the pod.
            image_name: Name of the image that should be built.
            context: The Kaniko executor context argument.

        Returns:
            Dictionary of spec override values.
        """
        args = [
            "--dockerfile=Dockerfile",
            f"--context={context}",
            f"--destination={image_name}",
            # Use the image name with repo digest as the Pod termination
            # message. We use this later to read the image name using kubectl.
            "--image-name-with-digest-file=/dev/termination-log",
        ] + self.config.executor_args

        optional_spec_args: Dict[str, Any] = {}
        if self.config.service_account_name:
            optional_spec_args["serviceAccountName"] = (
                self.config.service_account_name
            )
        logger.debug(f"Kaniko build args: {args}")
        logger.debug(f"Optional spec args: {optional_spec_args}")
        return {
            "apiVersion": "v1",
            "spec": {
                "containers": [
                    {
                        "name": pod_name,
                        "image": self.config.executor_image,
                        "stdin": True,
                        "stdinOnce": True,
                        "args": args,
                        "env": self.config.env,
                        "envFrom": self.config.env_from,
                        "volumeMounts": self.config.volume_mounts,
                    }
                ],
                "volumes": self.config.volumes,
                **optional_spec_args,
            },
        }

        

    def _run_kaniko_build(
        self,
        pod_name: str,
        spec_overrides: Dict[str, Any],
        build_context: "BuildContext",
    ) -> None:
        """Runs the Kaniko build in Kubernetes.

        Args:
            pod_name: Name of the Pod.
            spec_overrides: Pod spec override values.
            build_context: The build context.

        Raises:
            RuntimeError: If the Kaniko build fails.
        """
        command = [
            "kubectl",
            "--context",
            self.config.kubernetes_context,
            "--namespace",
            self.config.kubernetes_namespace,
            "run",
            pod_name,
            "--stdin",
            "true",
            "--restart",
            "Never",
            "--image",
            self.config.executor_image,
            "--overrides",
            json.dumps(spec_overrides),
            "--pod-running-timeout",
            f"{self.config.pod_running_timeout}s",
        ]

        logger.debug(f"Running Kaniko build with command: {command}")

        try:
            with subprocess.Popen(command, stdin=subprocess.PIPE, capture_output=True, text=True) as process:
                if not self.config.store_context_in_artifact_store:
                    self._write_build_context(process=process, build_context=build_context)

                try:  # Inner try block for process.wait()
                    stdout, stderr = process.communicate()  # Get all output and wait
                    return_code = process.returncode

                except Exception as wait_error:  # Catch exceptions during wait/communicate
                    process.kill()  # Kill the process if wait fails
                    logger.error(f"Error during Kaniko build (pod: {pod_name}) wait/communicate: {wait_error}")
                    try:
                        _, stderr = process.communicate() # Try to get the stderr anyway
                        logger.error(f"Kaniko build stderr (pod: {pod_name}) after kill:\n{stderr}")
                    except Exception as kill_error:
                        logger.error(f"Error getting stderr after kill: {kill_error}")
                    raise RuntimeError(f"Kaniko build failed (pod: {pod_name}) due to an error during wait/communicate: {wait_error}") from wait_error # Chain the exception

                if return_code:
                    logger.error(f"Kaniko build failed (pod: {pod_name}):\n{stderr}")
                    raise RuntimeError(f"Kaniko build failed (pod: {pod_name}). Return code: {return_code}. Check logs for more information.")

                logger.debug(f"Kaniko build (pod: {pod_name}) completed successfully:\n{stdout}")

        except Exception as e:  # Catch any other unexpected errors (outer block)
            logger.exception(f"An unexpected error occurred during Kaniko build (pod: {pod_name}):")
            raise  # Re-raise the exception


    @staticmethod
    def _write_build_context(
        process: BytePopen, build_context: "BuildContext"
        ) -> None:
        """Writes the build context to the process stdin.

        Args:
            process: The process to which the context will be written.
            build_context: The build context to write.

        Raises:
            RuntimeError: If writing the build context fails.
        """
        logger.debug("Writing build context to process stdin.")
        assert process.stdin

        try:
            with process.stdin as _, tempfile.TemporaryFile(mode="w+b") as f:
                build_context.write_archive(f, archive_type=ArchiveType.TAR_GZ)

                while True:
                    data = f.read(1024)  # Use a larger buffer size
                    if not data:
                        break
                    try:
                        process.stdin.write(data) # Write to stdin
                    except BrokenPipeError as e: # Handle broken pipe errors
                        logger.error("Broken pipe error when writing build context {}".format(e))
                        raise RuntimeError("Failed to write build context to stdin. Broken pipe.") from e
                    except Exception as e: # Catch any other exception during writing to stdin
                        logger.exception("An unexpected error occurred while writing build context to stdin: %s", e)
                        raise RuntimeError("Failed to write build context to stdin.") from e

        except Exception as e:  # Catch any other exceptions (e.g., file writing)
            logger.exception("An error occurred while creating/writing build context archive: %s", e)
            raise RuntimeError("Failed to create/write build context archive.") from e

        logger.debug("Build context written to process stdin.")

    @staticmethod
    def _generate_pod_name() -> str:
        """Generates a random name for the Pod that runs the Kaniko build.

        Returns:
            The Pod name.
        """
        logger.debug("Generating random Pod name for Kaniko build.")
        return f"kaniko-build-{random.Random().getrandbits(32):08x}"


    def _read_pod_output(self, pod_name: str) -> str:
        """Reads the Pod output message (image name with digest).

        Args:
            pod_name: Name of the Pod.

        Returns:
            The Pod output message (image name with digest).

        Raises:
            RuntimeError: If reading the Pod output fails.
        """
        command = [
            "kubectl",
            "--context",
            self.config.kubernetes_context,
            "--namespace",
            self.config.kubernetes_namespace,
            "get",
            "pod",
            pod_name,
            "-o",
            'jsonpath="{.status.containerStatuses[0].state.terminated.message}"',
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            output = result.stdout.strip('"\n')  # Strip quotes and newlines
            logger.debug(f"Kaniko build pod termination message: {output}")
            return output

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to read Pod output for '{pod_name}': {e}")
            logger.error(f"kubectl get pod stderr:\n{e.stderr}")
            raise RuntimeError(f"Failed to read Pod output for '{pod_name}'. Check logs for more information.") from e

        except Exception as e:
            logger.exception(f"An unexpected error occurred while reading Pod output for '{pod_name}':")
            raise RuntimeError(f"An unexpected error occurred while reading Pod output for '{pod_name}'.") from e


    def _delete_pod(self, pod_name: str) -> None:
        """Deletes a Pod.

        Args:
            pod_name: Name of the Pod to delete.

        Raises:
            RuntimeError: If the kubectl call to delete the Pod fails.
        """
        command = [
            "kubectl",
            "--context",
            self.config.kubernetes_context,
            "--namespace",
            self.config.kubernetes_namespace,
            "delete",
            "pod",
            pod_name,
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)  # capture_output, text
            logger.debug(f"kubectl delete pod output:\n{result.stdout}")  # Log successful output at debug level

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to delete Pod '{pod_name}': {e}")  # More descriptive message
            logger.error(f"kubectl delete pod stderr:\n{e.stderr}") # Log stderr for errors
            raise RuntimeError(f"Failed to delete Pod '{pod_name}'. Check logs for more information.") from e # Wrap in RuntimeError

        except Exception as e: # Catch any other exception during pod deletion
            logger.exception(f"An unexpected error occurred while deleting Pod '{pod_name}': {e}")  # Log unexpected error
            raise RuntimeError(f"An unexpected error occurred while deleting Pod '{pod_name}'.") from e

        logger.info(f"Deleted Kaniko build Pod '{pod_name}'.")  # Consistent formatting for successful deletion



    @staticmethod
    def _check_prerequisites() -> None:
        """Checks that all prerequisites are installed.

        Raises:
            RuntimeError: If any of the prerequisites are not installed.
        """
        try:
            if not shutil.which("kubectl"):
                raise RuntimeError("`kubectl` is required to run the Kaniko image builder.")

            # Check if kubectl can connect to the cluster (optional but recommended)
            try:
                subprocess.run(["kubectl", "version", "--short"], capture_output=True, check=True)  # Check kubectl connection
            except subprocess.CalledProcessError as e:
                logger.error(f"kubectl connection check failed: {e}")
                raise RuntimeError("kubectl is installed but cannot connect to the Kubernetes cluster. Check your Kubernetes context and configuration.") from e # Chaining exception

        except RuntimeError as e:  # Re-raise the caught exception after logging
            logger.error(f"Prerequisite check failed: {e}")
            raise  # Re-raise the RuntimeError
        except Exception as e:  # Catch any unexpected errors during the check
            logger.exception("An unexpected error occurred during prerequisite check: %s", e)
            raise  # Re-raise the exception
        logger.debug("Prerequisites check completed.")

    @staticmethod
    def _verify_image_name(image_name_with_tag: str, image_name_with_sha: str) -> None:
        """Verifies the image name and SHA digest.

        Args:
            image_name_with_tag: The image name with a tag (e.g., "my-image:latest").
            image_name_with_sha: The image name with SHA digest
                (e.g., "my-image@sha256:abcdef...").

        Raises:
            ValueError: If the image names do not match or the SHA digest is invalid.
        """
        try:
            image_name_without_tag, tag = image_name_with_tag.rsplit(":", 1)
        except ValueError:
            raise ValueError(f"Invalid image name with tag: {image_name_with_tag}.  Must be in the format 'image_name:tag'.")

        
        if not image_name_with_sha.startswith(image_name_without_tag):
            raise RuntimeError(
                f"The Kaniko Pod output {image_name_with_sha} is not a valid "
                f"image name in the repository {image_name_without_tag}."
            )
        
        logger.debug(f"Image name and SHA digest verified: {image_name_with_sha}")
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
        logger.info("Executing the build functi")
        logger.debug("Building image `%s` with Kaniko.", image_name)
        logger.debug("Docker build options: %s", docker_build_options)
        logger.debug("Build context: %s", build_context)
        logger.info("Executing function check_prerequisites")

        self._check_prerequisites()
        if not container_registry:
            logger.error(
                "Unable to use the Kaniko image builder without a container "
                "registry.")
            raise RuntimeError(
                "Unable to use the Kaniko image builder without a container "
                "registry."
            )

        logger.info("Generating pod name")
        pod_name = self._generate_pod_name()
        logger.debug(
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
                logger.error("Uploading the Kaniko build context to the artifact store failed." 
                    "Please make sure you have permissions to write "
                    "to the artifact store or update the Kaniko image builder "
                    "to stream the build context using stdin by running:\n"
                    f"  `zenml image-builder update {self.name}` "
                    "--store_context_in_artifact_store=False`")

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

        logger.info("Generating spec overrides")
        spec_overrides = self._generate_spec_overrides(
            pod_name=pod_name, image_name=image_name, context=kaniko_context
        )
        
        logger.info("Running Kaniko build")
        self._run_kaniko_build(
            pod_name=pod_name,
            spec_overrides=spec_overrides,
            build_context=build_context,
        )

        logger.info("Reading pod output")
        image_name_with_sha = self._read_pod_output(pod_name=pod_name)
        
        logger.info("Verifying image name")
        self._verify_image_name(
            image_name_with_tag=image_name,
            image_name_with_sha=image_name_with_sha,
        )
        
        logger.info("Deleting pod")
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
        logger.info("Generating spec overrides::_generate_spec_overrides")

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
        logger.info(f"Starting Kaniko build for Pod '{pod_name}'.")
        logger.debug(f"Pod name: {pod_name}")
        logger.debug(f"Spec overrides: {spec_overrides}")

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

        logger.debug(f"Executing Kaniko build command: {' '.join(command)}")

        try:
            with subprocess.Popen(command, stdin=subprocess.PIPE, capture_output=True, text=True) as process:
                # Write the build context to stdin if not stored in the artifact store
                if not self.config.store_context_in_artifact_store:
                    logger.debug("Writing build context to process stdin.")
                    self._write_build_context(process=process, build_context=build_context)

                try:
                    # Wait for the process to complete and capture output
                    stdout, stderr = process.communicate()
                    return_code = process.returncode

                    if return_code:
                        error_message = (
                            f"Kaniko build failed for Pod '{pod_name}' with return code {return_code}. "
                            "This indicates that the build process encountered an error. "
                            "Possible causes include: "
                            "1. The build context is invalid or incomplete. "
                            "2. The Kaniko executor image is misconfigured. "
                            "3. The Kubernetes cluster encountered an issue. "
                            "To resolve this issue, please: "
                            "1. Check the Kaniko build logs for errors. "
                            "2. Verify the build context and Kaniko configuration. "
                            "3. Ensure the Kubernetes cluster is accessible and stable."
                        )
                        logger.error(error_message)
                        logger.error(f"Command stderr:\n{stderr}")
                        raise RuntimeError(error_message)

                    logger.debug(f"Kaniko build completed successfully for Pod '{pod_name}'.")
                    logger.debug(f"Command stdout:\n{stdout}")

                except Exception as wait_error:
                    # Handle errors during process.wait() or process.communicate()
                    process.kill()  # Terminate the process if it's still running
                    error_message = (
                        f"An error occurred while waiting for the Kaniko build process to complete for Pod '{pod_name}'. "
                        "This could be due to a process crash, a timeout, or an internal error. "
                        "To resolve this issue, please: "
                        "1. Check the Kaniko build logs for errors. "
                        "2. Verify the Kubernetes cluster's health and connectivity. "
                        "3. Retry the build process."
                    )
                    logger.error(error_message)
                    logger.error(f"Error details: {wait_error}")

                    # Attempt to capture stderr after killing the process
                    try:
                        _, stderr = process.communicate()
                        logger.error(f"Command stderr after process kill:\n{stderr}")
                    except Exception as kill_error:
                        logger.error(f"Failed to capture stderr after process kill: {kill_error}")

                    raise RuntimeError(error_message) from wait_error

        except Exception as e:
            # Handle unexpected errors during the Kaniko build
            error_message = (
                f"An unexpected error occurred during the Kaniko build for Pod '{pod_name}'. "
                "This could be due to a system configuration issue, a network error, or an internal error. "
                "Please check the logs for more details and contact support if the issue persists."
            )
            logger.exception(error_message)
            raise RuntimeError(error_message) from e

        logger.info(f"Kaniko build completed successfully for Pod '{pod_name}'.")

    @staticmethod
    def _write_build_context(process: BytePopen, build_context: "BuildContext") -> None:
        """Writes the build context to the process stdin.

        Args:
            process: The process to which the context will be written.
            build_context: The build context to write.

        Raises:
            RuntimeError: If writing the build context fails.
        """
        logger.info("Starting to write build context to process stdin.")
        assert process.stdin, "Process stdin is not available."

        try:
            # Create a temporary file to store the build context archive
            with tempfile.TemporaryFile(mode="w+b") as f:
                logger.debug("Creating build context archive in temporary file.")
                build_context.write_archive(f, archive_type=ArchiveType.TAR_GZ)
                f.seek(0)  # Reset file pointer to the beginning

                # Write the build context to the process stdin
                logger.debug("Writing build context to process stdin.")
                while True:
                    data = f.read(1024)  # Use a buffer size of 1024 bytes
                    if not data:
                        break
                    try:
                        process.stdin.write(data)
                    except BrokenPipeError as e:
                        error_message = (
                            "A broken pipe error occurred while writing the build context to stdin. "
                            "This indicates that the process receiving the data has terminated unexpectedly. "
                            "Possible causes include: "
                            "1. The Kaniko build process crashed or was terminated. "
                            "2. The network connection to the Kubernetes cluster was interrupted. "
                            "To resolve this issue, please: "
                            "1. Check the Kaniko build logs for errors. "
                            "2. Verify that the Kubernetes cluster is accessible and stable. "
                            "3. Retry the build process."
                        )
                        logger.error(error_message)
                        raise RuntimeError(error_message) from e
                    except Exception as e:
                        error_message = (
                            "An unexpected error occurred while writing the build context to stdin. "
                            "This could be due to a system configuration issue or an internal error. "
                            "Please check the logs for more details and contact support if the issue persists."
                        )
                        logger.exception(error_message)
                        raise RuntimeError(error_message) from e

            logger.info("Build context successfully written to process stdin.")

        except Exception as e:
            # Handle unexpected errors during archive creation or writing
            error_message = (
                "An error occurred while creating or writing the build context archive. "
                "This could be due to a file system issue, insufficient permissions, or an internal error. "
                "Please check the logs for more details and contact support if the issue persists."
            )
            logger.exception(error_message)
            raise RuntimeError(error_message) from e

    @staticmethod
    def _generate_pod_name() -> str:
        """Generates a random name for the Pod that runs the Kaniko build.

        Returns:
            The Pod name.
        """
        logger.info("Starting generation of a random Pod name for the Kaniko build.")
        
        try:
            # Generate a random 32-bit hexadecimal value for the Pod name
            random_value = random.Random().getrandbits(32)
            pod_name = f"kaniko-build-{random_value:08x}"
            
            logger.debug(f"Generated random Pod name: {pod_name}")
            logger.info(f"Successfully generated Pod name::_generate_pod_name executed successfully.")
            return pod_name

        except Exception as e:
            # Handle unexpected errors during Pod name generation
            error_message = (
                "An unexpected error occurred while generating a random Pod name. "
                "This could be due to an issue with the random number generator or an internal error. "
                "Please check the logs for more details and contact support if the issue persists."
            )
            logger.exception(error_message)
            raise RuntimeError(error_message) from e


    def _read_pod_output(self, pod_name: str) -> str:
        """Reads the Pod output message (image name with digest).

        Args:
            pod_name: Name of the Pod.

        Returns:
            The Pod output message (image name with digest).

        Raises:
            RuntimeError: If reading the Pod output fails.
        """
        logger.info(f"Reading output from Kaniko build Pod '{pod_name}'.")
        logger.debug(f"Pod name: {pod_name}")

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

        logger.debug(f"Executing command to read Pod output: {' '.join(command)}")

        try:
            # Execute the `kubectl get pod` command to retrieve the Pod output
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            output = result.stdout.strip('"\n')  # Strip quotes and newlines
            logger.debug(f"Pod output retrieved successfully: {output}")
            logger.info(f"Successfully read output from Pod '{pod_name}'::_read_pod_output executed successfully.")
            return output

        except subprocess.CalledProcessError as e:
            # Handle errors from the `kubectl get pod` command
            error_message = (
                f"Failed to read output from Pod '{pod_name}' using `kubectl`. "
                "This indicates that the Pod output could not be retrieved. "
                "Possible causes include: "
                "1. The Pod does not exist or has not terminated yet. "
                "2. The container status or termination message is not available. "
                "3. A network or cluster connectivity issue. "
                "To resolve this issue, please: "
                "1. Verify that the Pod exists and has terminated by running `kubectl get pod {pod_name}`. "
                "2. Check the Pod logs for more details by running `kubectl logs {pod_name}`. "
                "3. Ensure your Kubernetes cluster is accessible and the context is correctly configured. "
                "4. Refer to the Kubernetes documentation for troubleshooting: "
                "   https://kubernetes.io/docs/reference/kubectl/cheatsheet/#inspecting-resources."
            )
            logger.error(error_message)
            logger.error(f"Command stderr:\n{e.stderr}")
            raise RuntimeError(error_message) from e

        except Exception as e:
            # Handle unexpected errors
            error_message = (
                f"An unexpected error occurred while reading output from Pod '{pod_name}'. "
                "This could be due to a system configuration issue or an internal error. "
                "Please check the logs for more details and contact support if the issue persists."
            )
            logger.exception(error_message)
            raise RuntimeError(error_message) from e

        


    def _delete_pod(self, pod_name: str) -> None:
        """Deletes a Pod.

        Args:
            pod_name: Name of the Pod to delete.

        Raises:
            RuntimeError: If the `kubectl` command fails to delete the Pod or if an unexpected error occurs.
        """
        logger.info(f"Starting deletion of Kaniko build Pod '{pod_name}'.")
        logger.debug(f"Command to delete Pod '{pod_name}' will be executed.")

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
            # Execute the `kubectl delete pod` command
            logger.debug(f"Executing command: {' '.join(command)}")
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logger.debug(f"Command output:\n{result.stdout}")
            logger.info(f"Successfully deleted Kaniko build Pod '{pod_name}'.")

        except subprocess.CalledProcessError as e:
            # Handle errors from the `kubectl delete pod` command
            error_message = (
                f"Failed to delete Pod '{pod_name}' using `kubectl`. "
                "This indicates that the Pod could not be deleted. "
                "Possible causes include: "
                "1. The Pod does not exist or has already been deleted. "
                "2. Insufficient permissions to delete the Pod. "
                "3. A network or cluster connectivity issue. "
                "To resolve this issue, please: "
                "1. Verify that the Pod exists by running `kubectl get pod {pod_name}`. "
                "2. Check your permissions by running `kubectl auth can-i delete pod {pod_name}`. "
                "3. Ensure your Kubernetes cluster is accessible and the context is correctly configured. "
                "4. Refer to the Kubernetes documentation for troubleshooting: "
                "   https://kubernetes.io/docs/reference/kubectl/cheatsheet/#deleting-resources."
            )
            logger.error(error_message)
            logger.error(f"Command stderr:\n{e.stderr}")
            raise RuntimeError(error_message) from e

        except Exception as e:
            # Handle unexpected errors
            error_message = (
                f"An unexpected error occurred while deleting Pod '{pod_name}'. "
                "This could be due to a system configuration issue or an internal error. "
                "Please check the logs for more details and contact support if the issue persists."
            )
            logger.exception(error_message)
            raise RuntimeError(error_message) from e

        logger.info(f"Pod deletion completed successfully::_delete_pod executed successfully.")

    @staticmethod
    def _check_prerequisites() -> None:
        """Checks that all prerequisites are installed.

        Raises:
            RuntimeError: If any of the prerequisites are not installed or if an unexpected error occurs.
        """
        logger.info("Starting prerequisite check for Kaniko image builder.")

        try:
            # Check if `kubectl` is installed
            if not shutil.which("kubectl"):
                error_message = (
                    "The `kubectl` command-line tool is not installed or not found in the system PATH. "
                    "`kubectl` is required to interact with the Kubernetes cluster and run the Kaniko image builder. "
                    "To resolve this issue, please: "
                    "1. Install `kubectl` by following the official Kubernetes documentation: "
                    "   https://kubernetes.io/docs/tasks/tools/install-kubectl/. "
                    "2. Ensure `kubectl` is added to your system PATH."
                )
                logger.error(error_message)
                raise RuntimeError(error_message)

            # Check if `kubectl` can connect to the Kubernetes cluster
            try:
                logger.debug("Checking `kubectl` connection to the Kubernetes cluster.")
                subprocess.run(["kubectl", "version", "--short"], capture_output=True, check=True)
                logger.debug("`kubectl` connection check successful.")
            except subprocess.CalledProcessError as e:
                error_message = (
                    "The `kubectl` command failed to connect to the Kubernetes cluster. "
                    "This indicates that either: "
                    "1. The Kubernetes cluster is not running or unreachable. "
                    "2. The `kubectl` configuration is incorrect or missing. "
                    "3. The current Kubernetes context is not set properly. "
                    "To resolve this issue, please: "
                    "1. Verify that your Kubernetes cluster is running and accessible. "
                    "2. Check your `kubectl` configuration by running `kubectl config view`. "
                    "3. Ensure the correct context is set using `kubectl config use-context <context-name>`. "
                    "4. Refer to the Kubernetes documentation for troubleshooting: "
                    "   https://kubernetes.io/docs/reference/kubectl/cheatsheet/#kubectl-context-and-configuration."
                )
                logger.error(error_message)
                raise RuntimeError(error_message) from e

        except RuntimeError as e:
            # Log and re-raise RuntimeError (e.g., missing `kubectl` or connection issues)
            logger.error(f"Prerequisite check failed: {e}")
            raise
        except Exception as e:
            # Log and re-raise unexpected errors
            error_message = (
                "An unexpected error occurred during the prerequisite check. "
                "This could be due to a system configuration issue or an internal error. "
                "Please check the logs for more details and contact support if the issue persists."
            )
            logger.exception(error_message)
            raise RuntimeError(error_message) from e

        logger.info("Prerequisite check completed successfully.")

    @staticmethod
    def _verify_image_name(image_name_with_tag: str, image_name_with_sha: str) -> None:
        """Verifies the image name and SHA digest.

        Args:
            image_name_with_tag: The image name with a tag (e.g., "my-image:latest").
            image_name_with_sha: The image name with SHA digest
                (e.g., "my-image@sha256:abcdef...").

        Raises:
            ValueError: If the image name with tag is invalid.
            RuntimeError: If the image name with SHA digest does not match the expected format.
        """
        logger.info("Starting verification of image name and SHA digest.")
        logger.debug(f"Image name with tag: {image_name_with_tag}")
        logger.debug(f"Image name with SHA digest: {image_name_with_sha}")

        # Validate the image name with tag format
        try:
            image_name_without_tag, tag = image_name_with_tag.rsplit(":", 1)
            logger.debug(f"Extracted image name without tag: {image_name_without_tag}, tag: {tag}")
        except ValueError as e:
            error_message = (
                f"Invalid image name with tag: '{image_name_with_tag}'. "
                "The image name must include a tag in the format 'image_name:tag'. "
                "For example, 'my-image:latest' or 'my-image:v1.0'. "
                "Please ensure the image name and tag are correctly formatted."
            )
            logger.error(error_message)
            raise ValueError(error_message) from e

        # Verify that the image name with SHA digest matches the expected format
        if not image_name_with_sha.startswith(image_name_without_tag):
            error_message = (
                f"The Kaniko Pod output '{image_name_with_sha}' does not match the "
                f"expected image name '{image_name_without_tag}'. "
                "This indicates that the image built by Kaniko does not belong to the expected repository. "
                "Possible causes include: "
                "1. The image name in the Kaniko build configuration is incorrect. "
                "2. The Kaniko build process was misconfigured. "
                "Please verify the image name in the Kaniko build configuration and ensure it matches the expected repository."
            )
            logger.error(error_message)
            raise RuntimeError(error_message)

        logger.info("Image name and SHA digest verified successfully.")

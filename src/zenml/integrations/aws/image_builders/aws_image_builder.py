#  Copyright (c) ZenML GmbH 2024. All Rights Reserved.
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

import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, cast
from urllib.parse import urlparse
from uuid import uuid4

import boto3

from zenml.enums import StackComponentType
from zenml.image_builders import BaseImageBuilder
from zenml.integrations.aws import (
    AWS_CONTAINER_REGISTRY_FLAVOR,
)
from zenml.integrations.aws.flavors import AWSImageBuilderConfig
from zenml.logger import get_logger
from zenml.stack import StackValidator
from zenml.utils.archivable import ArchiveType

if TYPE_CHECKING:
    from zenml.container_registries import BaseContainerRegistry
    from zenml.image_builders import BuildContext
    from zenml.stack import Stack

logger = get_logger(__name__)
logger.propagate = True  # Ensure logs propagate to the root logger

class AWSImageBuilder(BaseImageBuilder):
    """AWS Code Build image builder implementation."""

    _code_build_client: Optional[Any] = None

    @property
    def config(self) -> AWSImageBuilderConfig:
        """The stack component configuration.

        Returns:
            The configuration.
        """
        return cast(AWSImageBuilderConfig, self._config)

    @property
    def is_building_locally(self) -> bool:
        """Whether the image builder builds the images on the client machine.

        Returns:
            True if the image builder builds locally, False otherwise.
        """
        return False

    @property
    def validator(self) -> Optional["StackValidator"]:
        """Validates the stack for the AWS Code Build Image Builder.

        The AWS Code Build Image Builder requires a container registry to
        push the image to and an S3 Artifact Store to upload the build context,
        so AWS Code Build can access it.

        Returns:
            Stack validator.
        """

        def _validate_remote_components(stack: "Stack") -> Tuple[bool, str]:
            if stack.artifact_store.flavor != "s3":
                logger.error(
                    "The AWS Image Builder requires an S3 Artifact Store to "
                    "upload the build context, so AWS Code Build can access it."
                    "Please update your stack to include an S3 Artifact Store "
                    "and try again."
                )
                return False, (
                    "The AWS Image Builder requires an S3 Artifact Store to "
                    "upload the build context, so AWS Code Build can access it."
                    "Please update your stack to include an S3 Artifact Store "
                    "and try again."
                )

            return True, ""

        return StackValidator(
            required_components={StackComponentType.CONTAINER_REGISTRY},
            custom_validation_function=_validate_remote_components,
        )

    @property
    def code_build_client(self) -> Any:
        """The authenticated AWS Code Build client to use for interacting with AWS services.

        Returns:
            The authenticated AWS Code Build client.

        Raises:
            RuntimeError: If the AWS Code Build client cannot be created.
        """
        logger.info("Getting AWS CodeBuild client...")
        if (
            self._code_build_client is not None
            and self.connector_has_expired()
        ):
            self._code_build_client = None
        if self._code_build_client is not None:
            return self._code_build_client

        # Option 1: Service connector
        if connector := self.get_connector():
            boto_session = connector.connect()
            if not isinstance(boto_session, boto3.Session):
                logger.error(
                    f"Expected to receive a `boto3.Session` object from the "
                    f"linked connector, but got type `{type(boto_session)}`."
                )
                raise RuntimeError(
                    f"Expected to receive a `boto3.Session` object from the "
                    f"linked connector, but got type `{type(boto_session)}`."
                )
        # Option 2: Implicit configuration
        else:
            boto_session = boto3.Session()

        self._code_build_client = boto_session.client("codebuild")
        logger.info("AWS CodeBuild client created.")
        return self._code_build_client


    def build(
        self,
        image_name: str,
        build_context: "BuildContext",
        docker_build_options: Dict[str, Any],
        container_registry: Optional["BaseContainerRegistry"] = None,
    ) -> str:
        """Builds and pushes a Docker image using AWS CodeBuild.

        Args:
            image_name: Name of the image to build and push.
            build_context: The build context to use for the image.
            docker_build_options: Docker build options.
            container_registry: Optional container registry to push to.

        Returns:
            The Docker image name with digest.

        Raises:
            RuntimeError: If no container registry is passed.
            RuntimeError: If the AWS CodeBuild build fails.
        """
        logger.info(f"Starting AWS CodeBuild")
        logger.debug(f"Build context: {build_context}")
        logger.debug(f"Docker build options: {docker_build_options}")

        if not container_registry:
            logger.error("No container registry provided. AWS Image Builder requires a container registry.")
            raise RuntimeError(
                "The AWS Image Builder requires a container registry to push "
                "the image to. Please provide one and try again."
            )

        try:
            # Upload build context to S3
            logger.info("Uploading build context to S3...")
            try:
                cloud_build_context = self._upload_build_context(
                    build_context=build_context,
                    parent_path_directory_name=f"code-build-contexts/{str(self.id)}",
                    archive_type=ArchiveType.ZIP,
                )
                url_parts = urlparse(cloud_build_context)
                bucket = url_parts.netloc
                object_path = url_parts.path.lstrip("/")
                logger.info(f"Build context uploaded to S3: {bucket}, object path: {object_path}")
            except Exception as e:
                logger.exception("Failed to upload build context to S3:")
                raise

            # Prepare environment variables and pre-build commands
            environment_variables_override: Dict[str, str] = {}
            pre_build_commands = []
            if not self.config.implicit_container_registry_auth:
                credentials = container_registry.credentials
                if credentials:
                    environment_variables_override = {
                        "CONTAINER_REGISTRY_USERNAME": credentials[0],
                        "CONTAINER_REGISTRY_PASSWORD": credentials[1],
                    }
                    pre_build_commands = [
                        "echo Logging in to container registry",
                        'echo "$CONTAINER_REGISTRY_PASSWORD" | docker login --username "$CONTAINER_REGISTRY_USERNAME" --password-stdin '
                        f"{container_registry.config.uri}",
                    ]
            elif container_registry.flavor == AWS_CONTAINER_REGISTRY_FLAVOR:
                pre_build_commands = [
                    "echo Logging in to EKS",
                    f"aws ecr get-login-password --region {self.code_build_client._client_config.region_name} | docker login --username AWS --password-stdin {container_registry.config.uri}",
                ]

            # Convert docker_build_options to a string
            docker_build_args = ""
            for key, value in docker_build_options.items():
                option = f"--{key}"
                if isinstance(value, list):
                    for val in value:
                        docker_build_args += f"{option} {val} "
                elif value is not None and not isinstance(value, bool):
                    docker_build_args += f"{option} {value} "
                elif value is not False:
                    docker_build_args += f"{option} "

            # Generate buildspec
            pre_build_commands_str = "\n".join(
                [f"            - {command}" for command in pre_build_commands]
            )

            build_id = str(uuid4())
            repo_name = image_name.split(":")[0]
            alt_image_name = f"{repo_name}:{build_id}"

            buildspec = f"""
    version: 0.2
    phases:
        pre_build:
            commands:
    {pre_build_commands_str}
        build:
            commands:
                - echo Build started on `date`
                - echo Building the Docker image...
                - docker build -t {image_name} . {docker_build_args}
                - echo Build completed on `date`
        post_build:
            commands:
                - echo Pushing the Docker image...
                - docker push {image_name}
                - docker tag {image_name} {alt_image_name}
                - docker push {alt_image_name}
                - echo Pushed the Docker image
    artifacts:
        files:
            - '**/*'
    """

            # Add custom environment variables
            if self.config.custom_env_vars:
                environment_variables_override.update(self.config.custom_env_vars)

            environment_variables_override_list = [
                {"name": key, "value": value, "type": "PLAINTEXT"}
                for key, value in environment_variables_override.items()
            ]

            logger.info("Buildspec file generated.")
            logger.debug(f"Buildspec:\n{buildspec}")
            # Start AWS CodeBuild job
            logger.info("Starting AWS CodeBuild job...")
            try:
                response = self.code_build_client.start_build(
                    projectName=self.config.code_build_project,
                    environmentTypeOverride="LINUX_CONTAINER",
                    imageOverride=self.config.build_image,
                    computeTypeOverride=self.config.compute_type,
                    privilegedModeOverride=False,
                    sourceTypeOverride="S3",
                    sourceLocationOverride=f"{bucket}/{object_path}",
                    buildspecOverride=buildspec,
                    environmentVariablesOverride=environment_variables_override_list,
                    artifactsOverride={"type": "NO_ARTIFACTS"},
                )

                build_arn = response["build"]["arn"]
                aws_region, aws_account, build = build_arn.split(":", maxsplit=5)[3:6]
                codebuild_project = build.split("/")[1].split(":")[0]

                logs_url = f"https://{aws_region}.console.aws.amazon.com/codesuite/codebuild/{aws_account}/projects/{codebuild_project}/{build}/log"
                logger.info(f"AWS CodeBuild job started. Build logs: {logs_url}")

                # Wait for the build to complete
                code_build_id = response["build"]["id"]
                while True:
                    try:
                        build_status = self.code_build_client.batch_get_builds(ids=[code_build_id])
                        build = build_status["builds"][0]
                        status = build["buildStatus"]
                        logger.debug(f"Current build status: {status}")

                        if status in ["SUCCEEDED", "FAILED", "FAULT", "TIMED_OUT", "STOPPED"]:
                            break
                        time.sleep(10)
                    except Exception as e:
                        logger.exception("Error checking build status, retrying in 10s:")
                        time.sleep(10)

                if status != "SUCCEEDED":
                    logger.error(f"AWS CodeBuild job failed. Build logs: {logs_url}")
                    logger.error(f"Build status: {status}")
                    error_message = build.get("error", "No error details available")
                    logger.error(f"Build error: {error_message}")

                    # More specific error logging:
                    if status == "FAILED":
                        logger.error(
                            "CodeBuild FAILED: The build process encountered an error.  "
                            "This could be due to issues with the Dockerfile, build dependencies, "
                            "or commands within the buildspec.  Examine the CodeBuild logs closely "
                            "for specific error messages from the build tools (docker build, etc.)."
                        )
                    elif status == "FAULT":
                        logger.error(
                            "CodeBuild FAULT:  An internal error occurred within the CodeBuild service. "
                            "This is usually a transient issue. Check the AWS service health dashboard for "
                            "any reported outages or problems. If the issue persists, contact AWS support."
                        )
                    elif status == "TIMED_OUT":
                        logger.error(
                            "CodeBuild TIMED_OUT: The build exceeded the configured time limit.  "
                            "Increase the timeout setting in your CodeBuild project.  Analyze the build "
                            "logs to identify long-running steps and optimize the build process if possible."
                        )
                    elif status == "STOPPED":
                        logger.error(
                            "CodeBuild STOPPED: The build was manually stopped or was stopped by a lifecycle "
                            "policy in your CodeBuild project.  Check if a user cancelled the build or if any "
                            "automated processes terminated it."
                        )

                    raise RuntimeError(
                        f"The Code Build run to build the Docker image has failed. More "
                        f"information can be found in the Cloud Build logs: {logs_url}."
                    )

                logger.info(f"AWS CodeBuild job succeeded. Build logs: {logs_url}")
                return alt_image_name

            except Exception as e:
                logger.exception("Error starting or monitoring AWS CodeBuild job:")
                raise

        except Exception as e:
            logger.exception("AWS CodeBuild job failed. Unexpected error:")
            logger.error("Check the following for possible issues:")
            logger.error(
                "1. **AWS Credentials and Permissions:** Verify that the AWS credentials "
                "used by the ZenML stack have the necessary permissions to perform the following "
                "actions:\n"
                "   - `codebuild:StartBuild` (to start the CodeBuild job)\n"
                "   - `s3:GetObject` (to download the build context from S3)\n"
                "   - `s3:PutObject` (to upload artifacts, if any)\n"
                "   - `ecr:BatchCheckLayerAvailability`, `ecr:GetAuthorizationToken`, `ecr:BatchPutImage`, `ecr:CompleteLayerUpload`, `ecr:UploadLayerPart` (if using ECR)\n"  # Add other registry permissions as needed
                "   - and other permissions as defined in your CodeBuild project.\n"
                "   Check your IAM policies and ensure they are correctly attached to the user or role."
            )
            logger.error(
                "2. **S3 Bucket and Object Accessibility:** Ensure the S3 bucket specified for "
                "the build context exists and is accessible.  Verify that the build context archive "
                "was successfully uploaded to the correct path within the bucket.  Check the bucket's "
                "ACLs and policies to confirm that the IAM role used by CodeBuild has read access."
            )
            logger.error(
                "3. **AWS CodeBuild Project Configuration:** Double-check the configuration of your "
                "CodeBuild project in the AWS console.  Pay attention to the following settings:\n"
                "   - *Source*: Make sure the source is correctly configured to point to the S3 bucket "
                "     containing the build context.\n"
                "   - *Environment*: Verify the build environment settings, including the build image, "
                "     compute type, and any environment variables.\n"
                "   - *Buildspec*: Ensure the buildspec file (`buildspec.yml`) is present in the root of "
                "     your build context and contains the correct build commands.\n"
                "   - *Service Role*: Confirm that the CodeBuild project is using an IAM service role with "
                "     the necessary permissions (as described in step 1)."
            )
            logger.error(
                f"4. **CodeBuild Logs:** The CodeBuild logs are the most valuable resource for "
                f"troubleshooting build failures.  Carefully examine the logs at: {logs_url} "
                f"for any error messages, warnings, or other clues about the cause of the problem.  "
                f"Pay close attention to error messages from the build tools (e.g., `docker build`, "
                f"commands in your buildspec), as well as any issues related to dependency resolution, "
                f"network connectivity, or resource limitations."
            )
            logger.error(
                "5. **Build Context:**  Ensure your build context is correctly structured and "
                "contains all the necessary files, including the Dockerfile and any required "
                "dependencies.  If you're using a `.dockerignore` file, make sure it's not accidentally "
                "excluding files that are needed for the build.  Verify that the Dockerfile is valid and "
                "that the `COPY` and `ADD` instructions are referencing the correct paths within the "
                "build context."
            )
            logger.error(
                "6. **Docker Build Options:** Review the `docker_build_options` you provided.  "
                "Incorrect or conflicting options can lead to build failures.  Double-check options "
                "like `--build-arg`, `--cache-from`, `--target`, etc., and ensure they are appropriate "
                "for your build scenario."
            )
            logger.error(
                "7. **Container Registry (ECR, etc.):** If you're pushing the image to a container "
                "registry (like ECR), verify that the registry is accessible and that your AWS credentials "
                "have the necessary permissions to push images to it.  Check the registry's settings and "
                "ensure that the image name you're using is correct."
            )
            logger.error(
                "8. **Network Connectivity:**  Problems with network connectivity can prevent the build "
                "from downloading dependencies, pulling base images, or pushing the final image to the "
                "registry.  Check your network configuration and ensure that the CodeBuild environment has "
                "access to the necessary external resources."
            )
            logger.error(
                "9. **Resource Limits:** CodeBuild projects have resource limits (e.g., compute type, memory, "
                "disk space).  If your build requires more resources than are available, it might fail.  "
                "Consider increasing the resource limits for your CodeBuild project."
            )
            raise  # Re-raise the exception

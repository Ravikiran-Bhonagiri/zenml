#  Copyright (c) ZenML GmbH 2023. All Rights Reserved.
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

import logging
from unittest.mock import patch, MagicMock
import pytest
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.image_builders.aws_image_builder import AWSImageBuilder, AWSImageBuilderConfig

@pytest.fixture
def aws_image_builder():
    """Fixture for creating an AWSImageBuilder instance."""
    config = AWSImageBuilderConfig(
        code_build_project="test-project",
        build_image="test-build-image",
        compute_type="BUILD_GENERAL1_SMALL",
    )
    return AWSImageBuilder(config)

@pytest.fixture
def mock_build_context():
    """Fixture for creating a mock BuildContext."""
    return MagicMock(spec=BuildContext)

@pytest.fixture
def mock_container_registry():
    """Fixture for creating a mock BaseContainerRegistry."""
    return MagicMock(spec=BaseContainerRegistry)

def test_aws_image_builder_logging(aws_image_builder, mock_build_context, mock_container_registry, caplog):
    """Test that the AWSImageBuilder logs messages correctly during execution."""
    # Set up test data
    image_name = "test-image:latest"
    docker_build_options = {"arg1": "value1", "arg2": "value2"}

    # Mock external dependencies
    with patch.object(aws_image_builder, "_upload_build_context", return_value="s3://test-bucket/test-context"), \
         patch.object(aws_image_builder, "code_build_client") as mock_code_build_client, \
         patch("time.sleep"):

        # Mock AWS CodeBuild response
        mock_code_build_client.start_build.return_value = {
            "build": {
                "arn": "arn:aws:codebuild:us-west-2:123456789012:build/test-project:12345",
                "id": "test-build-id",
            }
        }
        mock_code_build_client.batch_get_builds.return_value = {
            "builds": [{"buildStatus": "SUCCEEDED"}]
        }

        # Execute the build method
        with caplog.at_level(logging.INFO):
            aws_image_builder.build(
                image_name=image_name,
                build_context=mock_build_context,
                docker_build_options=docker_build_options,
                container_registry=mock_container_registry,
            )

        # Verify log messages
        logs = caplog.text

        # Check initial logs
        assert "Starting AWS CodeBuild" in logs
        assert f"Build context: {mock_build_context}" in logs
        assert f"Docker build options: {docker_build_options}" in logs

        # Check logs for build context upload
        assert "Uploading build context to S3..." in logs
        assert "Build context uploaded to S3: test-bucket, object path: test-context" in logs

        # Check logs for AWS CodeBuild job execution
        assert "Starting AWS CodeBuild job..." in logs
        assert "AWS CodeBuild job started. Build logs: https://us-west-2.console.aws.amazon.com/codesuite/codebuild/123456789012/projects/test-project/test-build-id/log" in logs
        assert "AWS CodeBuild job succeeded. Build logs: https://us-west-2.console.aws.amazon.com/codesuite/codebuild/123456789012/projects/test-project/test-build-id/log" in logs

        # Check logs for successful image build and push
        assert "Successfully built and pushed image" in logs
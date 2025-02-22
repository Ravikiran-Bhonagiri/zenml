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
from uuid import uuid4
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.integrations.aws.image_builders.aws_image_builder import (
    AWSImageBuilder,
    AWSImageBuilderConfig,
)
from zenml.enums import StackComponentType

# Configure logging to ensure logs are captured
logging.basicConfig(level=logging.DEBUG)

# Fixture for creating an AWSImageBuilder instance
@pytest.fixture
def aws_image_builder():
    """Fixture for creating an AWSImageBuilder instance."""
    config = AWSImageBuilderConfig(
        code_build_project="test-project",
        build_image="test-build-image",
        compute_type="BUILD_GENERAL1_SMALL",
    )
    return AWSImageBuilder(
        id=uuid4(),  # Generate a unique ID
        config=config,
        name="aws-image-builder",
        type=StackComponentType.IMAGE_BUILDER,
        flavor="aws",
        user=uuid4(),  # Mock user ID
        workspace=uuid4(),  # Mock workspace ID
        created="2023-01-01T00:00:00Z",  # Mock creation timestamp
        updated="2023-01-01T00:00:00Z",  # Mock update timestamp
    )

# Fixture for creating a mock BuildContext
@pytest.fixture
def mock_build_context():
    """Fixture for creating a mock BuildContext."""
    return MagicMock(spec=BuildContext)

# Fixture for creating a mock BaseContainerRegistry
@pytest.fixture
def mock_container_registry():
    """Fixture for creating a mock BaseContainerRegistry."""
    return MagicMock(spec=BaseContainerRegistry)

# Fixture for creating mock Docker build options
@pytest.fixture
def mock_docker_build_options():
    """Fixture for creating mock Docker build options."""
    return {"tag": "latest"}


# Test 1: Build image failure (invalid container registry)
def test_build_image_failure_invalid_container_registry(aws_image_builder, mock_build_context, mock_docker_build_options, caplog):
    """Test that an image build fails when no container registry is provided."""
    logging.debug("Starting test_build_image_failure_invalid_container_registry")
    
    # Mock AWS region and Docker client
    with patch('boto3.Session') as mock_boto_session, \
         patch('zenml.image_builders.base_image_builder.BaseImageBuilder._upload_build_context'):
        
        # Mock the AWS region
        mock_boto_session.return_value.region_name = "us-west-2"
        
        image_name = "test_image"
        
        # Ensure logs are captured
        with pytest.raises(RuntimeError) as exc_info:
            aws_image_builder.build(
                image_name=image_name,
                build_context=mock_build_context,
                docker_build_options=mock_docker_build_options,
                container_registry=None,
            )
        
        # Verify the error message
        assert "The AWS Image Builder requires a container registry to push the image to" in str(exc_info.value)
    
    logging.debug("Finished test_build_image_failure_invalid_container_registry")



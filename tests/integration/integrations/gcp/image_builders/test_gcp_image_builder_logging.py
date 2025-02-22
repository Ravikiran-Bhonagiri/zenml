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
from datetime import datetime
from google.cloud.devtools import cloudbuild_v1
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.integrations.gcp.image_builders.gcp_image_builder import GCPImageBuilder, GCPImageBuilderConfig
from zenml.enums import StackComponentType

@pytest.fixture
def gcp_image_builder():
    """Fixture for creating a GCPImageBuilder instance."""
    # Mock the GCPImageBuilderConfig class
    mock_config = MagicMock()
    mock_config.cloud_builder_image = "test-builder-image"
    mock_config.build_timeout = 600

    # Create a GCPImageBuilder instance with the mocked config
    return GCPImageBuilder(
            name="gcp-image-builder",
            id=uuid4(),
            config=mock_config,
            flavor="gcp",
            type=StackComponentType.IMAGE_BUILDER,
            user=uuid4(),
            workspace=uuid4(),
            created=datetime.now(),
            updated=datetime.now(),
        )
@pytest.fixture
def mock_build_context():
    """Fixture for creating a mock BuildContext."""
    return MagicMock(spec=BuildContext)

@pytest.fixture
def mock_container_registry():
    """Fixture for creating a mock BaseContainerRegistry."""
    return MagicMock(spec=BaseContainerRegistry)

def test_gcp_image_builder_build(gcp_image_builder, mock_build_context, mock_container_registry):
    """Test that the GCPImageBuilder.build method works as expected."""
    # Set up test data
    image_name = "test-image:latest"
    docker_build_options = {"arg1": "value1", "arg2": "value2"}
    expected_image_digest = "sha256:abc123"

    # Mock external dependencies
    with patch.object(gcp_image_builder, "_upload_build_context", return_value="gs://test-bucket/test-context"), \
         patch.object(gcp_image_builder, "_configure_cloud_build", return_value=MagicMock()), \
         patch.object(gcp_image_builder, "_run_cloud_build", return_value=expected_image_digest), \
         patch.object(gcp_image_builder, "_get_authentication", return_value=(MagicMock(), "test-project")):

        # Call the build method
        result = gcp_image_builder.build(
            image_name=image_name,
            build_context=mock_build_context,
            docker_build_options=docker_build_options,
            container_registry=mock_container_registry,
        )

        # Verify the result
        assert result == f"test-image@{expected_image_digest}"

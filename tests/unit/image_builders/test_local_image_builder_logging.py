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
from zenml.image_builders.local_image_builder import (
    LocalImageBuilder,
    LocalImageBuilderConfig,
)
from zenml.enums import StackComponentType

# Configure logging to ensure logs are captured
logging.basicConfig(level=logging.DEBUG)

# Fixture for creating a LocalImageBuilder instance
@pytest.fixture
def local_image_builder():
    """Fixture for creating a LocalImageBuilder instance."""
    config = LocalImageBuilderConfig()
    builder = LocalImageBuilder(
        id=uuid4(),
        config=config,
        name="local-image-builder",
        type=StackComponentType.IMAGE_BUILDER,
        flavor="local",
        user=uuid4(),
        workspace=uuid4(),
        created="2023-01-01T00:00:00Z",
        updated="2023-01-01T00:00:00Z",
    )
    logging.debug("Created LocalImageBuilder instance: %s", builder)
    return builder

# Test 1: Check prerequisites successfully
def test_check_prerequisites_success(local_image_builder, caplog):
    """Test that prerequisites are checked successfully."""
    logging.debug("Starting test_check_prerequisites_success")
    
    # Set the logging level for the LocalImageBuilder logger
    logger = logging.getLogger("zenml.image_builders.local_image_builder")
    logger.setLevel(logging.INFO)
    logger.propagate = True  # Ensure logs propagate to the root logger

    with patch('shutil.which', return_value=True), \
         patch('zenml.utils.docker_utils.check_docker', return_value=True):
        # Ensure logs are captured
        with caplog.at_level(logging.INFO, logger="zenml.image_builders.local_image_builder"):
            local_image_builder._check_prerequisites()
        
        # Verify log messages
        logging.debug("Captured logs: %s", caplog.text)
        assert "'docker' executable found." in caplog.text
        assert "Successfully connected to the Docker daemon." in caplog.text
        assert "All prerequisites check passed." in caplog.text
    logging.debug("Finished test_check_prerequisites_success")


# Test 2: Check prerequisites when Docker is not found
def test_check_prerequisites_docker_not_found(local_image_builder, caplog):
    """Test that an error is logged and raised when Docker is not found."""
    logging.debug("Starting test_check_prerequisites_docker_not_found")
    
    # Set the logging level for the LocalImageBuilder logger
    logger = logging.getLogger("zenml.image_builders.local_image_builder")
    logger.setLevel(logging.INFO)
    logger.propagate = True  # Ensure logs propagate to the root logger

    with patch('shutil.which', return_value=False):
        with pytest.raises(RuntimeError):
            # Ensure logs are captured
            with caplog.at_level(logging.INFO, logger="zenml.image_builders.local_image_builder"):
                local_image_builder._check_prerequisites()
        
        # Verify log messages
        print("Captured logs: %s", caplog.text)
        assert "Prerequisite check failed: 'docker' is required to run the local image builder." in caplog.text
    logging.debug("Finished test_check_prerequisites_docker_not_found")


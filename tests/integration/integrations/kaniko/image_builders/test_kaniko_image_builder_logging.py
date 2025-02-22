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

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4
from datetime import datetime
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.integrations.kaniko.image_builders.kaniko_image_builder import KanikoImageBuilder
from zenml.enums import StackComponentType

def test_kaniko_image_builder_check_prerequisites_missing_kubectl():
    """Test that the `_check_prerequisites` method raises a RuntimeError when `kubectl` is missing."""
    # Mock the `shutil.which` function to simulate `kubectl` not being installed
    with patch("shutil.which", return_value=None):
        # Create a KanikoImageBuilder instance
        kaniko_image_builder = KanikoImageBuilder(
            name="kaniko-image-builder",
            id="test-id",
            config=MagicMock(),
            flavor="kaniko",
            type=StackComponentType.IMAGE_BUILDER,
            user="test-user",
            workspace="test-workspace",
            created=datetime.now(),
            updated=datetime.now(),
        )

        # Verify that the `_check_prerequisites` method raises a RuntimeError
        with pytest.raises(RuntimeError) as exc_info:
            kaniko_image_builder._check_prerequisites()

        # Verify the error message
        assert "The `kubectl` command-line tool is not installed or not found in the system PATH." in str(exc_info.value)

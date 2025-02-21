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

from unittest.mock import patch, MagicMock
import pytest
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.image_builders.local_image_builder import LocalImageBuilder, LocalImageBuilderConfig

@pytest.fixture
def local_image_builder():
    config = LocalImageBuilderConfig()
    return LocalImageBuilder(config)

@pytest.fixture
def mock_build_context():
    return MagicMock(spec=BuildContext)

@pytest.fixture
def mock_container_registry():
    return MagicMock(spec=BaseContainerRegistry)

def test_check_prerequisites_success(local_image_builder, caplog):
    with patch('shutil.which', return_value=True), \
         patch('zenml.utils.docker_utils.check_docker', return_value=True):
        local_image_builder._check_prerequisites()
        
        assert "'docker' executable found." in caplog.text
        assert "Successfully connected to the Docker daemon." in caplog.text
        assert "All prerequisites check passed." in caplog.text

def test_check_prerequisites_docker_not_found(local_image_builder, caplog):
    with patch('shutil.which', return_value=False):
        with pytest.raises(RuntimeError):
            local_image_builder._check_prerequisites()
        
        assert "'docker' executable found." not in caplog.text
        assert "Prerequisite check failed: 'docker' is required to run the local image builder." in caplog.text

def test_check_prerequisites_docker_daemon_not_running(local_image_builder, caplog):
    with patch('shutil.which', return_value=True), \
         patch('zenml.utils.docker_utils.check_docker', return_value=False):
        with pytest.raises(RuntimeError):
            local_image_builder._check_prerequisites()
        
        assert "Unable to connect to the Docker daemon." in caplog.text
        assert "Prerequisite check failed: Failed to connect to the Docker daemon." in caplog.text

def test_build_image_success(local_image_builder, mock_build_context, caplog):
    with patch('tempfile.NamedTemporaryFile'), \
         patch('zenml.utils.docker_utils._try_get_docker_client_from_env'), \
         patch('docker.client.DockerClient.images.client.api.build') as mock_build, \
         patch('zenml.utils.docker_utils._process_stream', return_value="image_digest"):
        
        mock_build.return_value = [b'{"stream":"Step 1/10 : FROM python:3.8-slim\\n"}']
        
        image_name = "test_image"
        image_digest = local_image_builder.build(image_name, mock_build_context)
        
        assert f"Starting build of image '{image_name}'..." in caplog.text
        assert f"Image '{image_name}' built successfully. Digest: image_digest" in caplog.text
        assert image_digest == "image_digest"

def test_build_image_with_registry_success(local_image_builder, mock_build_context, mock_container_registry, caplog):
    with patch('tempfile.NamedTemporaryFile'), \
         patch('zenml.utils.docker_utils._try_get_docker_client_from_env'), \
         patch('docker.client.DockerClient.images.client.api.build') as mock_build, \
         patch('zenml.utils.docker_utils._process_stream', return_value="image_digest"), \
         patch.object(mock_container_registry, 'push_image', return_value="repo_digest"):
        
        mock_build.return_value = [b'{"stream":"Step 1/10 : FROM python:3.8-slim\\n"}']
        
        image_name = "test_image"
        repo_digest = local_image_builder.build(image_name, mock_build_context, container_registry=mock_container_registry)
        
        assert f"Starting build of image '{image_name}'..." in caplog.text
        assert f"Image '{image_name}' built successfully. Digest: image_digest" in caplog.text
        assert f"Pushing image '{image_name}' to {mock_container_registry.name}..." in caplog.text
        assert f"Image '{image_name}' pushed successfully. Repo digest: repo_digest" in caplog.text
        assert repo_digest == "repo_digest"

def test_build_image_failure(local_image_builder, mock_build_context, caplog):
    with patch('tempfile.NamedTemporaryFile'), \
         patch('zenml.utils.docker_utils._try_get_docker_client_from_env'), \
         patch('docker.client.DockerClient.images.client.api.build', side_effect=Exception("Build failed")):
        
        image_name = "test_image"
        with pytest.raises(RuntimeError):
            local_image_builder.build(image_name, mock_build_context)
        
        assert f"Starting build of image '{image_name}'..." in caplog.text
        assert "Error during Docker image build:" in caplog.text
        assert "An unexpected error occurred during the build/push process: Build failed" in caplog.text

def test_build_image_push_failure(local_image_builder, mock_build_context, mock_container_registry, caplog):
    with patch('tempfile.NamedTemporaryFile'), \
         patch('zenml.utils.docker_utils._try_get_docker_client_from_env'), \
         patch('docker.client.DockerClient.images.client.api.build') as mock_build, \
         patch('zenml.utils.docker_utils._process_stream', return_value="image_digest"), \
         patch.object(mock_container_registry, 'push_image', side_effect=Exception("Push failed")):
        
        mock_build.return_value = [b'{"stream":"Step 1/10 : FROM python:3.8-slim\\n"}']
        
        image_name = "test_image"
        with pytest.raises(RuntimeError):
            local_image_builder.build(image_name, mock_build_context, container_registry=mock_container_registry)
        
        assert f"Starting build of image '{image_name}'..." in caplog.text
        assert f"Image '{image_name}' built successfully. Digest: image_digest" in caplog.text
        assert f"Pushing image '{image_name}' to {mock_container_registry.name}..." in caplog.text
        assert f"Error pushing image '{image_name}' to {mock_container_registry.name}: Push failed" in caplog.text
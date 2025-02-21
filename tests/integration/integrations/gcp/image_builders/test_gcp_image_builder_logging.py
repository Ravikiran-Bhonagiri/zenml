import logging
from unittest.mock import patch, MagicMock
import pytest
from google.cloud.devtools import cloudbuild_v1
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.image_builders.gcp_image_builder import GCPImageBuilder, GCPImageBuilderConfig

@pytest.fixture
def gcp_image_builder():
    """Fixture for creating a GCPImageBuilder instance."""
    config = GCPImageBuilderConfig(
        project_id="test-project",
        cloud_builder_image="test-builder-image",
        build_timeout=600,
    )
    return GCPImageBuilder(config)

@pytest.fixture
def mock_build_context():
    """Fixture for creating a mock BuildContext."""
    return MagicMock(spec=BuildContext)

@pytest.fixture
def mock_container_registry():
    """Fixture for creating a mock BaseContainerRegistry."""
    return MagicMock(spec=BaseContainerRegistry)

def test_gcp_image_builder_logging(gcp_image_builder, mock_build_context, mock_container_registry, caplog):
    """Test that the GCPImageBuilder logs messages correctly during execution."""
    # Set up test data
    image_name = "test-image:latest"
    docker_build_options = {"arg1": "value1", "arg2": "value2"}

    # Mock external dependencies
    with patch.object(gcp_image_builder, "_upload_build_context", return_value="gs://test-bucket/test-context"), \
         patch.object(gcp_image_builder, "_configure_cloud_build", return_value=cloudbuild_v1.Build()), \
         patch.object(gcp_image_builder, "_run_cloud_build", return_value="sha256:abc123"), \
         patch.object(gcp_image_builder, "_get_authentication", return_value=(MagicMock(), "test-project")):

        # Execute the build method
        with caplog.at_level(logging.INFO):
            gcp_image_builder.build(
                image_name=image_name,
                build_context=mock_build_context,
                docker_build_options=docker_build_options,
                container_registry=mock_container_registry,
            )

        # Verify log messages
        logs = caplog.text

        # Check initial logs
        assert f"Starting build for image '{image_name}'..." in logs
        assert f"Build context: {mock_build_context}" in logs
        assert f"Docker build options: {docker_build_options}" in logs

        # Check logs for build context upload
        assert "Uploading build context..." in logs
        assert "Build context uploaded." in logs

        # Check logs for Cloud Build job configuration
        assert "Configuring Cloud Build job..." in logs
        assert "Cloud Build job configured." in logs

        # Check logs for Cloud Build job execution
        assert "Running Cloud Build job..." in logs
        assert "Started Cloud Build job" in logs
        assert "Cloud Build job completed successfully" in logs

        # Check logs for successful image build and push
        assert "Successfully built and pushed image" in logs
        assert f"Successfully built and pushed image 'test-image@sha256:abc123'." in logs
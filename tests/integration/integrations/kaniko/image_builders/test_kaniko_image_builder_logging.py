import logging
from unittest.mock import patch, MagicMock
import pytest
from zenml.image_builders import BuildContext
from zenml.container_registries import BaseContainerRegistry
from zenml.image_builders.kaniko_image_builder import KanikoImageBuilder, KanikoImageBuilderConfig

@pytest.fixture
def kaniko_image_builder():
    """Fixture for creating a KanikoImageBuilder instance."""
    config = KanikoImageBuilderConfig(
        kubernetes_context="test-context",
        kubernetes_namespace="test-namespace",
        executor_image="test-executor-image",
    )
    return KanikoImageBuilder(config)

@pytest.fixture
def mock_build_context():
    """Fixture for creating a mock BuildContext."""
    return MagicMock(spec=BuildContext)

@pytest.fixture
def mock_container_registry():
    """Fixture for creating a mock BaseContainerRegistry."""
    return MagicMock(spec=BaseContainerRegistry)

def test_kaniko_image_builder_logging(kaniko_image_builder, mock_build_context, mock_container_registry, caplog):
    """Test that the KanikoImageBuilder logs messages correctly during execution."""
    # Set up test data
    image_name = "test-image:latest"
    docker_build_options = {"arg1": "value1", "arg2": "value2"}

    # Mock external dependencies
    with patch.object(kaniko_image_builder, "_check_prerequisites"), \
         patch.object(kaniko_image_builder, "_generate_pod_name", return_value="kaniko-build-pod"), \
         patch.object(kaniko_image_builder, "_upload_build_context", return_value="tar://stdin"), \
         patch.object(kaniko_image_builder, "_generate_spec_overrides", return_value={"spec": "overrides"}), \
         patch.object(kaniko_image_builder, "_run_kaniko_build"), \
         patch.object(kaniko_image_builder, "_read_pod_output", return_value="test-image@sha256:abc123"), \
         patch.object(kaniko_image_builder, "_verify_image_name"), \
         patch.object(kaniko_image_builder, "_delete_pod"):

        # Execute the build method
        with caplog.at_level(logging.INFO):
            kaniko_image_builder.build(
                image_name=image_name,
                build_context=mock_build_context,
                docker_build_options=docker_build_options,
                container_registry=mock_container_registry,
            )

        # Verify log messages
        logs = caplog.text

        # Check initial logs
        assert "Executing the build functi" in logs
        assert "Building image `test-image:latest` with Kaniko." in logs
        assert "Docker build options: {'arg1': 'value1', 'arg2': 'value2'}" in logs
        assert "Build context: " in logs
        assert "Executing function check_prerequisites" in logs

        # Check logs for pod name generation
        assert "Generating pod name" in logs
        assert "Using Kaniko to build image `test-image:latest` in pod `kaniko-build-pod`." in logs

        # Check logs for spec overrides generation
        assert "Generating spec overrides" in logs
        assert "Kaniko build args: " in logs
        assert "Optional spec args: " in logs

        # Check logs for Kaniko build execution
        assert "Starting Kaniko build for Pod 'kaniko-build-pod'." in logs
        assert "Executing Kaniko build command: " in logs

        # Check logs for reading pod output
        assert "Reading pod output" in logs
        assert "Pod output retrieved successfully: test-image@sha256:abc123" in logs

        # Check logs for image name verification
        assert "Verifying image name" in logs
        assert "Image name and SHA digest verified successfully." in logs

        # Check logs for pod deletion
        assert "Deleting pod" in logs
        assert "Successfully deleted Kaniko build Pod 'kaniko-build-pod'." in logs
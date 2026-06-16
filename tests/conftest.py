"""pytest configuration for the DPD test suite."""
import pytest

from pytest_homeassistant_custom_component.plugins import hass  # noqa: F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make ``custom_components.dpd`` loadable in HA tests."""
    yield

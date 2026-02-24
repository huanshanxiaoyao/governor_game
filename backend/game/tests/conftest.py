"""Shared fixtures for game tests."""

import pytest

from game.services.county import CountyService


@pytest.fixture
def county():
    """Create a fresh county_data dict for testing."""
    return CountyService.create_initial_county()


@pytest.fixture
def county_fiscal(county):
    """Create a fiscal_core county for testing."""
    return CountyService.create_initial_county(county_type="fiscal_core")


@pytest.fixture
def county_coastal(county):
    """Create a coastal county for testing."""
    return CountyService.create_initial_county(county_type="coastal")

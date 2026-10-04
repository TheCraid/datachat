import pytest

from app.config import Settings
from app.datasets import DatasetRegistry


@pytest.fixture(scope="session")
def registry() -> DatasetRegistry:
    reg = DatasetRegistry()
    reg.load_samples()
    return reg


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, groq_api_key="", database_url=f"sqlite:///{tmp_path}/test.db",
                    rate_limit_per_min=1000)

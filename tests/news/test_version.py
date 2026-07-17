import herald
from herald.validator.utils import config as validator_config


def test_release_and_bittensor_spec_versions_are_consistent():
    assert herald.__version__ == "0.1.0"
    assert validator_config.__version__ == herald.__version__
    assert herald.__spec_version__ == 10

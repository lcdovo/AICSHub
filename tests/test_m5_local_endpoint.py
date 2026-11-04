import argparse
import pytest
from scripts.evaluate_m5_local import local_reranker_base


@pytest.mark.parametrize('value', [
    'https://example.test:7999', 'http://example.test:7999',
    'http://user:secret@127.0.0.1:7999', 'http://127.0.0.1:7999/path',
    'http://127.0.0.1:7999?key=x', 'http://127.0.0.1:7999#x',
    'http://127.0.0.1', 'http://127.0.0.1:99999',
])
def test_local_experiment_cannot_redirect_to_external_or_credential_endpoint(value):
    with pytest.raises(argparse.ArgumentTypeError):
        local_reranker_base(value)


def test_local_experiment_endpoint_accepts_explicit_loopback_port():
    assert local_reranker_base('http://127.0.0.1:7999/') == 'http://127.0.0.1:7999'

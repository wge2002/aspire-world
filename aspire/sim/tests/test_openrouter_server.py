from aspire.sim.cap.serving.openrouter_server import _upstream_error_status_code


class _ProviderError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def test_preserves_valid_provider_http_status() -> None:
    assert _upstream_error_status_code(_ProviderError(524)) == 524
    assert _upstream_error_status_code(_ProviderError(429)) == 429


def test_maps_non_http_failures_to_internal_server_error() -> None:
    assert _upstream_error_status_code(RuntimeError("boom")) == 500
    assert _upstream_error_status_code(_ProviderError("524")) == 500
    assert _upstream_error_status_code(_ProviderError(700)) == 500

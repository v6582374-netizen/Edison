"""Composition counts SDK HTTP requests, including failures, without hidden retries."""
import httpx
import pytest
from openai import OpenAI

from coworker.providers.openai_provider import OpenAIProvider
from coworker.providers.openai_responses import OpenAIResponsesProvider


@pytest.mark.parametrize('provider_type', [OpenAIProvider, OpenAIResponsesProvider])
@pytest.mark.parametrize('status', [400, 503])
def test_single_attempt_transport_does_not_retry_sdk_or_parameter_errors(provider_type, status):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': "'max_tokens' is not supported", 'type': 'invalid_request_error'}})
    client = OpenAI(api_key='fixture', base_url='https://provider.invalid/v1',
                    http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    provider = provider_type(client=client)
    failed = False
    try:
        provider.complete_once(
            model='fixture', messages=[{'role': 'user', 'content': 'retained translation'}]
        )
    except BaseException:
        failed = True
    assert failed and len(requests) == 1

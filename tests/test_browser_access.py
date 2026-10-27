import httpx
import pytest
from fastapi.testclient import TestClient

from fm_serve.proxy import PAGE_ASSIST_ORIGIN, create_app


@pytest.mark.parametrize('streaming', [False, True])
def test_page_assist_headers_and_streaming(streaming):
    app = create_app(backend_base='http://backend', enable_keepalive=False)
    seen = []

    def backend(request):
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'data: hello\n\n' if streaming else b'{"ok":true}'))

    with TestClient(app) as client:
        client.portal.call(app.state.http.aclose)
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(backend), base_url='http://backend')
        response = client.post('/v1/chat/completions?test=1', json={'stream': streaming}, headers={
            'Origin': PAGE_ASSIST_ORIGIN,
            'Referer': PAGE_ASSIST_ORIGIN + '/index.html',
            'Sec-Fetch-Site': 'cross-site',
            'Authorization': 'Bearer dummy',
        })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == PAGE_ASSIST_ORIGIN
    assert response.content == (b'data: hello\n\n' if streaming else b'{"ok":true}')
    assert seen[0].url.query == b'test=1'
    for header in ('origin', 'referer', 'sec-fetch-site', 'authorization'):
        assert header not in seen[0].headers


def test_preflight_and_untrusted_origins():
    app = create_app(backend_base='http://127.0.0.1:9', enable_keepalive=False)
    with TestClient(app) as client:
        response = client.options('/v1/chat/completions', headers={
            'Origin': PAGE_ASSIST_ORIGIN,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type,authorization',
        })
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == PAGE_ASSIST_ORIGIN
        for origin in ('https://example.com', 'null', PAGE_ASSIST_ORIGIN + '.evil'):
            response = client.post('/v1/chat/completions', headers={'Origin': origin})
            assert response.status_code == 403
            assert 'access-control-allow-origin' not in response.headers
        assert client.get('/v1/models').status_code == 502

"""Exercise Hermes's real OAuth discovery flow without network or credentials.

Run with the Python environment from the pinned Hermes image. Set
HERMES_OAUTH_PROVIDER_FILE to test a generated provider before mounting it.
"""

import json
import logging
import os
from pathlib import Path
import runpy
import unittest
from urllib.parse import urlsplit

import httpx2
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata


class AuthorizationReached(Exception):
    """Stop before opening a browser or exchanging any credentials."""


class MemoryStorage:
    async def get_tokens(self):
        return None

    async def set_tokens(self, tokens):
        raise AssertionError("This discovery test must not exchange tokens")

    async def get_client_info(self):
        return None

    async def set_client_info(self, client_info):
        self.client_info = client_info


class TickTickOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def discover(
        self,
        *,
        server_url="https://mcp.ticktick.com",
        advertised_issuer="https://ticktick.com/",
        metadata_issuer="https://ticktick.com",
        response_url=None,
        response_method="GET",
        redirected=False,
    ):
        source = Path(os.environ.get(
            "HERMES_OAUTH_PROVIDER_FILE", "/opt/hermes/tools/mcp_oauth_provider.py"
        ))
        mixin = runpy.run_path(str(source))["HermesProviderMixin"]
        provider_type = type("FixtureOAuthProvider", (mixin, OAuthClientProvider), {})

        async def redirect(url):
            self.assertEqual(urlsplit(url).hostname, urlsplit(metadata_issuer).hostname)
            raise AuthorizationReached

        async def callback():
            self.fail("This discovery test must stop before the browser callback")

        provider = provider_type(
            server_url=server_url,
            client_metadata=OAuthClientMetadata(
                redirect_uris=["http://127.0.0.1:27890/callback"],
                token_endpoint_auth_method="none",
            ),
            storage=MemoryStorage(),
            redirect_handler=redirect,
            callback_handler=callback,
        )
        issuer = urlsplit(advertised_issuer)
        discovery_url = (
            f"{issuer.scheme}://{issuer.netloc}/.well-known/oauth-authorization-server"
            f"{issuer.path.rstrip('/')}"
        )
        resource_metadata_url = f"{server_url.rstrip('/')}/.well-known/oauth-protected-resource"
        metadata = {
            "issuer": metadata_issuer,
            "authorization_endpoint": f"{metadata_issuer.rstrip('/')}/oauth/authorize",
            "token_endpoint": "https://ticktick.com/oauth/token",
            "registration_endpoint": "https://ticktick.com/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
        }
        flow = provider.async_auth_flow(httpx2.Request("POST", server_url))
        try:
            request = await anext(flow)
            for _ in range(10):
                url = str(request.url)
                if url == server_url:
                    response = httpx2.Response(401, request=request, headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url}"'
                    })
                elif url == resource_metadata_url:
                    response = httpx2.Response(200, request=request, json={
                        "resource": server_url,
                        "authorization_servers": [advertised_issuer],
                    })
                elif url == discovery_url:
                    response_request = httpx2.Request(response_method, response_url or url)
                    history = [httpx2.Response(302, request=request)] if redirected else []
                    response = httpx2.Response(
                        200, request=response_request, json=metadata, history=history
                    )
                elif url == "https://ticktick.com/oauth/register":
                    registration = json.loads(request.content)
                    registration["client_id"] = "fixture-client"
                    response = httpx2.Response(201, request=request, json=registration)
                else:
                    self.fail(f"Unexpected OAuth request: {url}")
                request = await flow.asend(response)
            self.fail("OAuth discovery did not reach browser authorization")
        finally:
            await flow.aclose()

    async def test_ticktick_root_slash_mismatch_reaches_authorization(self):
        with self.assertRaises(AuthorizationReached):
            await self.discover()

    async def test_ticktick_endpoint_with_root_slash_reaches_authorization(self):
        with self.assertRaises(AuthorizationReached):
            await self.discover(server_url="https://mcp.ticktick.com/")

    async def test_exact_issuer_match_still_reaches_authorization(self):
        with self.assertRaises(AuthorizationReached):
            await self.discover(advertised_issuer="https://ticktick.com")

    async def test_mismatch_for_another_mcp_server_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(server_url="https://tasks.example.com/mcp")

    async def test_mismatch_for_a_lookalike_mcp_host_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(server_url="https://mcp.ticktick.com.example.com")

    async def test_different_metadata_issuer_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(metadata_issuer="https://other.example.com")

    async def test_different_issuer_path_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(metadata_issuer="https://ticktick.com/other")

    async def test_different_advertised_issuer_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(advertised_issuer="https://other.example.com/")

    async def test_reverse_slash_mismatch_is_not_relaxed(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(
                advertised_issuer="https://ticktick.com",
                metadata_issuer="https://ticktick.com/",
            )

    async def test_redirected_metadata_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(redirected=True)

    async def test_other_metadata_location_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(response_url="https://ticktick.com/.well-known/openid-configuration")

    async def test_non_get_metadata_response_is_rejected(self):
        with self.assertRaisesRegex(OAuthFlowError, "metadata issuer mismatch"):
            await self.discover(response_method="POST")


if __name__ == "__main__":
    # The SDK logs expected fixture failures; unittest still reports unexpected exceptions.
    logging.getLogger("mcp.client.auth.oauth2").setLevel(logging.CRITICAL)
    unittest.main()

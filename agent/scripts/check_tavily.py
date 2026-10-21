"""One public search to verify local config and Tavily, without printing secrets."""
from dataclasses import replace

import httpx

from agent.gateway import DashScopeGateway, Settings


class CheckedClient(httpx.Client):
    def post(self, *args, **kwargs):
        try:
            response = super().post(*args, **kwargs)
        except httpx.HTTPError as exc:
            print('Transport error:', type(exc).__name__)
            raise
        print('Search HTTP status:', response.status_code)
        return response


def main():
    config = Settings.from_env()
    print('Configured provider:', config.search_provider)
    print('Tavily key configured:', bool(config.tavily_api_key))
    print('Search enabled:', config.enable_search)
    if not config.tavily_api_key:
        raise SystemExit(1)
    with CheckedClient(timeout=30) as client:
        gateway = DashScopeGateway(replace(config, search_provider='tavily', enable_search=True), client)
        result = gateway.search('Python official documentation list comprehension')
    print('Usable sources:', len(result.sources) if result else 0)
    if not result:
        raise SystemExit(1)
    print('Search adapter: OK')


if __name__ == '__main__':
    main()

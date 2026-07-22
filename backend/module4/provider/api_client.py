"""
Module 4.3

Central HTTP Client

Every provider (NSE, BSE, SEBI, FMP)
uses this client instead of requests directly.
"""

import requests


class APIClient:

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()

    def get(self, url, params=None, headers=None):

        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()

    def close(self):
        self.session.close()


api_client = APIClient()

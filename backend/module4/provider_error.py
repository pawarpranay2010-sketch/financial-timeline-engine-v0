"""
Provider Exceptions
"""


class ProviderError(Exception):
    pass


class ProviderConnectionError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderDataError(ProviderError):
    pass

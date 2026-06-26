"""
Retry utility with exponential backoff for API calls.

Ported verbatim from the vault's _assets/code/common/retry_utils.py — the single
canonical retry helper for every bio_toolkit client.

Usage:
    from bio_toolkit.util.retry import retry_on_failure

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def fetch_gene_data(symbol: str) -> dict:
        response = requests.get(f"https://api.example.com/{symbol}")
        response.raise_for_status()
        return response.json()
"""

import functools
import logging
import time
from typing import Callable, Optional, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Exception types that should trigger a retry (transient errors)
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[BaseException], ...] = ()

try:
    import requests
    DEFAULT_RETRYABLE_EXCEPTIONS = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )
except ImportError:
    pass


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Optional[Tuple[Type[BaseException], ...]] = None,
    retry_on_status_codes: Tuple[int, ...] = (429, 500, 502, 503, 504),
) -> Callable:
    """
    Decorator that retries a function on transient failures with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier applied to delay after each retry.
        retryable_exceptions: Tuple of exception types that trigger retry.
            Defaults to requests connection/timeout errors.
        retry_on_status_codes: HTTP status codes that trigger retry
            (only applies if the result has a status_code attribute).
    """
    if retryable_exceptions is None:
        retryable_exceptions = DEFAULT_RETRYABLE_EXCEPTIONS

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            delay = base_delay

            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)

                    # Check for retryable HTTP status codes on requests.Response
                    if hasattr(result, 'status_code') and result.status_code in retry_on_status_codes:
                        if attempt < max_retries:
                            logger.warning(
                                "%s returned HTTP %d, retrying in %.1fs (attempt %d/%d)",
                                func.__name__, result.status_code, delay, attempt + 1, max_retries
                            )
                            time.sleep(delay)
                            delay = min(delay * backoff_factor, max_delay)
                            continue
                    return result

                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            "%s failed with %s, retrying in %.1fs (attempt %d/%d)",
                            func.__name__, type(e).__name__, delay, attempt + 1, max_retries
                        )
                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_retries + 1, e
                        )
                        raise

            raise last_exception  # Should not reach here, but safety net

        return wrapper
    return decorator

"""Tests for ISSClient error handling."""

import dataclasses

import pytest
import requests

from iss_display.config import Settings
from iss_display.data.iss_client import ISSClient, ISSFetchError


class FailingSession:
    """Raises the way requests does, with the full URL in the message."""

    def get(self, url, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    def close(self):
        pass


def test_fetch_errors_do_not_leak_n2yo_key():
    settings = dataclasses.replace(Settings.load(), n2yo_api_key="SECRETKEY123")
    client = ISSClient(settings)
    client._session = FailingSession()

    with pytest.raises(ISSFetchError) as exc:
        client.get_fix()

    assert "SECRETKEY123" not in str(exc.value)
    assert "n2yo" in str(exc.value)

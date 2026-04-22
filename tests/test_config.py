"""Tests for Settings — just the proxy URL assembly logic.

The rest of Settings is pydantic-settings boilerplate; we don't unit-test library code.
"""

from new_amazon_scraper.config import Settings


def test_proxy_url_is_none_when_username_missing():
    s = Settings(thordata_username="", thordata_password="p", thordata_proxy_server="h:1")
    assert s.thordata_proxy_url is None


def test_proxy_url_is_none_when_server_missing():
    s = Settings(thordata_username="u", thordata_password="p", thordata_proxy_server="")
    assert s.thordata_proxy_url is None


def test_proxy_url_assembled_when_all_fields_set():
    s = Settings(thordata_username="u", thordata_password="p", thordata_proxy_server="h:1")
    assert s.thordata_proxy_url == "http://u:p@h:1"

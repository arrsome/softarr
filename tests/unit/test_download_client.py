"""Tests for the AbstractDownloadClient interface and SABnzbd implementation."""

import pytest


def test_sabnzbd_implements_abstract_interface():
    """SABnzbdClient must implement all abstract methods from AbstractDownloadClient."""
    import inspect

    from softarr.integrations.download_client import AbstractDownloadClient
    from softarr.integrations.sabnzbd import SABnzbdClient

    # Verify SABnzbdClient is a subclass
    assert issubclass(SABnzbdClient, AbstractDownloadClient)

    # Verify all abstract methods are implemented (no remaining abstract methods)
    abstract_methods = {
        name
        for name, method in inspect.getmembers(
            AbstractDownloadClient, predicate=inspect.isfunction
        )
        if getattr(method, "__isabstractmethod__", False)
    }

    for method_name in abstract_methods:
        assert hasattr(SABnzbdClient, method_name), (
            f"SABnzbdClient is missing abstract method: {method_name}"
        )
        # The method should not be abstract on SABnzbdClient
        method = getattr(SABnzbdClient, method_name)
        assert not getattr(method, "__isabstractmethod__", False), (
            f"SABnzbdClient.{method_name} is still abstract"
        )


def test_abstract_cannot_be_instantiated():
    """AbstractDownloadClient should raise TypeError if instantiated directly."""
    from softarr.integrations.download_client import AbstractDownloadClient

    with pytest.raises(TypeError):
        AbstractDownloadClient()


def test_download_client_error_is_exception():
    """DownloadClientError should be a subclass of Exception."""
    from softarr.integrations.download_client import DownloadClientError

    assert issubclass(DownloadClientError, Exception)


def test_sabnzbd_error_extends_download_client_error():
    """SABnzbdError should extend DownloadClientError."""
    from softarr.integrations.download_client import DownloadClientError
    from softarr.integrations.sabnzbd import SABnzbdError

    assert issubclass(SABnzbdError, DownloadClientError)


def test_send_nzb_content_alias():
    """send_nzb_content should be an alias for send_file (backwards compat)."""
    from softarr.integrations.sabnzbd import SABnzbdClient

    # Both methods should exist
    assert hasattr(SABnzbdClient, "send_nzb_content")
    assert hasattr(SABnzbdClient, "send_file")

from ssl import SSLContext
from unittest.mock import MagicMock

from pytest import MonkeyPatch

from relay.mail import connection


def test_imap_connection_validates_tls_and_opens_folder_read_only(monkeypatch: MonkeyPatch) -> None:
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.select.return_value = ("OK", [b"0"])
    monkeypatch.setattr(connection.imaplib, "IMAP4_SSL", factory)

    assert (
        connection.check_connection("edi@example.com", "imap.263.net", "交付计划", "demo-code")
        == "ok"
    )
    assert factory.call_args.args == ("imap.263.net", 993)
    context = factory.call_args.kwargs["ssl_context"]
    assert isinstance(context, SSLContext) and context.check_hostname
    client.login.assert_called_once_with("edi@example.com", "demo-code")
    client.select.assert_called_once_with('"&TqRO2IuhUhI-"', readonly=True)
    assert [call[0] for call in client.method_calls] == ["login", "select"]

"""CLI bind addresses must produce a usable backend URL."""

from unittest.mock import patch

import pytest

from s21_compass.cli import main


@pytest.mark.parametrize("host", ["127.0.0.2", "0.0.0.0", "localhost", "::ffff:127.0.0.1"])
def test_cli_rejects_unsupported_bind_address(host):
    with (
        patch("sys.argv", ["s21-compass", "web", "--host", host]),
        pytest.raises(SystemExit) as error,
    ):
        main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "host,normalized,url",
    [("127.0.0.1", "127.0.0.1", "http://127.0.0.1:8210"),
     ("0:0:0:0:0:0:0:1", "::1", "http://[::1]:8210")],
)
def test_cli_normalizes_supported_address(host, normalized, url, capsys):
    with patch("sys.argv", ["s21-compass", "web", "--host", host]), patch(
        "uvicorn.run"
    ) as run:
        main()
    assert run.call_args.kwargs["host"] == normalized
    assert run.call_args.kwargs["proxy_headers"] is False
    assert url in capsys.readouterr().out

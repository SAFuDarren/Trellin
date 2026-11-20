# SPDX-License-Identifier: Apache-2.0

from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from research_multiplier.workflows.setup import _dsn


pytestmark = pytest.mark.offline


def test_socket_directory_and_port_are_connection_parameters():
    directory = "/tmp/research 100% socket"
    url = urlsplit(_dsn("project owner", "", directory, "5544", "project database"))

    assert url.hostname is None
    assert url.port is None
    assert unquote(url.username) == "project owner"
    assert unquote(url.path) == "/project database"
    assert "%20" in url.query
    assert parse_qs(url.query) == {"host": [directory], "port": ["5544"]}


@pytest.mark.parametrize(
    ("host", "expected_hostname"),
    [("127.0.0.1", "127.0.0.1"), ("::1", "::1"), (None, "localhost")],
)
def test_tcp_host_and_port_are_in_the_authority(host, expected_hostname):
    url = urlsplit(_dsn("owner", "", host, "5544", "project"))

    assert url.hostname == expected_hostname
    assert url.port == 5544
    assert not url.query

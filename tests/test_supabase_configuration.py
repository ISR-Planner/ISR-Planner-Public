"""Configuration diagnostics with isolated fake credentials and no SDK/network calls."""

import builtins
import sys
from types import SimpleNamespace

import pytest

from server.database import supabase_client


@pytest.fixture
def configuration(monkeypatch):
    # Never load .env or copy developer credentials into this fixture.
    monkeypatch.setenv("SUPABASE_URL", "https://configuration.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "synthetic-configuration-value")
    monkeypatch.setattr(supabase_client, "_supabase_client", None)
    calls = []
    client = object()

    def create_client(url, key):
        calls.append((url, key))
        return client

    monkeypatch.setitem(
        sys.modules, "supabase", SimpleNamespace(create_client=create_client)
    )
    return calls, client


def assert_safe_output(capsys):
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "configuration.invalid" not in output
    assert "synthetic-configuration-value" not in output
    return output


@pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_KEY"])
def test_missing_setting_skips_client_creation(
    configuration, monkeypatch, capsys, missing
):
    calls, _ = configuration
    monkeypatch.delenv(missing)
    assert supabase_client.get_supabase_client() is None
    assert calls == []
    assert "Missing env vars" in assert_safe_output(capsys)


def test_successful_client_is_reused(configuration, capsys):
    calls, client = configuration
    assert supabase_client.get_supabase_client() is client
    assert supabase_client.get_supabase_client() is client
    assert len(calls) == 1
    assert "Client created successfully" in assert_safe_output(capsys)


@pytest.mark.parametrize("error_type", [ImportError, RuntimeError])
def test_configuration_failures_do_not_echo_sdk_error_text(
    configuration, monkeypatch, capsys, error_type
):
    _, client = configuration
    attempts = 0

    def create_client(url, key):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error_type(f"SDK rejected {url} with credential {key}")
        return client

    monkeypatch.setitem(
        sys.modules, "supabase", SimpleNamespace(create_client=create_client)
    )
    assert supabase_client.get_supabase_client() is None
    assert supabase_client._supabase_client is None
    output = assert_safe_output(capsys)
    assert "SDK rejected" not in output
    assert (
        "ImportError" if error_type is ImportError else "Check SUPABASE_URL"
    ) in output
    # A failure is not cached: the existing retry-on-next-call behavior remains.
    assert supabase_client.get_supabase_client() is client
    assert attempts == 2
    assert_safe_output(capsys)


def test_missing_sdk_reports_install_hint_without_import_exception(
    configuration, monkeypatch, capsys
):
    original_import = builtins.__import__

    def isolated_import(name, *args, **kwargs):
        if name == "supabase":
            raise ImportError("synthetic-configuration-value")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", isolated_import)
    assert supabase_client.get_supabase_client() is None
    assert "pip install supabase" in assert_safe_output(capsys)


def test_required_client_still_raises_when_configuration_is_missing(
    configuration, monkeypatch, capsys
):
    monkeypatch.delenv("SUPABASE_KEY")
    with pytest.raises(RuntimeError, match="Supabase client not configured"):
        supabase_client.require_supabase_client()
    assert_safe_output(capsys)

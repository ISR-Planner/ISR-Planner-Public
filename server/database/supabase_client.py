"""
Supabase Client Module

Provides a singleton Supabase client for database operations.
"""

import os

# Supabase client instance
_supabase_client = None


def get_supabase_client():
    """
    Get the Supabase client instance.

    Returns:
        Supabase client or None if not configured
    """
    global _supabase_client

    if _supabase_client is not None:
        return _supabase_client

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")  # must be service_role key

    # Supabase's newer API keys are prefixed: sb_secret_* (service_role, full access,
    # bypasses RLS — what the server needs) vs sb_publishable_* (client, RLS-bound).
    # The legacy format was a long JWT with no prefix. Only warn on a genuinely
    # publishable key; sb_secret_* is correct.
    if supabase_key and supabase_key.startswith("sb_publishable_"):
        print(
            "[supabase_client] WARNING: Using a PUBLISHABLE key; server capture needs "
            "the service_role (sb_secret_*) key to bypass RLS."
        )

    if not supabase_url or not supabase_key:
        print(
            f"[supabase_client] Missing env vars - SUPABASE_URL: {'set' if supabase_url else 'NOT SET'}, SUPABASE_KEY: {'set' if supabase_key else 'NOT SET'}"  # noqa: E501
        )
        return None

    print("[supabase_client] Env vars found, attempting to create client...")

    try:
        from supabase import create_client

        print("[supabase_client] supabase package imported successfully")
        _supabase_client = create_client(supabase_url, supabase_key)
        print("[supabase_client] Client created successfully")
        return _supabase_client
    except ImportError:
        print(
            "[supabase_client] ImportError: supabase package not installed. "
            "Run: pip install supabase."
        )
        return None
    except Exception:
        # SDK exception text can contain the configured URL or credential.
        print(
            "[supabase_client] Error creating Supabase client. "
            "Check SUPABASE_URL, SUPABASE_KEY, and service availability."
        )
        return None


def is_supabase_configured() -> bool:
    """Check if Supabase is properly configured."""
    return get_supabase_client() is not None


def require_supabase_client():
    """
    Like get_supabase_client(), but raises if Supabase is not configured.
    Use this for executive-critical paths where running without logging is unacceptable.
    """
    client = get_supabase_client()
    if client is None:
        raise RuntimeError(
            "Supabase client not configured. Ensure SUPABASE_URL and SUPABASE_KEY (service_role) are set."
        )
    return client

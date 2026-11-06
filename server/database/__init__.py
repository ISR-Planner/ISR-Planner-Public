"""
Database module for ISR Mission Planner.

Provides Supabase client and database operations for:
- Agent memories
- Session management
- Conversation history
- Mission plan storage
"""

from .session_storage import (  # Session management; Conversation management; Mission plans
    add_conversation_message,
    clear_conversation,
    create_session,
    delete_session,
    format_messages_for_prompt,
    get_conversation_history,
    get_conversation_summary,
    get_mission_plans,
    get_recent_messages,
    get_session,
    save_mission_plan,
    update_session_metadata,
)
from .supabase_client import get_supabase_client, is_supabase_configured

__all__ = [
    # Client
    "get_supabase_client",
    "is_supabase_configured",
    # Sessions
    "create_session",
    "get_session",
    "update_session_metadata",
    "delete_session",
    # Conversations
    "add_conversation_message",
    "get_conversation_history",
    "get_recent_messages",
    "get_conversation_summary",
    "clear_conversation",
    "format_messages_for_prompt",
    # Mission plans
    "save_mission_plan",
    "get_mission_plans",
]

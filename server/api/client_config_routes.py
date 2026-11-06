import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/client_config")
def api_client_config():
    """
    Non-secret-ish config the frontend needs at load time. Keeps API keys out
    of shipped JS/HTML (matching how other provider keys stay server-side) —
    the token itself still reaches the browser over the wire since Cesium
    needs it client-side to fetch imagery/terrain, but it never lives in a
    static file that could be cached, indexed, or committed by accident.
    """
    return {
        "cesium_ion_token": os.environ.get("CESIUM_ION_TOKEN", ""),
    }

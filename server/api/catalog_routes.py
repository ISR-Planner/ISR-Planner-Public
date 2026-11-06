from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.asset_catalog import CatalogError, drone_fields, load_catalog

router = APIRouter()


@router.get("/api/asset_catalog")
def api_asset_catalog():
    """The platform + sensor catalog (catalog/asset_catalog.json), checked."""
    try:
        return load_catalog()
    except (OSError, ValueError, CatalogError) as e:
        raise HTTPException(status_code=500, detail=f"asset catalog: {e}")


class DroneFieldsRequest(BaseModel):
    platform_id: str
    sensor_ids: List[str] = []
    # The mission's target category ids (default: the catalog's).
    categories: Optional[List[str]] = None


@router.post("/api/asset_catalog/drone_fields")
def api_drone_fields(req: DroneFieldsRequest):
    """A drone's settings for a platform carrying these sensors (the one rule:
    server/asset_catalog.py drone_fields), in the planner's units."""
    try:
        return drone_fields(
            load_catalog(), req.platform_id, req.sensor_ids, req.categories
        )
    except CatalogError as e:
        raise HTTPException(status_code=400, detail=str(e))

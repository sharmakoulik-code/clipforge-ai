from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import User, UserPreference

router = APIRouter(prefix="/api/auth", tags=["user preferences"])


class PreferencesUpdate(BaseModel):
    theme: str = "system"
    email_notifications: bool = True
    processing_notifications: bool = True

    def normalized(self) -> dict[str, Any]:
        if self.theme not in {"system", "light", "dark"}:
            raise HTTPException(status_code=422, detail="Theme must be system, light, or dark.")
        return self.model_dump()


@router.get("/preferences")
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.query(UserPreference).filter_by(user_id=user.id).first()
    return {"theme": "system", "email_notifications": True, "processing_notifications": True, **(row.preferences if row else {})}


@router.put("/preferences")
def update_preferences(payload: PreferencesUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    values = payload.normalized()
    row = db.query(UserPreference).filter_by(user_id=user.id).first()
    if row:
        row.preferences = values
    else:
        db.add(UserPreference(user_id=user.id, preferences=values))
    db.commit()
    return values

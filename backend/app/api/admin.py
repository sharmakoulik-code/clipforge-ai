from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.models.models import User

router = APIRouter(prefix="/api/admin", tags=["administration"])


@router.get("/health")
def admin_health(user: User = Depends(require_admin)) -> dict[str, str]:
    return {"status": "ok", "role": "admin", "user_id": user.id}

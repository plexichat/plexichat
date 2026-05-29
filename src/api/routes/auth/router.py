from fastapi import APIRouter

from .register import router as register_router
from .login import router as login_router
from .oauth import router as oauth_router
from .two_factor import router as two_factor_router
from .sessions import router as sessions_router
from .password import router as password_router
from .passkeys import router as passkeys_router

router = APIRouter(tags=["Authentication"])

router.include_router(register_router)
router.include_router(login_router)
router.include_router(oauth_router)
router.include_router(two_factor_router)
router.include_router(sessions_router)
router.include_router(password_router)
router.include_router(passkeys_router)

__all__ = ["router"]

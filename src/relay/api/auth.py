import os
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr

from relay.auth import accounts
from relay.auth.accounts import ChangeUser, NewUser, User

_COOKIE = "relay_session"
router = APIRouter(prefix="/api/v1", tags=["authentication"])


def require_browser_request(x_relay_request: Annotated[str | None, Header()] = None) -> None:
    # A cross-origin form cannot set this header. CORS stays disabled for these routes.
    if x_relay_request != "1":
        raise HTTPException(403, detail="invalid_request_origin")


def current_user(response: Response, relay_session: Annotated[str | None, Cookie()] = None) -> User:
    user = accounts.session_user(relay_session) if relay_session else None
    if user is None:
        raise HTTPException(401, detail="login_required")
    response.headers["Cache-Control"] = "no-store"
    return user


SessionUser = Annotated[User, Depends(current_user)]


def verified_user(user: SessionUser) -> User:
    if user.must_change_password:
        raise HTTPException(403, detail="password_change_required")
    return user


SignedInUser = Annotated[User, Depends(verified_user)]


def admin_user(user: SignedInUser) -> User:
    if user.role != "admin":
        raise HTTPException(403, detail="admin_required")
    return user


AdminUser = Annotated[User, Depends(admin_user)]
_BROWSER = [Depends(require_browser_request)]


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class ChangePassword(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)


class ResetPassword(BaseModel):
    password: SecretStr = Field(min_length=8, max_length=128)


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(
        _COOKIE,
        token,
        max_age=accounts.SESSION_SECONDS,
        httponly=True,
        secure=os.environ.get("RELAY_COOKIE_SECURE", "false").lower() == "true",
        samesite="strict",
    )


@router.post("/auth/login", dependencies=_BROWSER)
def sign_in(credentials: Credentials, response: Response) -> User:
    result = accounts.login(credentials.username, credentials.password.get_secret_value())
    if result is None:
        raise HTTPException(401, detail="invalid_login")
    user, token = result
    response.headers["Cache-Control"] = "no-store"
    _set_session(response, token)
    return user


@router.get("/auth/me")
def who_am_i(user: SessionUser) -> User:
    return user


@router.post("/auth/logout", status_code=204, dependencies=_BROWSER)
def sign_out(response: Response, relay_session: Annotated[str | None, Cookie()] = None) -> None:
    if relay_session:
        accounts.revoke_session(relay_session)
    response.delete_cookie(_COOKIE, httponly=True, samesite="strict")


@router.post("/auth/password", dependencies=_BROWSER)
def change_password(values: ChangePassword, user: SessionUser, response: Response) -> User:
    try:
        token = accounts.update_own_password(
            user, values.current_password.get_secret_value(), values.new_password.get_secret_value()
        )
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from None
    if token is None:
        raise HTTPException(400, detail="current_password_invalid")
    _set_session(response, token)
    return user.model_copy(update={"must_change_password": False})


@router.get("/users")
def users(_: AdminUser) -> list[User]:
    return accounts.list_users()


@router.post("/users", status_code=201, dependencies=_BROWSER)
def add_user(values: NewUser, _: AdminUser) -> User:
    user = accounts.create_user(values)
    if user is None:
        raise HTTPException(409, detail="username_exists")
    return user


@router.patch("/users/{user_id}", dependencies=_BROWSER)
def edit_user(user_id: int, values: ChangeUser, actor: AdminUser) -> User:
    try:
        user = accounts.update_user(actor, user_id, values)
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from None
    if user is None:
        raise HTTPException(404, detail="user_not_found")
    return user


@router.delete("/users/{user_id}", status_code=204, dependencies=_BROWSER)
def remove_user(user_id: int, actor: AdminUser) -> None:
    try:
        deleted = accounts.delete_user(actor, user_id)
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from None
    if not deleted:
        raise HTTPException(404, detail="user_not_found")


@router.post("/users/{user_id}/password", status_code=204, dependencies=_BROWSER)
def reset_password(user_id: int, values: ResetPassword, actor: AdminUser) -> None:
    try:
        updated = accounts.reset_password(actor, user_id, values.password.get_secret_value())
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from None
    if not updated:
        raise HTTPException(404, detail="user_not_found")

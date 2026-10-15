"""Authenticate once, then route to an entirely separate runtime/database per user."""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import sqlite3
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request as ASGIRequest

from ..accounts import AccountStore, SESSION_TTL, user_directory
from ..config import Settings
from ..models_config import apply_overrides
from ..channels.feishu import FeishuHub
from .app import VERSION, _create_tenant_app

COOKIE = "memoria_session"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)


class NewPassword(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=12, max_length=128)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


def session_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header:
        return header[7:] if header.lower().startswith("bearer ") else ""
    return request.cookies.get(COOKIE, "")


class TenantPool:
    def __init__(self, settings: Settings, root: Path):
        self.settings, self.root = settings, root
        self.apps: dict[str, FastAPI] = {}
        self.lock = asyncio.Lock()
        self.stack = AsyncExitStack()

    async def get(self, user: dict) -> FastAPI:
        async with self.lock:
            if user["id"] in self.apps:
                return self.apps[user["id"]]
            if len(self.apps) >= 32:
                raise HTTPException(503, "当前用户运行时容量已满，请稍后重试")
            directory = user_directory(self.root, user["id"])
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            settings = copy.deepcopy(self.settings)
            settings.database = directory / "memory/memoria.db"
            settings.markdown_directory = directory / "markdown"
            settings.mcp_config_file = directory / "config/mcp_servers.json"
            settings.model_override_file = directory / "config/models.override.toml"
            settings.mcp_enabled = settings.mcp_config_file.is_file()
            settings.api_token = ""  # Login sessions are authenticated by this gateway.
            settings.drift_enabled = False
            apply_overrides(settings)
            app = _create_tenant_app(tenant_settings=settings, tenant_user_id=user["id"])
            try:
                await self.stack.enter_async_context(app.router.lifespan_context(app))
            except BaseException:
                app.state.store.close()
                raise
            self.apps[user["id"]] = app
            return app

    async def close(self):
        await self.stack.aclose()
        self.apps.clear()


class PrivateDispatch:
    def __init__(self, accounts: AccountStore, pool: TenantPool):
        self.accounts, self.pool = accounts, pool

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        request = ASGIRequest(scope, receive=receive)
        user = self.accounts.authenticate(session_token(request))
        if not user:
            await JSONResponse({"code": "unauthorized", "message": "请先注册或登录"}, status_code=401)(scope, receive, send)
            return
        # Global/private integration configuration is never exposed by the gateway.
        # Each service reads only its own MCP config and database; agent keys cannot bypass login.
        scope.setdefault("state", {})["user"] = user
        # Server model credentials and local-process paths are administrator configuration.
        # A public signup must not redirect shared credentials to an arbitrary endpoint.
        if scope.get("path") == "/api/settings/models" and scope.get("method") == "PUT":
            await JSONResponse({"message": "模型配置由本机管理员维护"}, status_code=403)(scope, receive, send)
            return
        try:
            app = await self.pool.get(user)
        except HTTPException as exc:
            await JSONResponse({"message": exc.detail}, status_code=exc.status_code)(scope, receive, send)
            return
        await app(scope, receive, send)


def create_app(config_path: str | Path | None = None) -> FastAPI:
    settings = Settings.load(config_path)
    root = settings.database.parent.resolve()
    accounts = AccountStore(root / "auth/accounts.db")
    pool = TenantPool(settings, root)
    password_slots = asyncio.Semaphore(2)
    feishu = FeishuHub(root, accounts, pool)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await feishu.start()
            yield
        finally:
            await feishu.close()
            await pool.close()
            accounts.close()

    app = FastAPI(title="Memoria authenticated memory API", version=VERSION, lifespan=lifespan)
    app.state.accounts, app.state.tenants, app.state.data_root = accounts, pool, root
    app.state.feishu = feishu
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Content-Type", "Authorization", "X-Agent-Key", "X-Request-ID"], allow_credentials=True)

    @app.middleware("http")
    async def privacy_boundary(request, call_next):
        peer = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local = peer == "testclient"
        private_path = (request.url.path.startswith("/api/") and request.url.path != "/api/health") or request.url.path == "/metrics"
        if private_path and request.url.scheme != "https" and not local:
            return JSONResponse({"message": "远程访问需要 HTTPS"}, status_code=426)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/") and origin.rstrip("/") not in settings.allowed_origins:
                return JSONResponse({"message": "Origin 不在允许列表"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"message": "跨站请求被拒绝"}, status_code=403)
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) < 0:
                    raise ValueError("negative length")
                if int(length) > settings.max_request_bytes:
                    return JSONResponse({"message": "请求体过大"}, status_code=413)
            except ValueError:
                return JSONResponse({"message": "Content-Length 无效"}, status_code=400)
        # Bound chunked requests too; Content-Length alone is not a size limit.
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > settings.max_request_bytes:
                return JSONResponse({"message": "请求体过大"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.middleware("http")
    async def response_privacy_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("X-Request-ID", uuid.uuid4().hex)
        identity=getattr(request.state,'user',None)
        if identity:response.headers['X-Memoria-User']=identity['id']
        if request.url.path == '/' or request.url.path.startswith(("/studio/", "/assets/")):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        return JSONResponse({"message": "请求字段不合法；用户名及密码长度需符合说明"}, status_code=422)

    def current_user(request):
        user = accounts.authenticate(session_token(request))
        if not user:
            raise HTTPException(401, "请先登录")
        request.state.user=user
        return user

    def current_superadmin(request):
        user=current_user(request)
        if user['role']!='superadmin':
            raise HTTPException(403,'需要超级管理员权限')
        return user

    def check_rate(request, username):
        peer = request.client.host if request.client else "unknown"
        if not accounts.throttle(f"ip:{peer}", maximum=30) or not accounts.throttle(f"name:{username.strip().lower()}"):
            raise HTTPException(429, "登录/注册尝试过多，10 分钟后重试")

    @app.post("/api/auth/register", status_code=201, tags=["auth"])
    async def register(body: Credentials, request: Request):
        check_rate(request, body.username)
        try:
            async with password_slots:
                return await asyncio.to_thread(accounts.register, body.username, body.password)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该用户名不可注册")
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/auth/login", tags=["auth"])
    async def login(body: Credentials, request: Request, response: Response):
        check_rate(request, body.username)
        try:
            async with password_slots:
                result = await asyncio.to_thread(accounts.login, body.username, body.password)
        except ValueError:
            result = None
        if result is None:
            raise HTTPException(401, "用户名或密码错误")
        user, token = result
        response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict", path="/")
        return {"user": user, "access_token": token, "token_type": "bearer", "expires_in": SESSION_TTL}

    @app.get("/api/auth/me", tags=["auth"])
    def me(request: Request):
        return current_user(request)

    @app.post("/api/auth/logout", status_code=204, tags=["auth"])
    def logout(request: Request, response: Response):
        accounts.logout(session_token(request))
        response.delete_cookie(COOKIE, path="/")

    @app.post('/api/auth/password', status_code=204, tags=['auth'])
    async def change_password(body: PasswordChange, request: Request, response: Response):
        actor = current_user(request)
        peer = request.client.host if request.client else 'unknown'
        if not accounts.throttle(f'password-change:ip:{peer}', maximum=30) or not accounts.throttle(f'password-change:user:{actor["id"]}'):
            raise HTTPException(429, '密码修改尝试过多，10分钟后重试')
        try:
            async with password_slots:
                await asyncio.to_thread(accounts.change_password, session_token(request), body.current_password, body.new_password)
        except PermissionError:
            raise HTTPException(401, '登录已失效，请重新登录')
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        response.delete_cookie(COOKIE, path='/')

    @app.get('/api/admin/users', tags=['admin'])
    def admin_users(request: Request):
        current_superadmin(request)
        return accounts.list_accounts()

    @app.put('/api/admin/users/{username}/password', status_code=204, tags=['admin'])
    async def admin_password(username: str, body: NewPassword, request: Request):
        actor=current_superadmin(request)
        check_rate(request,actor['username'])
        try:
            async with password_slots:
                await asyncio.to_thread(accounts.reset_password_as_superadmin,session_token(request),username,body.password)
        except PermissionError:
            raise HTTPException(403,'需要超级管理员权限')
        except ValueError:
            raise HTTPException(404,'账号不存在')

    @app.get("/api/health", tags=["system"])
    def health():
        return {"status": "ok", "version": VERSION, "login_required": True}

    @app.get('/api/channels/feishu', tags=['channels'])
    def feishu_status(request: Request):
        return feishu.status(current_user(request)['id'])

    @app.post('/api/channels/feishu/pairing', tags=['channels'])
    def feishu_pairing(request: Request):
        user=current_user(request)
        check_rate(request,user['username'])
        try:return feishu.pairing(user['id'])
        except ValueError as exc:raise HTTPException(503,str(exc))

    @app.delete('/api/channels/feishu/binding',status_code=204,tags=['channels'])
    async def feishu_unbind(request: Request):
        feishu.unbind(current_user(request)['id'])

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if 'text/html' in request.headers.get('accept',''):
            if chat_index.is_file():
                return FileResponse(chat_index, headers={'Cache-Control':'no-cache'})
            return frontend_not_built()
        return {"service": "Memoria-Agent", "docs": "/docs", "health": "/api/health",
                "studio": "/studio/", "chat": "/",
                "register": "/api/auth/register", "login": "/api/auth/login"}

    # Build schema without opening the legacy database or warming a tenant/model.
    from fastapi.openapi.utils import get_openapi
    def openapi():
        if app.openapi_schema is None:
            schema_app = _create_tenant_app(tenant_settings=copy.deepcopy(settings), schema_only=True)
            routes = [r for r in schema_app.routes if getattr(r, "path", "") != "/api/health"]
            app.openapi_schema = get_openapi(title=app.title, version=VERSION, routes=app.routes + routes)
            components = app.openapi_schema.setdefault("components", {})
            components.setdefault("securitySchemes", {})["UserSession"] = {"type": "http", "scheme": "bearer"}
            for path, operations in app.openapi_schema["paths"].items():
                if path.startswith("/api/") and path not in {"/api/health", "/api/auth/login", "/api/auth/register"}:
                    for operation in operations.values():
                        if isinstance(operation, dict): operation["security"] = [{"UserSession": []}]
        return app.openapi_schema
    app.openapi = openapi
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    chat_index = frontend / 'chat' / 'index.html'
    def frontend_not_built():
        return JSONResponse({'message':'请先在 frontend 目录执行 npm ci 和 npm run build，再重启服务'}, status_code=503)
    if (frontend / "studio" / "index.html").is_file():
        # Public shell only; all data still passes authenticated tenant dispatch.
        app.mount("/studio", StaticFiles(directory=frontend / 'studio', html=True), name="memory-studio")
    else:
        @app.get("/studio/", include_in_schema=False)
        def studio_not_built():
            return frontend_not_built()
    if (frontend / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=frontend / 'assets'), name='frontend-assets')
    @app.get('/app', include_in_schema=False)
    @app.get('/app/', include_in_schema=False)
    def retired_workspace():
        return JSONResponse({'message':'旧工作台已移除；聊天请访问 /，管理请访问 /studio/'},status_code=404)
    app.mount("/", PrivateDispatch(accounts, pool))
    return app

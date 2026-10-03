import os
import secrets
from urllib.parse import urlencode

import aiohttp
import aiomysql
from itsdangerous import BadSignature, URLSafeSerializer

DISCORD_API = "https://discord.com/api/v10"
SESSION_COOKIE = "logs_session"


class Auth:
    def __init__(self):
        self.client_id = os.environ["DISCORD_CLIENT_ID"]
        self.client_secret = os.environ["DISCORD_CLIENT_SECRET"]
        self.public_url = os.environ["PUBLIC_URL"].rstrip("/")
        self.central_url = os.environ["SSCENTRAL_URL"].rstrip("/")
        self.serializer = URLSafeSerializer(os.environ["SESSION_SECRET"], salt="session")
        self.db = {
            "host": os.environ["DB_HOST"],
            "port": int(os.environ.get("DB_PORT", "3306")),
            "db": os.environ["DB_NAME"],
            "user": os.environ["DB_USER"],
            "password": os.environ["DB_PASSWORD"],
        }
        self.states = set()

    @property
    def redirect_uri(self):
        return f"{self.public_url}/auth/callback"

    def login_url(self):
        state = secrets.token_urlsafe(16)
        self.states.add(state)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": "identify",
            "state": state,
        }
        return f"{DISCORD_API}/oauth2/authorize?{urlencode(params)}"

    async def complete(self, code, state):
        if state not in self.states:
            return None, "Сессия входа устарела, попробуйте ещё раз"
        self.states.discard(state)
        async with aiohttp.ClientSession() as session:
            token = await self._exchange(session, code)
            if not token:
                return None, "Discord не подтвердил вход"
            async with session.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status != 200:
                    return None, "Discord не ответил"
                user = await resp.json()
            ckey = await self._ckey_for(session, user["id"])
        if not ckey:
            return None, "Этот Discord не привязан ни к одному ckey"
        rank = await self._admin_rank(ckey)
        if not rank:
            return None, f"У {ckey} нет прав администратора на сервере"
        return {"ckey": ckey, "rank": rank, "discord_id": user["id"]}, None

    def cookie(self, identity):
        return self.serializer.dumps(identity)

    def identity(self, cookie):
        if not cookie:
            return None
        try:
            return self.serializer.loads(cookie)
        except BadSignature:
            return None

    async def _exchange(self, session, code):
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        async with session.post(f"{DISCORD_API}/oauth2/token", data=form) as resp:
            if resp.status != 200:
                return None
            return (await resp.json()).get("access_token")

    async def _ckey_for(self, session, discord_id):
        async with session.get(f"{self.central_url}/players/discord/{discord_id}") as resp:
            if resp.status != 200:
                return None
            return (await resp.json()).get("ckey")

    async def _admin_rank(self, ckey):
        conn = await aiomysql.connect(**self.db)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT `rank` FROM admin WHERE ckey = %s", (ckey,))
                row = await cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

"""경동원(KDOne) 나비엔 홈네트워크 클라우드 API - 사용량 조회용 비동기 클라이언트.

APK(4.14.04) v5/r.java, v5/m.java 분석 결과 기반:
  - Base URL : https://ilps.naviensmartcontrol.com:3000
  - 전송     : body = base64(AES-256-CBC(json)), header x-message-id = 32자 랜덤 키
               key/iv = PBKDF2-HMAC-SHA256(password=reverse(키), salt=키[:4], iter=5, len=48)
  - 사용량   : POST /used/list
               {Access_Token, Refresh_Token, Complex, Category_Type, Start_Time, End_Time, Query_Type}
               Category_Type: Electricity | Gas | Water | Hotwater | Heating | Cooling
               Query_Type   : Month | Day
               응답 Resources[]: {Start_Time, Amount, Unit}
  - 응답 최상위에 Access_Token / Refresh_Token 이 오면 갱신된 토큰이므로 교체해야 함.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import string
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://ilps.naviensmartcontrol.com:3000"
SUCCESS = "0000"
# r.java: C=5010, G=8800, H=8801, I=8802, O=8803 -> 세션/토큰 오류로 처리됨
AUTH_ERROR_CODES = {"5010", "8800", "8801", "8802", "8803"}
APP_TYPE = "2"  # Android=2, iOS=1

KST = ZoneInfo("Asia/Seoul")

CATEGORIES = ["Electricity", "Gas", "Water", "Hotwater", "Heating", "Cooling"]


class KDOneError(Exception):
    """일반 API 오류."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class KDOneAuthError(KDOneError):
    """토큰 만료/인증 실패."""


class KDOneNeedCertification(Exception):
    """월패드 인증번호가 필요함."""


# ---------------------------------------------------------------- crypto
def _cipher(key: str) -> Cipher:
    dk = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=48, salt=key[:4].encode(), iterations=5
    ).derive(key[::-1].encode())
    return Cipher(algorithms.AES(dk[:32]), modes.CBC(dk[32:48]))


def encrypt(key: str, text: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode()) + padder.finalize()
    enc = _cipher(key).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def decrypt(key: str, payload: str) -> str:
    dec = _cipher(key).decryptor()
    raw = dec.update(base64.b64decode(payload)) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return (unpad.update(raw) + unpad.finalize()).decode()


def random_key(n: int = 32) -> str:
    alphabet = string.digits + string.ascii_uppercase + string.ascii_lowercase
    return "".join(secrets.choice(alphabet) for _ in range(n))


def row_date(value: Any) -> date | None:
    """Start_Time -> KST 날짜. 실서버(2026-11-21)는 Unix 초(KST 자정)를 준다. ISO 문자열도 허용."""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        ts = float(value)
        if ts > 1e11:  # 밀리초
            ts /= 1000
        return datetime.fromtimestamp(ts, KST).date()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- client
class KDOneClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        complex_id: str | None = None,
        app_certify: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._session = session
        self.username = username
        self.password = password
        self.complex_id = complex_id
        self.app_certify = app_certify or ""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.base_url = base_url
        # 마지막 요청의 비밀 없는 진단 정보(HTTP 상태, 복호화 키 종류, 토큰 회전 여부 등)
        self.last_meta: dict[str, Any] = {}
        self.last_rows: dict[str, Any] = {}

    # -- low level -----------------------------------------------------
    async def _post(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        key = random_key()
        meta: dict[str, Any] = {"endpoint": endpoint}
        self.last_meta = meta
        async with self._session.post(
            f"{self.base_url}{endpoint}",
            data=encrypt(key, json.dumps(data, ensure_ascii=False)),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "x-message-id": key,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            body = await resp.text()
            meta["http_status"] = resp.status
            if resp.status == 404 and endpoint == "/users/login2":
                meta["result"] = "http_404"
                raise KDOneNeedCertification
            resp.raise_for_status()
            resp_key = resp.headers.get("x-message-id")
            meta["resp_key_header"] = bool(resp_key)

        result: dict[str, Any] | None = None
        # 안드로이드 앱은 요청 키로 복호화, KDOne(py)은 응답 헤더 키로 복호화 -> 둘 다 시도.
        # 잘못된 키로도 패딩이 우연히 맞을 수 있으므로 JSON 파싱까지 성공해야 채택한다.
        for label, k in (("response_key", resp_key), ("request_key", key)):
            if not k:
                continue
            try:
                result = json.loads(decrypt(k, body))
                meta["decrypted_with"] = label
                break
            except Exception:  # noqa: BLE001
                continue
        if result is None:
            result = json.loads(body)  # 평문 응답(complex-list 등)
            meta["decrypted_with"] = "plain"
        meta["top_keys"] = sorted(result)

        # 토큰 회전
        rotated = []
        if result.get("Access_Token"):
            rotated.append("access" if result["Access_Token"] != self.access_token else "access(same)")
            self.access_token = result["Access_Token"]
        if result.get("Refresh_Token"):
            rotated.append("refresh" if result["Refresh_Token"] != self.refresh_token else "refresh(same)")
            self.refresh_token = result["Refresh_Token"]
        meta["token_rotation"] = rotated

        code = str(result.get("Error_Cd", ""))
        meta["error_cd"] = code
        if code != SUCCESS:
            msg = str(result.get("Error_Nm", ""))
            meta["error_nm"] = msg
            if code in AUTH_ERROR_CODES:
                raise KDOneAuthError(code, msg)
            raise KDOneError(code, msg)
        return result

    def _auth(self) -> dict[str, str]:
        return {
            "Access_Token": self.access_token or "",
            "Refresh_Token": self.refresh_token or "",
            "Complex": self.complex_id or "",
        }

    # -- login ---------------------------------------------------------
    async def get_complexes(self) -> list[dict[str, Any]]:
        async with self._session.post(
            f"{self.base_url}/info/complex-list",
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            data = json.loads(await resp.text())
        return data.get("Resources", [])

    async def login(self) -> None:
        """App_Certify 로 로그인. 기기 미인증이면 KDOneNeedCertification."""
        try:
            res = await self._post(
                "/users/login2",
                {
                    "User_Id": self.username,
                    "User_Pw": self.password,
                    "Complex": self.complex_id,
                    "App_Certify": self.app_certify,
                    "App_Type": APP_TYPE,
                },
            )
        except KDOneError as err:
            _LOGGER.debug("login2 실패: %s", err)
            if not self.app_certify:
                raise KDOneNeedCertification from err
            raise
        oauth = (res.get("Resources") or [{}])[0].get("Oauth", {})
        if not oauth.get("Access_Token"):
            raise KDOneNeedCertification
        self.access_token = oauth["Access_Token"]
        self.refresh_token = oauth.get("Refresh_Token")

    async def request_certification_code(self) -> None:
        """월패드로 인증번호 전송."""
        await self._post(
            "/users/certification-code",
            {"Complex": self.complex_id, "User_Id": self.username},
        )

    async def certify(self, number: str) -> None:
        await self._post(
            "/users/certification",
            {
                "User_Id": self.username,
                "Complex": self.complex_id,
                "Certify_Number": number,
            },
        )
        self.app_certify = number
        await self.login()

    # -- usage ---------------------------------------------------------
    async def used_list(
        self, category: str, start: str, end: str, query_type: str
    ) -> list[dict[str, Any]]:
        payload = {
            **self._auth(),
            "Category_Type": category,
            "Start_Time": start,
            "End_Time": end,
            "Query_Type": query_type,
        }
        try:
            res = await self._post("/used/list", payload)
        except KDOneAuthError:
            await self.login()  # 저장된 App_Certify 로 재로그인 후 1회 재시도
            payload.update(self._auth())
            res = await self._post("/used/list", payload)
        return res.get("Resources") or []

    async def get_usage(
        self,
        category: str,
        today: date | None = None,
        cached_months: dict[str, float] | None = None,
        start: date | None = None,
    ) -> dict[str, Any]:
        """카테고리 하나에 대해 월별(start~오늘), 일별(이번달) 사용량과 start 이후 누적.

        today 는 서버 기준(KST) 날짜여야 한다. HA에서는 dt_util.now().date()를 넘긴다.
        cached_months 를 주면 Month 조회를 생략한다(호출 수 절감; 차단 위험 때문).
        """
        today = today or date.today()
        start = start or date(today.year - 1, 1, 1)
        end = f"{today.isoformat()}T23:59:59"
        self.last_rows = {"Month": None, "Day": None}
        months_rows = None
        if cached_months is None:
            months_rows = await self.used_list(
                category, f"{start:%Y-%m}-01T00:00:00", end, "Month"
            )
        # 일별은 지난달 1일부터 조회한다(호출 수 동일). 말일 값은 다음 날(1일) 새벽에야 채워지므로
        # 지난달 일별 확정값을 last_month 센서의 daily 속성으로 내주기 위함.
        prev_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        days_rows = await self.used_list(
            category, f"{prev_first.isoformat()}T00:00:00", end, "Day"
        )
        self.last_rows = {"Month": months_rows, "Day": days_rows}
        unit = None
        months: dict[str, float] = dict(cached_months or {})
        days: dict[str, float] = {}
        for rows, store, n in ((months_rows or [], months, 7), (days_rows, days, 10)):
            for row in rows:
                d = row_date(row.get("Start_Time"))
                try:
                    amount = float(row["Amount"])
                except (KeyError, TypeError, ValueError):
                    continue
                if d is None or d < start.replace(day=1):
                    continue
                store[d.isoformat()[:n]] = amount
                unit = unit or row.get("Unit")
        this_month = f"{today:%Y-%m}"
        last_month = f"{(today.replace(day=1) - timedelta(days=1)):%Y-%m}"
        # 캐시된 월 값은 조회 시점까지의 이번달 값이라 낡았을 수 있다 -> 일별 합계를 우선.
        prev_days = {k: v for k, v in days.items() if k[:7] == last_month}
        days = {k: v for k, v in days.items() if k[:7] == this_month}
        month_now = sum(days.values()) if days else months.get(this_month)
        # 누적(start 이후): 지난달까지 확정된 월 값 + 이번달. 월초에 0으로 돌아가지 않으므로
        # 하루 3회 조회여도 월말 마지막 조회 뒤의 사용량이 다음 날 지난달 확정값으로 반영된다.
        total = None
        if month_now is not None:
            total = round(sum(v for k, v in months.items() if k < this_month) + month_now, 3)
        return {
            "unit": unit,
            "months": months,
            "days": days,
            "prev_days": prev_days,
            "today": days.get(today.isoformat()),
            "this_month": month_now,
            "last_month": months.get(last_month),
            "total": total,
        }

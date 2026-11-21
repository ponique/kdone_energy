"""HA 설치 전, 사용량 API가 실제로 동작하는지 확인하는 테스트 스크립트.

사용법:
    pip install aiohttp cryptography
    python kdone_usage_cli.py          # 요약 + 진단
    python kdone_usage_cli.py --raw    # 각 항목의 서버 응답 행(Start_Time/Amount/Unit) 전체도 출력

최초 실행 시 월패드 인증번호를 입력하며, 토큰은 kdone_session.json(권한 600)에 저장되어
다음 실행부터는 인증 없이 조회됩니다. 출력에는 비밀번호·토큰을 찍지 않으므로
결과를 그대로 붙여넣어 공유해도 됩니다(kdone_session.json 파일은 공유·커밋 금지).
"""
import asyncio
import getpass
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_components", "kdone_energy"))
from api import CATEGORIES, KDOneClient, KDOneError, KDOneNeedCertification  # noqa: E402

SESSION_FILE = "kdone_session.json"
KST = ZoneInfo("Asia/Seoul")


def meta(client: KDOneClient) -> str:
    """마지막 요청의 비밀 없는 진단(HTTP 상태, 복호화 키 종류, 최상위 키, 토큰 회전, Error_Cd)."""
    return json.dumps(client.last_meta, ensure_ascii=False)


def save(client: KDOneClient) -> None:
    fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(
            {
                "username": client.username,
                "password": client.password,
                "complex": client.complex_id,
                "app_certify": client.app_certify,
                "access_token": client.access_token,
                "refresh_token": client.refresh_token,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


async def main(raw: bool) -> None:
    saved = {}
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    today = datetime.now(KST).date()
    print(f"# 기준일(KST) {today}  저장된 세션 {'있음' if saved.get('access_token') else '없음'}")

    async with aiohttp.ClientSession() as session:
        client = KDOneClient(
            session,
            saved.get("username") or input("아이디: "),
            saved.get("password") or getpass.getpass("비밀번호: "),
            complex_id=saved.get("complex"),
            app_certify=saved.get("app_certify"),
            access_token=saved.get("access_token"),
            refresh_token=saved.get("refresh_token"),
        )

        if not client.complex_id:
            keyword = input("단지명 검색어: ").strip()
            hits = [c for c in await client.get_complexes() if keyword in c.get("Complex_Name", "")]
            for i, c in enumerate(hits):
                print(f"[{i}] {c['Complex']}  {c.get('Complex_Name')}  {c.get('Complex_Addr')}")
            client.complex_id = str(hits[int(input("번호 선택: "))]["Complex"])

        if not client.access_token:
            try:
                await client.login()
                print(f"# 첫 login2: 인증 없이 성공 {meta(client)}")
            except KDOneNeedCertification:
                # Q1: 미인증 기기 로그인 실패가 HTTP 404인지 Error_Cd인지
                print(f"# 첫 login2: 기기 인증 필요 {meta(client)}")
                await client.request_certification_code()
                print(f"# certification-code {meta(client)}")
                await client.certify(input("월패드 인증번호: ").strip())
                print(f"# certify 후 login2 {meta(client)}")
            save(client)

        for cat in CATEGORIES:
            try:
                u = await client.get_usage(cat, today)
            except KDOneError as err:
                # Q5: 미설치 항목(냉방 등)의 응답 형태
                print(f"{cat:10s} 오류 {err} {meta(client)}")
                continue
            rows = client.last_rows
            units = sorted({str(r.get("Unit")) for k in rows for r in (rows[k] or [])})
            print(
                f"{cat:10s} 단위원문={units} 오늘={u['today']} 이번달={u['this_month']} "
                f"지난달={u['last_month']} 행수(Month/Day)={len(rows['Month'] or [])}/{len(rows['Day'] or [])}"
            )
            # Q3: 이번달 월 값이 오늘까지 누적인지 — 일별 합계·마지막 일자와 비교
            if u["days"]:
                print(f"{'':10s} 일별합계={round(sum(u['days'].values()), 3)} 마지막일={max(u['days'])}")
            # Q4: 토큰 회전 여부(token_rotation)
            print(f"{'':10s} 진단 {meta(client)}")
            if raw:
                for k in ("Month", "Day"):
                    print(f"{'':10s} {k}: {json.dumps(rows[k], ensure_ascii=False)}")

        save(client)
        print("# 세션 저장 완료 (토큰 값은 출력하지 않음)")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main("--raw" in sys.argv))

# 경동원 홈네트워크 → Home Assistant 에너지 사용량

경동원(KD Navien) 홈네트워크 앱의 전기·가스·수도·온수·난방 사용량을 Home Assistant 센서로 가져옵니다.
비공식 통합입니다. 본인 계정으로만 사용하세요.

## 분석 결과 (APK 4.14.04 + soulee-dev/KDOne)

| 항목 | 내용 |
|---|---|
| 서버 | `https://ilps.naviensmartcontrol.com:3000` (KDOne과 동일) |
| 암호화 | KDOne과 동일 (AES-256-CBC, PBKDF2-SHA256 5회, `x-message-id` 헤더) |
| 사용량 API | `POST /used/list` (앱 내부 코드 `USED_CALL_LIST`, 화면 `e6/h.java`) |
| 요청 | `Access_Token, Refresh_Token, Complex, Category_Type, Start_Time, End_Time, Query_Type` |
| Category_Type | `Electricity`(전기) `Gas`(가스) `Water`(수도) `Hotwater`(온수) `Heating`(난방) `Cooling`(냉방) |
| Query_Type | `Month`(월별) / `Day`(일별) |
| 요청 시간 형식 | `2026-11-01T00:00:00` ~ `2026-11-21T23:59:59` |
| 응답 `Start_Time` | Unix 초(KST 자정) |
| 응답 | `Resources: [{Start_Time, Amount, Unit}, ...]` |
| 토큰 | 응답 최상위에 `Access_Token`/`Refresh_Token`이 오면 새 토큰 → 교체 (실서버에서는 아직 회전 관측 없음) |
| 인증오류 | `Error_Cd` 5010, 8800~8803 → 저장된 `App_Certify`로 `login2` 재로그인 |

참고 API: `/info/administrative-expenses-list`(관리비), `/info/energy-used-target-list`(목표 사용량).

## 1단계: PC에서 먼저 확인

```
pip install aiohttp cryptography
python kdone_usage_cli.py
```

아이디/비번 → 단지 검색 → 월패드 인증번호 입력 → 6개 항목의 오늘/이번달/지난달 사용량이 출력됩니다.
**단위(`Unit`) 값과 숫자가 앱과 같은지 확인**해 주세요.

## 2단계: HA 설치 (HACS)

1. HACS > 우측 상단 메뉴 > 사용자 지정 저장소 > `https://github.com/ponique/kdone_energy`, 유형 "통합구성요소"
2. "경동원 홈네트워크 에너지" 다운로드 → HA 재시작
3. 설정 > 기기 및 서비스 > 통합 추가 > "경동원 홈네트워크 에너지"
4. 아이디/비번 입력. 이미 월패드 인증을 마친 번호가 있으면 "기존 인증번호"에 넣으면 재인증 없이 로그인됩니다.
   입주 전 기록을 빼려면 "데이터 시작 월"(예: `2025-10`)을 넣습니다.
5. 단지 선택 → (필요 시) 월패드 인증번호 입력

수동 설치: `custom_components/kdone_energy` 폴더를 HA `config/custom_components/`에 복사 후 재시작.

생성 센서 (항목별): `○○ 누적 사용량`(데이터 시작 월 이후, 리셋 없음), `○○ 오늘 사용량`, `○○ 이번달 사용량`(일별 attribute), `○○ 지난달 사용량`(월별 attribute). 전 기간 0인 항목(냉방)은 만들지 않습니다.

## 에너지 대시보드

에너지 대시보드에는 `누적 사용량` 센서를 씁니다. `이번달`은 월초 리셋 때 월말 마지막 조회 뒤 사용량이 빠질 수 있지만,
누적 센서는 다음 날 지난달 확정값으로 채워집니다. 설정 > 대시보드 > 에너지에서
- 전력 소비: `전기 누적 사용량`
- 가스 소비: `가스 누적 사용량`
- 수도 소비: `수도 누적 사용량` (+ 온수는 개별 기기/수도에 추가)
- 난방: 서버 단위가 `㎥`라 에너지 대시보드 항목에 맞지 않습니다. 센서로 일/월 비교에 씁니다.

## 주의

- 하루 1회(06:17 KST)만 조회합니다. 서버는 하루치를 다음 날 새벽에 채우므로 당일 중 조회는 의미가 없고, 잦은 호출은 비정상 접근으로 차단될 수 있습니다. 하루 약 10회 호출(+HA 재시작마다 1회분).
- 그날 사용량은 다음 날 06:17 조회 때 반영됩니다. `오늘 사용량` 센서는 서버 당일 값(거의 갱신 안 됨)이라 참고용입니다.
- 같은 계정으로 휴대폰 앱과 동시 로그인 시 한쪽 토큰이 무효화될 수 있습니다.
  이 경우 HA가 재인증을 요청합니다(월패드 인증번호 재입력). 가능하면 HA 전용 계정 사용을 권장.
- 단지에 미설치된 항목(예: 냉방)은 센서가 생성되지 않습니다.

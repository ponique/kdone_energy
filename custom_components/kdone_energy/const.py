DOMAIN = "kdone_energy"

CONF_COMPLEX = "complex"
CONF_APP_CERTIFY = "app_certify"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_CERT_NUMBER = "certify_number"

# 잦은 호출은 비정상 접근으로 차단될 수 있다(사용자 경고). 하루 1회 고정 시각(KST)에만 조회한다.
# 서버는 하루치를 다음 날 새벽에 채우고 당일 값은 거의 갱신하지 않는다(2026-11-24/25 관측:
# 24일 전기 23:47에 0.63 -> 25일 07:04에 10.65). 그래서 당일 중 조회는 의미가 없고,
# 전날 값이 채워진 새벽에 한 번만 조회한다. 정각을 피한 분.
UPDATE_TIMES = ((6, 17),)

# 데이터 시작 월(YYYY-MM). 입주 전 기록을 빼려고 통합 추가 시 입력. 비우면 서버가 주는 전 기간(작년 1월~).
CONF_START_MONTH = "start_month"

CATEGORY_NAMES = {
    "Electricity": "전기",
    "Gas": "가스",
    "Water": "수도",
    "Hotwater": "온수",
    "Heating": "난방",
    "Cooling": "냉방",
}

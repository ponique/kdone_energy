DOMAIN = "kdone_energy"

CONF_COMPLEX = "complex"
CONF_APP_CERTIFY = "app_certify"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_CERT_NUMBER = "certify_number"

# 잦은 호출은 비정상 접근으로 차단될 수 있다(사용자 경고). 하루 3회 고정 시각(KST)에만 조회한다.
# 정각을 피한 분, 마지막은 자정 직전이라 그날 사용량이 그날 통계에 들어간다.
UPDATE_TIMES = ((7, 17), (15, 17), (23, 47))

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

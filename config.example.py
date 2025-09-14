# config.example.py — пример конфига без секретов

# === Storage / Pair ===
DB_PATH = "ebot.db"
PAIR    = "KASUSDC"
BASE_ASSET  = "KAS"
QUOTE_ASSET = "USDC"

# === MEXC endpoints (REAL) ===
MEXC_API_URL  = "https://api.mexc.com"
MEXC_HTTP_URL = "https://api.mexc.com/api/v3/klines"
MEXC_WS_URL   = "wss://wbs.mexc.com/ws"

# === Telegram ===
TG_BOT_TOKEN = "REPLACE_ME"
TG_CHAT_ID   = "REPLACE_ME"
TG_ERROR_COOLDOWN_SEC = 600   # анти-спам ошибок (сек)

# === Exchange API keys (REAL) ===
API_KEY    = "REPLACE_ME"
API_SECRET = "REPLACE_ME"

# === Modes / toggles ===
PAPER_TRADE   = False
PAUSE_TRADING = False          # глобальная пауза торговли (оркестратор её уважает)

# === Capital / accounting (для репортов/лимитов) ===
START_CAPITAL_USD = 1000.0
MAKER_FEE_PCT     = 0.000

# === Market data / Range builder ===
MAX_CANDLE_GAP = 60
GRID_STEPS     = 30

# === Глобальные лимиты/квантизация ===
MIN_ORDER_USD       = 1.20
SMALL_REMAINDER_USD = 1.50
MICRO_OFFSET_MIN    = 0.000001
MICRO_OFFSET_MAX    = 0.000005

# === BUY strategy ===
BUY_BELOW_OFFSETS       = [0.005, 0.010, 0.015]
BUY_SIZE_BELOW_FIXED_USD= 5.0
BUY_INCHANNEL_LEVELS    = [5, 10, 15]
BUY_SIZE_INCH_MAX_USD   = 5.0
BUY_SIZE_INCH_MIN_USD   = 2.0
BUY_SIZE_ABOVE_FIXED_USD= 2.0
BUY_MAX_OPEN_ORDERS     = 80
BUY_CANCEL_FAR_COUNT    = 10
BUY_LIMIT_STICKY_TICKS  = 2

# === SELL strategy ===
SELL_SPLIT      = 0.5
SELL_MIN_GAIN   = 0.01
SELL_MICROSHIFT = 0.0001
MAX_OPEN_SELLS  = 50

# === Sync settings ===
SYNC_WINDOW_MIN = 5
POSITION_ZERO_QTY_THRESH = 1e-6

# === Orchestrator (ebot.py) — интервалы ===
EBOT_SYNC_INTERVAL_SEC   = 60
EBOT_BUY_INTERVAL_SEC    = 300
EBOT_SELL_INTERVAL_SEC   = 300
EBOT_REPORT_INTERVAL_SEC = 1800

ENABLE_SYNC   = True
ENABLE_BUY    = True
ENABLE_SELL   = True
ENABLE_REPORT = True

# === Reports ===
REPORT_PERIOD_MIN = 30

# --- Sync extras ---
SYNC_OPEN_LIMIT = 500
SYNC_WINDOW_MIN = 5

# ===== BUY ABOVE-CHANNEL SETTINGS =====
BUY_ABOVE_PCT = 0.01

# ===== CONSOLIDATOR (collapse BUY orders) =====
ENABLE_CONSOLIDATE       = True
CONSOLIDATE_CHECK_EVERY_SEC = 900
CONSOLIDATE_LIMIT_OVER   = 100
CONSOLIDATE_TO_CANCEL    = 60
CONSOLIDATE_PLACE_COUNT  = 30
SCHEDULER_JITTER_MAX_SEC = 7

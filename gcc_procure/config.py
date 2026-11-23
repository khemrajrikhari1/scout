"""Central configuration. Everything can be overridden with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PROCURE_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("PROCURE_DB", DATA_DIR / "procurement.db"))
EXPORT_DIR = Path(os.environ.get("PROCURE_EXPORT_DIR", ROOT / "exports"))
SEED_DIR = Path(__file__).resolve().parent / "seeds"
LOG_DIR = Path(os.environ.get("PROCURE_LOG_DIR", DATA_DIR / "logs"))

# Honest, identifiable user agent. We do NOT rotate or spoof browsers.
USER_AGENT = os.environ.get(
    "PROCURE_USER_AGENT",
    "AWS-Distribution-ProcurementResearch/0.1 (+business research; contact: set PROCURE_USER_AGENT)",
)

# Politeness
MIN_DELAY_PER_HOST = float(os.environ.get("PROCURE_MIN_DELAY", "2.5"))  # seconds
REQUEST_TIMEOUT = float(os.environ.get("PROCURE_TIMEOUT", "15"))
MAX_RETRIES = int(os.environ.get("PROCURE_RETRIES", "3"))
MAX_PAGES_PER_COMPANY = int(os.environ.get("PROCURE_MAX_PAGES", "8"))
MAX_BYTES = 2_000_000
RECRAWL_AFTER_DAYS = int(os.environ.get("PROCURE_RECRAWL_DAYS", "30"))

# Optional search provider keys (neither is required)
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

COUNTRIES = {
    "UAE": {"iso": "AE", "dial": "+971", "name": "United Arab Emirates"},
    "Saudi Arabia": {"iso": "SA", "dial": "+966", "name": "Saudi Arabia"},
    "Oman": {"iso": "OM", "dial": "+968", "name": "Oman"},
    "Qatar": {"iso": "QA", "dial": "+974", "name": "Qatar"},
    "Kuwait": {"iso": "KW", "dial": "+965", "name": "Kuwait"},
    "Bahrain": {"iso": "BH", "dial": "+973", "name": "Bahrain"},
}

CAT_SUPER = "Supermarkets & Hypermarkets"
CAT_FOOD = "Food FMCG Retail"
CAT_NONFOOD = "Non-Food FMCG Retail"
CAT_PHARMA = "Drug Stores & Pharmacies"
CAT_WHOLESALE = "FMCG Wholesalers"
CAT_DIST = "Distributors & Importers"
CATEGORIES = [CAT_SUPER, CAT_FOOD, CAT_NONFOOD, CAT_PHARMA, CAT_WHOLESALE, CAT_DIST]

# Execution phases requested by the business
PHASES = {
    1: {"name": "UAE supermarkets & hypermarkets", "countries": ["UAE"], "categories": [CAT_SUPER]},
    2: {"name": "UAE food FMCG retailers", "countries": ["UAE"], "categories": [CAT_FOOD]},
    3: {"name": "UAE non-food FMCG retailers", "countries": ["UAE"], "categories": [CAT_NONFOOD]},
    4: {"name": "UAE pharmacies & drugstores", "countries": ["UAE"], "categories": [CAT_PHARMA]},
    5: {"name": "UAE wholesalers & distributors", "countries": ["UAE"], "categories": [CAT_WHOLESALE, CAT_DIST]},
    6: {"name": "Saudi Arabia (all categories)", "countries": ["Saudi Arabia"], "categories": CATEGORIES},
    7: {"name": "Oman, Qatar, Kuwait, Bahrain", "countries": ["Oman", "Qatar", "Kuwait", "Bahrain"], "categories": CATEGORIES},
}

import os
from dotenv import load_dotenv

load_dotenv()

HEDERA_OPERATOR_ID = os.getenv("HEDERA_OPERATOR_ID", "")
HEDERA_OPERATOR_KEY = os.getenv("HEDERA_OPERATOR_KEY", "")
HEDERA_TOPIC_ID = os.getenv("HEDERA_TOPIC_ID", "")
HEDERA_NETWORK = os.getenv("HEDERA_NETWORK", "testnet")

MIRROR_URLS = {
    "testnet": "https://testnet.mirrornode.hedera.com",
    "mainnet": "https://mainnet-public.mirrornode.hedera.com",
}
MIRROR_URL = MIRROR_URLS.get(HEDERA_NETWORK, MIRROR_URLS["testnet"])
HASHSCAN_BASE = f"https://hashscan.io/{HEDERA_NETWORK}"

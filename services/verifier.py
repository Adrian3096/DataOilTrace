import base64
import json
from datetime import datetime, timezone

import requests

from config.settings import HASHSCAN_BASE, HEDERA_TOPIC_ID, MIRROR_URL


def _fmt_timestamp(consensus_ts: str) -> str:
    seconds = float(consensus_ts)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def verify_version_record(record: dict) -> dict:
    """Check a specific locally indexed version against its Hedera Mirror Node entry."""
    sequence_number = record.get("sequence_number")
    if not sequence_number:
        return {"status": "ERROR", "message": "El registro no tiene número de secuencia."}

    try:
        response = requests.get(
            f"{MIRROR_URL}/api/v1/topics/{record['topic_id']}/messages/{sequence_number}",
            timeout=15,
        )
        response.raise_for_status()
        mirror_record = response.json()
        payload = json.loads(base64.b64decode(mirror_record["message"]).decode("utf-8"))
        verified = (
            mirror_record.get("topic_id") == record["topic_id"]
            and payload.get("hash_sha256") == record["file_hash"]
            and payload.get("app") == "DataOilTrace"
        )
        if not verified:
            return {
                "status": "SUCCESS",
                "verified": False,
                "message": "El contenido del registro en Mirror Node no coincide con esta versión.",
            }
        consensus_time = mirror_record.get("consensus_timestamp")
        return {
            "status": "SUCCESS",
            "verified": True,
            "consensus_timestamp": consensus_time,
            "consensus_time": _fmt_timestamp(consensus_time) if consensus_time else None,
            "hashscan_url": record.get("hashscan_url"),
        }
    except Exception as error:
        return {"status": "ERROR", "message": str(error)}


def verify_hash(file_hash: str, max_pages: int = 20) -> dict:
    """
    Busca en el Mirror Node de Hedera si el hash fue registrado en el Topic.
    - found=True  -> el archivo es idéntico al que se selló (y devuelve cuándo).
    - found=False -> el archivo no coincide con ningún registro: fue alterado o nunca se selló.
    """
    if max_pages < 1:
        return {"status": "ERROR", "message": "max_pages debe ser mayor que cero."}

    try:
        url = f"{MIRROR_URL}/api/v1/topics/{HEDERA_TOPIC_ID}/messages?limit=100&order=desc"
        for page_number in range(max_pages):
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            for msg in data.get("messages", []):
                try:
                    payload = json.loads(base64.b64decode(msg["message"]).decode("utf-8"))
                except (ValueError, KeyError):
                    continue
                if payload.get("hash_sha256") == file_hash:
                    return {
                        "status": "SUCCESS",
                        "found": True,
                        "file_name": payload.get("file_name"),
                        "metadata": payload.get("metadata", {}),
                        "sequence_number": msg.get("sequence_number"),
                        "consensus_time": _fmt_timestamp(msg["consensus_timestamp"]),
                        "hashscan_url": f"{HASHSCAN_BASE}/topic/{HEDERA_TOPIC_ID}",
                    }

            next_link = data.get("links", {}).get("next")
            if not next_link:
                break
            if page_number + 1 == max_pages:
                return {
                    "status": "ERROR",
                    "found": False,
                    "message": (
                        f"Búsqueda incompleta: se alcanzó el límite de "
                        f"{max_pages} páginas antes de revisar todos los registros."
                    ),
                }
            url = f"{MIRROR_URL}{next_link}"

        return {"status": "SUCCESS", "found": False}
    except Exception as error:
        return {"status": "ERROR", "message": str(error)}

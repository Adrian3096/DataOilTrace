import json

from hiero_sdk_python import (
    AccountId,
    Client,
    Network,
    PrivateKey,
    TopicId,
    TopicMessageSubmitTransaction,
)

from config.settings import (
    HASHSCAN_BASE,
    HEDERA_NETWORK,
    HEDERA_OPERATOR_ID,
    HEDERA_OPERATOR_KEY,
    HEDERA_TOPIC_ID,
)

MAX_HCS_MESSAGE_BYTES = 1024


def load_private_key(key_str: str) -> PrivateKey:
    """El portal de Hedera entrega la clave en distintos formatos; probamos todos."""
    for loader in (
        PrivateKey.from_string,
        PrivateKey.from_string_der,
        PrivateKey.from_string_ecdsa,
        PrivateKey.from_string_ed25519,
    ):
        try:
            return loader(key_str)
        except Exception:
            continue
    raise ValueError("HEDERA_OPERATOR_KEY no tiene un formato válido.")


def get_client() -> tuple[Client, PrivateKey]:
    if not HEDERA_OPERATOR_ID or not HEDERA_OPERATOR_KEY:
        raise ValueError("Configura HEDERA_OPERATOR_ID y HEDERA_OPERATOR_KEY en .env.")
    client = Client(Network(network=HEDERA_NETWORK))
    operator_key = load_private_key(HEDERA_OPERATOR_KEY)
    client.set_operator(AccountId.from_string(HEDERA_OPERATOR_ID), operator_key)
    return client, operator_key


def hashscan_tx_url(transaction_id: str) -> str:
    """0.0.123@1700000000.123456789 -> formato que acepta HashScan."""
    try:
        account, ts = str(transaction_id).split("@")
        seconds, nanos = ts.split(".")
        return f"{HASHSCAN_BASE}/transaction/{account}-{seconds}-{nanos}"
    except ValueError:
        return f"{HASHSCAN_BASE}/transaction/{transaction_id}"


def build_payload(file_name: str, file_hash: str, metadata: dict) -> str:
    payload = {
        "app": "DataOilTrace",
        "file_name": file_name,
        "hash_sha256": file_hash,
        "metadata": metadata,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def send_hash_to_hedera(file_name: str, file_hash: str, metadata: dict) -> dict:
    """Registra hash + metadatos en un Topic de Hedera Consensus Service."""
    try:
        if not HEDERA_TOPIC_ID:
            raise ValueError("No hay Topic configurado; crea uno en Hedera Testnet primero.")
        message = build_payload(file_name, file_hash, metadata)
        size = len(message.encode("utf-8"))
        if size > MAX_HCS_MESSAGE_BYTES:
            return {
                "status": "ERROR",
                "message": f"El mensaje pesa {size} bytes y el límite de HCS es "
                f"{MAX_HCS_MESSAGE_BYTES}. Reduce la metadata.",
            }

        client, _ = get_client()
        try:
            transaction = TopicMessageSubmitTransaction(
                topic_id=TopicId.from_string(HEDERA_TOPIC_ID),
                message=message,
            )
            response = transaction.execute(client, wait_for_receipt=False)
            receipt = response.get_receipt(client, validate_status=True)
            tx_id = str(response.transaction_id)

            return {
                "status": "SUCCESS",
                "topic_id": HEDERA_TOPIC_ID,
                "sequence_number": receipt.topic_sequence_number,
                "transaction_id": tx_id,
                "hashscan_url": hashscan_tx_url(tx_id),
            }
        finally:
            client.close()
    except Exception as error:
        return {"status": "ERROR", "message": str(error)}
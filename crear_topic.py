from pathlib import Path

from dotenv import set_key
from hiero_sdk_python import TopicCreateTransaction

from config.settings import HEDERA_NETWORK
from services.hedera_service import get_client

ENV_FILE = Path(__file__).resolve().parent / ".env"


def create_topic() -> str:
    """Crea un Topic en Hedera Testnet y devuelve su ID."""
    if HEDERA_NETWORK.lower() != "testnet":
        raise RuntimeError(
            "Creación cancelada: configura HEDERA_NETWORK=testnet en .env."
        )

    client, operator_key = get_client()
    try:
        receipt = TopicCreateTransaction(
            memo="DataOilTrace - Auditoria Oil&Gas",
            # Solo la clave del operador puede enviar mensajes al Topic.
            submit_key=operator_key.public_key(),
        ).execute(client, validate_status=True)
        if receipt.topic_id is None:
            raise RuntimeError("Hedera confirmó la transacción pero no devolvió Topic ID.")
        return str(receipt.topic_id)
    finally:
        client.close()


def main() -> None:
    try:
        topic_id = create_topic()
    except Exception as error:
        raise SystemExit(f"No se pudo crear el Topic: {error}") from error

    # Mostrar el ID antes de guardar, así no se pierde si falla la escritura local.
    print("Topic creado correctamente en Hedera Testnet.")
    print(f"HEDERA_TOPIC_ID={topic_id}")
    try:
        set_key(str(ENV_FILE), "HEDERA_TOPIC_ID", topic_id)
    except OSError as error:
        print(f"No se pudo actualizar .env ({error}); copia allí el Topic ID mostrado.")
    else:
        print("El Topic ID quedó guardado en .env.")


if __name__ == "__main__":
    main()
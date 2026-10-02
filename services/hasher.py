import hashlib
import os


def generate_file_sha256(file_path: str) -> str:
    """SHA-256 de un archivo (lectura por bloques, sirve para archivos grandes)."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def generate_bundle_sha256(file_paths: list[str]) -> str:
    """
    Hash único para un paquete de archivos (ej. un shapefile: .shp .shx .dbf .prj).
    Es determinista: no depende del orden en que se seleccionen los archivos.
    """
    entries = sorted(
        (os.path.basename(p).lower(), generate_file_sha256(p)) for p in file_paths
    )
    manifest = "\n".join(f"{name}:{digest}" for name, digest in entries)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()
"""Prueba de humo de la interfaz: construye la app con una página simulada y ejecuta
los flujos principales (sin red ni ventana). Hedera y Mirror Node se simulan.

Ejecutar:  python -m unittest tests.test_ui_smoke -v
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("HEDERA_TOPIC_ID", "0.0.1234")

import flet as ft
from openpyxl import Workbook

import storage.version_store as version_store
import ui.app_ui as app_ui


class FakePage:
    def __init__(self):
        self.services, self.added, self.tasks, self.dialogs = [], [], [], []
        self.window = SimpleNamespace()
        self.updates = 0

    def add(self, *controls):
        self.added.extend(controls)

    def update(self):
        self.updates += 1

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    async def drain(self):
        while self.tasks:
            handler, args = self.tasks.pop(0)
            await handler(*args)


def walk(control):
    yield control
    for attr in ("controls", "destinations"):
        for child in getattr(control, attr, None) or []:
            yield from walk(child)
    for attr in ("content", "leading"):
        child = getattr(control, attr, None)
        if isinstance(child, ft.Control):
            yield from walk(child)


def texts(root):
    return [c.value for c in walk(root) if isinstance(c, ft.Text) and c.value]


def find_button(root, label):
    for c in walk(root):
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton)) and c.content == label:
            return c
    raise AssertionError(f"No se encontró el botón {label!r}")


def make_xlsx(path: Path, value: int, extra_row: bool = False):
    wb = Workbook()
    ws = wb.active
    ws.title = "Produccion"
    ws.append(["Campo", "Barriles"])
    ws.append(["Norte", value])
    if extra_row:
        ws.append(["Sur", 50])
    wb.save(path)


FAKE_REMOTE = {
    "status": "SUCCESS",
    "verified": True,
    "consensus_timestamp": "1759300000.000000001",
    "consensus_time": "2025-10-01 06:26:40 UTC",
}


class UiSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for target, value in (
            ("STORAGE_DIR", self.root / "storage"),
            ("SNAPSHOT_DIR", self.root / "storage" / "snapshots"),
            ("DATABASE_PATH", self.root / "storage" / "versions.sqlite3"),
        ):
            p = patch.object(version_store, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

        self.selected: list[str] = []
        self.copied: list[str] = []
        self.hedera_calls = 0

        async def fake_pick(_self, **_kwargs):
            return [SimpleNamespace(path=self.selected[-1])]

        async def fake_copy(_self, value):
            self.copied.append(value)

        def fake_send(file_name, file_hash, metadata):
            self.hedera_calls += 1
            n = self.hedera_calls
            return {
                "status": "SUCCESS",
                "topic_id": "0.0.1234",
                "sequence_number": n,
                "transaction_id": f"0.0.5@1759300{n:03d}.000000001",
                "hashscan_url": f"https://hashscan.io/testnet/transaction/tx-{n}",
            }

        for target, new in (
            (ft.FilePicker, ("pick_files", fake_pick)),
            (ft.Clipboard, ("set", fake_copy)),
        ):
            p = patch.object(target, new[0], new[1])
            p.start()
            self.addCleanup(p.stop)
        for name, new in (
            ("verify_version_record", lambda record: dict(FAKE_REMOTE)),
            ("send_hash_to_hedera", fake_send),
        ):
            p = patch.object(app_ui, name, new)
            p.start()
            self.addCleanup(p.stop)

        self.file = self.root / "produccion.xlsx"
        make_xlsx(self.file, 100)

    def run_flow(self, coro_fn):
        async def runner():
            page = FakePage()
            app_ui.build_app(page)
            await page.drain()
            await coro_fn(page)

        asyncio.run(runner())

    def test_full_flow(self):
        async def flow(page):
            root = page.added[0]
            rail = next(c for c in walk(root) if isinstance(c, ft.NavigationRail))
            self.assertEqual(len(rail.destinations), 4)

            # Panel vacío
            self.assertIn("Todavía no hay archivos registrados", texts(root))

            # Ir a Archivo y seleccionar un libro nuevo
            rail.selected_index = 1
            rail.on_change(SimpleNamespace(control=rail))
            self.selected.append(str(self.file))
            await find_button(root, "Seleccionar Excel").on_click(None)
            self.assertIn("SIN REGISTRO EN HEDERA", texts(root))
            register = find_button(root, "Registrar versión inicial")
            self.assertTrue(register.visible)

            # Registrar v1 -> íntegro
            await register.on_click(None)
            self.assertEqual(self.hedera_calls, 1)
            self.assertIn("ARCHIVO ÍNTEGRO", texts(root))
            self.assertFalse(register.visible)
            self.assertIn("Versión 1", texts(root))

            # Modificar el archivo y recalcular -> modificado, con diff
            make_xlsx(self.file, 125, extra_row=True)
            await find_button(root, "Recalcular").on_click(None)
            self.assertIn("ARCHIVO MODIFICADO", texts(root))
            register = find_button(root, "Registrar como v2")
            self.assertTrue(register.visible)
            self.assertTrue(any("1 registro modificado" in s for s in texts(root)))
            self.assertIn("Produccion!B2", texts(root))

            # Registrar v2 y comprobar historial
            await register.on_click(None)
            self.assertEqual(self.hedera_calls, 2)
            self.assertIn("ARCHIVO ÍNTEGRO", texts(root))
            self.assertIn("Versión 2", texts(root))
            self.assertIn("Archivo actual", texts(root))

            # Verificar contra la v1 -> archivo modificado respecto a esa versión
            dropdown = next(
                c for c in walk(root) if isinstance(c, ft.Dropdown) and c.label == "Versión a comprobar"
            )
            dropdown.value = "1"
            await find_button(root, "Verificar").on_click(None)
            self.assertIn("ARCHIVO MODIFICADO", texts(root))
            dropdown.value = "2"
            await find_button(root, "Verificar").on_click(None)
            self.assertIn("ARCHIVO ÍNTEGRO", texts(root))

            # Comparar v1 -> v2
            await find_button(root, "Comparar").on_click(None)
            self.assertIn("Produccion!B2", texts(root))

            # Auditoría
            rail.selected_index = 2
            rail.on_change(SimpleNamespace(control=rail))
            await page.drain()
            audit_texts = texts(root)
            self.assertIn("Registro confirmado en Hedera", audit_texts)
            self.assertIn("SHA-256 generado", audit_texts)

            # Panel con un archivo íntegro
            rail.selected_index = 0
            rail.on_change(SimpleNamespace(control=rail))
            await page.drain()
            self.assertIn("Íntegro", texts(root))
            self.assertIn("produccion.xlsx", texts(root))

        self.run_flow(flow)

    def test_public_verification(self):
        async def flow(page):
            root = page.added[0]
            rail = next(c for c in walk(root) if isinstance(c, ft.NavigationRail))
            rail.selected_index = 3
            rail.on_change(SimpleNamespace(control=rail))
            self.selected.append(str(self.file))
            button = find_button(root, "Seleccionar archivo")
            found = {
                "status": "SUCCESS",
                "found": True,
                "file_name": "produccion.xlsx",
                "metadata": {"version": 2, "change_summary": "1 registro modificado"},
                "sequence_number": 7,
                "consensus_time": "2025-10-01 06:26:40 UTC",
                "hashscan_url": "https://hashscan.io/testnet/topic/0.0.1234",
            }
            with patch.object(app_ui, "verify_hash", lambda digest: found):
                await button.on_click(None)
            self.assertIn("ARCHIVO AUTÉNTICO", texts(root))
            self.assertIn("v2", texts(root))
            with patch.object(app_ui, "verify_hash", lambda digest: {"status": "SUCCESS", "found": False}):
                await button.on_click(None)
            self.assertIn("SIN COINCIDENCIAS", texts(root))
            with patch.object(app_ui, "verify_hash", lambda digest: {"status": "ERROR", "message": "sin red"}):
                await button.on_click(None)
            self.assertIn("VERIFICACIÓN INCOMPLETA", texts(root))

        self.run_flow(flow)

    def test_no_expanding_children_inside_wrapping_rows(self):
        """Flutter no admite hijos con expand dentro de un Row con wrap=True (pantalla gris)."""

        async def flow(page):
            root = page.added[0]
            rail = next(c for c in walk(root) if isinstance(c, ft.NavigationRail))
            for index in range(4):
                rail.selected_index = index
                rail.on_change(SimpleNamespace(control=rail))
                await page.drain()
                for control in walk(root):
                    if isinstance(control, ft.Row) and control.wrap:
                        for child in control.controls:
                            self.assertFalse(
                                getattr(child, "expand", None),
                                f"Row con wrap=True contiene un hijo expandible en la vista {index}: {child}",
                            )

        self.run_flow(flow)


if __name__ == "__main__":
    unittest.main()

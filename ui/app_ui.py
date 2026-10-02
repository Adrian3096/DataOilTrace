"""Interfaz de DataOilTrace (Flet 1.0.x).

Estructura:
  - Barra lateral: Panel · Archivo · Auditoría · Verificar
  - Panel     : KPIs de integridad y lista de archivos registrados
  - Archivo   : veredicto de integridad, evidencia, registro Hedera, historial y cambios
  - Auditoría : línea de tiempo con los timestamps de consenso de Hedera
  - Verificar : comprobación pública de cualquier archivo, sin historial local
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import flet as ft

from config.settings import HEDERA_NETWORK, HEDERA_TOPIC_ID
from services.excel_diff import compare_workbooks
from services.hasher import generate_file_sha256
from services.hedera_service import send_hash_to_hedera
from services.verifier import verify_hash, verify_version_record
from storage.version_store import (
    get_dashboard_data,
    get_latest,
    get_version,
    get_versions,
    initialize_store,
    register_version,
    update_source_path,
)
from ui import theme as t
from ui.components import (
    CopyField,
    SealBanner,
    card,
    caption,
    chip,
    empty_state,
    icon_badge,
    kv_row,
    primary_button,
    section_title,
    soft_button,
    stat_card,
    value_text,
)
from ui.utils import event_datetime, full_date, human_size, local_date, short_hash, spanish_day

MAX_CHANGES_SHOWN = 100
PAGE_TITLES = (
    ("Panel de integridad", "Estado de todos los archivos sellados en Hedera"),
    ("Archivo", "Calcula la huella, verifica y registra nuevas versiones"),
    ("Auditoría", "Línea de tiempo de huellas y confirmaciones de consenso"),
    ("Verificar documento", "Comprueba cualquier archivo contra el registro público"),
)
STATUS_STYLE = {
    "intact": ("Íntegro", t.OK, ft.Icons.CHECK_CIRCLE),
    "modified": ("Modificado", t.BAD, ft.Icons.WARNING_AMBER),
    "unknown": ("Sin verificar", t.WARN, ft.Icons.HELP_OUTLINE),
}


def build_app(page: ft.Page):
    initialize_store()

    # ------------------------------------------------------------------ #
    # Página
    # ------------------------------------------------------------------ #
    page.title = "DataOilTrace | Evidencia de integridad"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=t.BRAND)
    page.dark_theme = ft.Theme(color_scheme_seed=t.BRAND)
    page.bgcolor = t.BG
    page.padding = 0
    page.window.width = 1240
    page.window.height = 860
    page.window.min_width = 980
    page.window.min_height = 640

    clipboard = ft.Clipboard()
    file_picker = ft.FilePicker()
    page.services.extend([clipboard, file_picker])

    state: dict = {"path": None, "hash": None, "hash_generated_at": None, "diff": None, "record": None}
    busy_counter = {"n": 0}

    busy_bar = ft.ProgressBar(
        value=None, color=t.BRAND, bgcolor="transparent", bar_height=3, visible=False
    )

    def set_busy(active: bool) -> None:
        busy_counter["n"] = max(busy_counter["n"] + (1 if active else -1), 0)
        busy_bar.visible = busy_counter["n"] > 0

    def notify(message: str, kind: str = "info") -> None:
        color = {"ok": t.OK, "bad": t.BAD, "warn": t.WARN}.get(kind, t.BRAND)
        icon = {
            "ok": ft.Icons.CHECK_CIRCLE,
            "bad": ft.Icons.ERROR_OUTLINE,
            "warn": ft.Icons.WARNING_AMBER,
        }.get(kind, ft.Icons.INFO_OUTLINE)
        page.show_dialog(
            ft.SnackBar(
                content=ft.Row(
                    [ft.Icon(icon, color=color, size=20), ft.Text(message, color=t.TEXT, expand=True)],
                    spacing=10,
                ),
                bgcolor=t.SURFACE_ALT,
                duration=5000,
            )
        )

    async def copy_text(value: str) -> None:
        await clipboard.set(value)
        notify("Copiado al portapapeles", "ok")
        page.update()

    # ------------------------------------------------------------------ #
    # Vista ARCHIVO — controles
    # ------------------------------------------------------------------ #
    file_title = ft.Text("Ningún archivo seleccionado", size=18, weight=ft.FontWeight.BOLD, color=t.TEXT)
    file_subtitle = ft.Text("Libros de Excel (.xlsx, .xlsm)", size=13, color=t.MUTED)

    verification_version = ft.Dropdown(label="Versión a comprobar", options=[], width=200, disabled=True, dense=True)
    older_version = ft.Dropdown(label="Versión base", options=[], width=160, dense=True)
    newer_version = ft.Dropdown(label="Comparar contra", options=[], width=160, dense=True)

    verify_btn = soft_button("Verificar", ft.Icons.VERIFIED_USER, disabled=True)
    register_btn = primary_button("Registrar en Hedera", ft.Icons.CLOUD_UPLOAD, visible=False)
    recheck_btn = soft_button("Recalcular", ft.Icons.REFRESH, disabled=True)
    compare_btn = soft_button("Comparar", ft.Icons.COMPARE_ARROWS, disabled=True)
    select_btn = primary_button("Seleccionar Excel", ft.Icons.FOLDER_OPEN)

    seal = SealBanner(
        actions=ft.Row(
            [verification_version, verify_btn, register_btn],
            spacing=10,
            wrap=True,
            run_spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )

    # Evidencia
    ev_version = value_text(bold=True)
    ev_registered = value_text()
    ev_size = value_text()
    ev_type = value_text()
    registered_hash = CopyField("Huella registrada en Hedera", copy_text)
    current_hash = CopyField("Huella del archivo actual", copy_text)
    match_chip_host = ft.Container()

    def set_match_chip(kind: str | None) -> None:
        if kind == "same":
            match_chip_host.content = chip("Las huellas coinciden", t.OK, ft.Icons.CHECK_CIRCLE)
        elif kind == "different":
            match_chip_host.content = chip("Las huellas son distintas", t.BAD, ft.Icons.WARNING_AMBER)
        else:
            match_chip_host.content = None

    # Registro Hedera
    hedera_context = ft.Text(
        "Selecciona un archivo para ver el registro que le corresponde.", size=12, color=t.MUTED
    )
    hedera_network = value_text(f"Hedera {HEDERA_NETWORK.title()}", bold=True)
    hedera_topic = CopyField("Topic ID", copy_text)
    hedera_topic.set(HEDERA_TOPIC_ID or None, "No configurado")
    hedera_tx = CopyField("Transaction ID", copy_text)
    hedera_consensus = value_text()
    hedera_sequence = value_text()
    hashscan_btn = soft_button("Abrir en HashScan", ft.Icons.OPEN_IN_NEW, disabled=True)

    # Historial y cambios
    history_list = ft.Column(spacing=10)
    changes_summary = ft.Text("Selecciona un archivo para detectar cambios.", size=13, color=t.MUTED)
    changes_stats = ft.Row(spacing=8, wrap=True, run_spacing=8)
    changes_list = ft.Column(spacing=0)

    def render_history_empty() -> None:
        history_list.controls = [
            empty_state(
                ft.Icons.HISTORY,
                "Sin versiones registradas",
                "Cuando registres este archivo en Hedera aparecerá aquí su historial.",
            )
        ]

    def render_history(records: list[dict]) -> None:
        if not records:
            render_history_empty()
            return
        current = state.get("hash")
        items: list[ft.Control] = []
        for record in reversed(records):
            is_current = current is not None and record["file_hash"] == current
            header: list[ft.Control] = [
                ft.Text(f"Versión {record['version']}", size=14, weight=ft.FontWeight.W_600, color=t.TEXT),
                ft.Text(local_date(record["registered_at"]), size=12, color=t.MUTED),
            ]
            if is_current:
                header.append(chip("Archivo actual", t.OK, ft.Icons.CHECK_CIRCLE))
            items.append(
                ft.Container(
                    content=ft.Row(
                        [
                            icon_badge(ft.Icons.TAG, t.BRAND, 40),
                            ft.Column(
                                [
                                    ft.Row(header, spacing=10, wrap=True, run_spacing=4),
                                    ft.Text(
                                        record["diff"].get("summary", "Versión inicial"),
                                        size=13,
                                        color=t.TEXT,
                                    ),
                                    ft.Text(
                                        f"SHA-256 {short_hash(record['file_hash'], 14, 8)}  ·  "
                                        f"Tx {record['transaction_id']}",
                                        size=11,
                                        font_family=t.MONO,
                                        color=t.MUTED,
                                        selectable=True,
                                    ),
                                ],
                                spacing=4,
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.OPEN_IN_NEW,
                                icon_size=18,
                                icon_color=t.BRAND,
                                tooltip="Abrir transacción en HashScan",
                                url=record["hashscan_url"],
                            ),
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    padding=14,
                    bgcolor=t.SURFACE_ALT,
                    border_radius=12,
                )
            )
        history_list.controls = items

    def render_changes(diff: dict | None) -> None:
        state["diff"] = diff
        changes_stats.controls = []
        changes_list.controls = []
        if diff is None:
            changes_summary.value = "Sin comparación disponible."
            return
        changes_summary.value = diff.get("summary", "")
        if "added_rows" in diff:
            changes_stats.controls = [
                chip(f"{diff.get('added_rows', 0)} agregados", t.OK, ft.Icons.ADD_CIRCLE_OUTLINE),
                chip(f"{diff.get('modified_rows', 0)} modificados", t.WARN, ft.Icons.EDIT_NOTE),
                chip(f"{diff.get('deleted_rows', 0)} eliminados", t.BAD, ft.Icons.REMOVE_CIRCLE_OUTLINE),
                chip(f"{diff.get('modified_columns', 0)} columnas afectadas", t.BRAND, ft.Icons.TABLE_CHART),
            ]
        changes = diff.get("changes", [])
        if not changes:
            changes_list.controls = [
                ft.Text("No hay diferencias de celdas para mostrar.", size=13, color=t.MUTED)
            ]
            return
        for change in changes[:MAX_CHANGES_SHOWN]:
            before = change["before"] or "(vacío)"
            after = change["after"] or "(vacío)"
            changes_list.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                ft.Text(
                                    f"{change['sheet']}!{change['cell']}",
                                    size=12,
                                    font_family=t.MONO,
                                    color=t.BRAND,
                                    no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                width=170,
                            ),
                            ft.Text(before, size=12, color=t.BAD, expand=True, selectable=True),
                            ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=t.FAINT),
                            ft.Text(after, size=12, color=t.OK, expand=True, selectable=True),
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding.symmetric(horizontal=6, vertical=8),
                    border=ft.Border.only(bottom=ft.BorderSide(1, t.BORDER)),
                )
            )
        if diff.get("truncated") or len(changes) > MAX_CHANGES_SHOWN:
            changes_list.controls.append(
                ft.Text(
                    f"Vista limitada a {MAX_CHANGES_SHOWN} cambios; el resumen incluye el total.",
                    size=12,
                    color=t.MUTED,
                    italic=True,
                )
            )

    render_history_empty()

    # Pestañas simples (Historial / Cambios)
    tab_state = {"index": 0}
    history_panel = ft.Column([history_list], spacing=10)
    changes_panel = ft.Column(
        [changes_summary, changes_stats, ft.Container(changes_list, padding=ft.Padding.only(top=6))],
        spacing=10,
        visible=False,
    )
    tab_parts: list[tuple[ft.Container, ft.Icon, ft.Text]] = []

    def select_tab(index: int) -> None:
        tab_state["index"] = index
        history_panel.visible = index == 0
        changes_panel.visible = index == 1
        for position, (button, icon, label) in enumerate(tab_parts):
            active = position == index
            button.bgcolor = t.tint(t.BRAND, 0.16) if active else None
            icon.color = label.color = t.BRAND if active else t.MUTED

    def make_tab(label_text: str, icon_name: ft.IconData, index: int) -> ft.Container:
        icon = ft.Icon(icon_name, size=16, color=t.MUTED)
        label = ft.Text(label_text, size=13, color=t.MUTED, weight=ft.FontWeight.W_600)
        button = ft.Container(
            content=ft.Row([icon, label], spacing=8, tight=True),
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            border_radius=8,
            ink=True,
            on_click=lambda _e, i=index: (select_tab(i), page.update()),
        )
        tab_parts.append((button, icon, label))
        return button

    tabs_row = ft.Row(
        [make_tab("Historial", ft.Icons.HISTORY, 0), make_tab("Cambios entre versiones", ft.Icons.COMPARE_ARROWS, 1)],
        spacing=6,
    )
    select_tab(0)

    compare_controls = ft.Row(
        [older_version, ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=t.FAINT), newer_version, compare_btn],
        spacing=10,
        wrap=True,
        run_spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    changes_panel.controls.insert(0, compare_controls)

    # ------------------------------------------------------------------ #
    # Vista ARCHIVO — lógica
    # ------------------------------------------------------------------ #
    def update_version_options(records: list[dict], preferred_hash: str | None = None) -> None:
        def options() -> list[ft.DropdownOption]:
            return [ft.DropdownOption(key=str(r["version"]), text=f"Versión {r['version']}") for r in records]

        for dropdown in (older_version, newer_version, verification_version):
            dropdown.options = options()
        preferred = next((r for r in reversed(records) if r["file_hash"] == preferred_hash), None)
        verification_version.value = (
            str(preferred["version"] if preferred else records[-1]["version"]) if records else None
        )
        verification_version.disabled = not records
        verify_btn.disabled = not records
        if len(records) >= 2:
            older_version.value = str(records[-2]["version"])
            newer_version.value = str(records[-1]["version"])
        else:
            older_version.value = str(records[0]["version"]) if records else None
            newer_version.value = str(records[0]["version"]) if records else None
        compare_btn.disabled = len(records) < 2

    def apply_hedera_card(record: dict | None, consensus_time: str | None = None, context: str | None = None) -> None:
        hedera_context.value = context or "Registro de la versión seleccionada."
        hedera_topic.set(record.get("topic_id") if record else HEDERA_TOPIC_ID or None, "No configurado")
        hedera_tx.set(record.get("transaction_id") if record else None, "Pendiente")
        hedera_consensus.value = consensus_time or "No disponible"
        hedera_sequence.value = str(record.get("sequence_number") or "—") if record else "—"
        if record and record.get("hashscan_url"):
            hashscan_btn.url = record["hashscan_url"]
            hashscan_btn.disabled = False
        else:
            hashscan_btn.url = None
            hashscan_btn.disabled = True

    async def analyze_file(file_path: str) -> None:
        state.update({"path": file_path, "hash": None, "diff": None, "record": None})
        set_busy(True)
        seal.set("busy", "Verificando archivo…", "Calculando SHA-256 y consultando el registro en Hedera.")
        register_btn.visible = False
        apply_hedera_card(None, context="Consultando el registro de la huella del archivo…")
        page.update()
        try:
            file_name = os.path.basename(file_path)
            file_hash = await asyncio.to_thread(generate_file_sha256, file_path)
            state["hash"] = file_hash
            state["hash_generated_at"] = datetime.now(timezone.utc).isoformat()
            file_size = os.path.getsize(file_path)
            await asyncio.to_thread(update_source_path, file_name, file_path)
            records = await asyncio.to_thread(get_versions, file_name)
            latest = records[-1] if records else None

            file_title.value = file_name
            file_subtitle.value = f"{human_size(file_size)}  ·  Libro de Microsoft Excel"
            ev_size.value = human_size(file_size)
            ev_type.value = Path(file_path).suffix.upper().lstrip(".") + " · Microsoft Excel"
            current_hash.set(file_hash)
            render_history(records)
            update_version_options(records, preferred_hash=file_hash)
            render_changes(None)
            recheck_btn.disabled = False

            matching = next((r for r in reversed(records) if r["file_hash"] == file_hash), None)
            if matching:
                await _show_matching(matching, file_hash)
            else:
                await _show_unmatched(latest, file_hash, file_path)
        except Exception as error:  # noqa: BLE001 - se muestra al usuario
            seal.set("bad", "No se pudo analizar el archivo", f"Revisa que sea un libro Excel válido. Detalle: {error}")
            register_btn.visible = False
            notify(f"No se pudo analizar el archivo: {error}", "bad")
        finally:
            set_busy(False)
            page.update()

    async def _show_matching(matching: dict, file_hash: str) -> None:
        remote = await asyncio.to_thread(verify_version_record, matching)
        verified = remote.get("status") == "SUCCESS" and bool(remote.get("verified"))
        state["record"] = matching
        ev_version.value = f"v{matching['version']}"
        ev_registered.value = remote.get("consensus_time") or local_date(matching["registered_at"])
        registered_hash.set(matching["file_hash"])
        set_match_chip("same")
        apply_hedera_card(
            matching,
            remote.get("consensus_time") if verified else None,
            f"Registro correspondiente a la versión v{matching['version']}.",
        )
        register_btn.visible = False
        if verified:
            seal.set(
                "ok",
                "ARCHIVO ÍNTEGRO",
                f"La huella coincide con la versión v{matching['version']} confirmada en Hedera Mirror Node.",
                digest=file_hash,
                timestamp=full_date(remote.get("consensus_timestamp") or matching["registered_at"]),
            )
        elif remote.get("status") == "SUCCESS":
            seal.set(
                "bad",
                "REGISTRO NO COINCIDE",
                remote.get("message", "El contenido del registro en Hedera no corresponde a esta versión."),
                digest=file_hash,
            )
        else:
            seal.set(
                "warn",
                "VERIFICACIÓN PENDIENTE",
                "No se pudo consultar Hedera; no se afirma que el archivo esté verificado. "
                f"{remote.get('message', '')}".strip(),
                digest=file_hash,
            )

    async def _show_unmatched(latest: dict | None, file_hash: str, file_path: str) -> None:
        if latest is None:
            ev_version.value = "v1 (pendiente)"
            ev_registered.value = "Pendiente de registro"
            registered_hash.set(None)
            set_match_chip(None)
            apply_hedera_card(None, context="Este archivo aún no tiene un registro confirmado en Hedera.")
            seal.set(
                "warn",
                "SIN REGISTRO EN HEDERA",
                "Este archivo todavía no ha sido sellado. Puedes registrar su versión inicial.",
                digest=file_hash,
            )
            render_changes(
                {"summary": "Versión inicial: no existe una versión anterior para comparar.", "changes": [], "truncated": False}
            )
            register_btn.content = "Registrar versión inicial"
            register_btn.visible = True
            register_btn.disabled = False
            return

        next_version = latest["version"] + 1
        ev_version.value = f"v{next_version} (pendiente)"
        ev_registered.value = "Pendiente de registro"
        registered_hash.set(latest["file_hash"])
        set_match_chip("different")
        seal.set(
            "bad",
            "ARCHIVO MODIFICADO",
            f"Su huella ya no coincide con la última versión sellada (v{latest['version']}). "
            "Revisa los cambios y, si son legítimos, regístralo como nueva versión.",
            digest=file_hash,
        )
        register_btn.content = f"Registrar como v{next_version}"
        register_btn.visible = True
        register_btn.disabled = False
        page.update()

        remote = await asyncio.to_thread(verify_version_record, latest)
        apply_hedera_card(
            latest,
            remote.get("consensus_time") if remote.get("verified") else None,
            f"Último sello (v{latest['version']}); el archivo actual es distinto.",
        )
        if Path(latest["snapshot_path"]).is_file():
            diff = await asyncio.to_thread(compare_workbooks, latest["snapshot_path"], file_path)
            render_changes(diff)
            select_tab(1)

    async def pick_excel_and_analyze(_=None) -> None:
        try:
            files = await file_picker.pick_files(
                allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["xlsx", "xlsm"],
            )
        except Exception as error:  # noqa: BLE001
            notify(f"No se pudo abrir el selector de archivos: {error}", "bad")
            page.update()
            return
        if not files:
            return
        if not files[0].path:
            notify("No se obtuvo una ruta local para el archivo seleccionado.", "bad")
            page.update()
            return
        navigate(1, refresh=False)
        await analyze_file(files[0].path)

    async def recheck_current_file(_=None) -> None:
        file_path = state.get("path")
        if file_path and os.path.isfile(file_path):
            await analyze_file(file_path)
        else:
            notify("Selecciona nuevamente el archivo; no se encontró la ruta local.", "warn")
            page.update()

    async def verify_selected_integrity(_=None) -> None:
        file_path = state.get("path")
        file_name = os.path.basename(file_path or "")
        set_busy(True)
        try:
            if not file_path or not os.path.isfile(file_path):
                raise ValueError("Selecciona primero un archivo Excel válido.")
            if not verification_version.value:
                raise ValueError("No hay una versión registrada para comparar.")
            record = await asyncio.to_thread(get_version, file_name, int(verification_version.value))
            if record is None:
                raise ValueError("No se encontró la versión seleccionada en el historial.")

            seal.set("busy", "Verificando archivo…", f"Comprobando la versión v{record['version']} en Hedera.")
            registered_hash.set(record["file_hash"])
            ev_version.value = f"v{record['version']}"
            ev_registered.value = local_date(record["registered_at"])
            apply_hedera_card(record, context=f"Registro de la versión v{record['version']}.")
            page.update()

            digest = await asyncio.to_thread(generate_file_sha256, file_path)
            state["hash"] = digest
            state["hash_generated_at"] = datetime.now(timezone.utc).isoformat()
            current_hash.set(digest)
            remote = await asyncio.to_thread(verify_version_record, record)
            if remote.get("consensus_time"):
                ev_registered.value = remote["consensus_time"]
            verified_remote = remote.get("status") == "SUCCESS" and bool(remote.get("verified"))
            apply_hedera_card(
                record,
                remote.get("consensus_time") if verified_remote else None,
                f"Registro de la versión v{record['version']}.",
            )
            same = digest == record["file_hash"]
            set_match_chip("same" if same else "different")

            if verified_remote and same:
                seal.set(
                    "ok",
                    "ARCHIVO ÍNTEGRO",
                    f"El archivo coincide con la huella registrada en Hedera para la versión v{record['version']}.",
                    digest=digest,
                    timestamp=full_date(remote.get("consensus_timestamp") or record["registered_at"]),
                )
            elif verified_remote:
                seal.set(
                    "bad",
                    "ARCHIVO MODIFICADO",
                    f"El contenido actual no coincide con la versión v{record['version']} registrada en Hedera.",
                    digest=digest,
                )
            elif remote.get("status") == "SUCCESS":
                seal.set(
                    "bad",
                    "REGISTRO NO COINCIDE",
                    "La evidencia consultada en Hedera no corresponde a la versión elegida.",
                    digest=digest,
                )
            else:
                seal.set(
                    "warn",
                    "VERIFICACIÓN NO DISPONIBLE",
                    f"Mirror Node no respondió; el estado queda sin confirmar. {remote.get('message', '')}".strip(),
                    digest=digest,
                )
        except Exception as error:  # noqa: BLE001
            seal.set("bad", "NO SE PUDO VERIFICAR", str(error))
            notify(str(error), "bad")
        finally:
            set_busy(False)
            page.update()

    async def register_current_version(_=None) -> None:
        file_path = state["path"]
        file_hash = state["hash"]
        if not file_path or not file_hash:
            return
        register_btn.disabled = True
        set_busy(True)
        seal.set("busy", "Registrando en Hedera…", "Enviando la huella y los metadatos a Hedera Consensus Service.", digest=file_hash)
        page.update()
        try:
            file_name = os.path.basename(file_path)
            latest = await asyncio.to_thread(get_latest, file_name)
            if latest and latest["file_hash"] == file_hash:
                notify("Esta huella ya tiene una versión registrada; no se creó un duplicado.", "warn")
                return

            diff = state.get("diff") or {
                "summary": "Versión inicial: no existe una versión anterior.",
                "modified_rows": 0,
                "added_rows": 0,
                "deleted_rows": 0,
                "modified_columns": 0,
                "changed_cells": 0,
            }
            next_version = latest["version"] + 1 if latest else 1
            metadata = {
                "version": next_version,
                "parent_hash": latest["file_hash"] if latest else None,
                "file_size": os.path.getsize(file_path),
                "file_type": Path(file_path).suffix.lower(),
                "change_summary": diff.get("summary", "Versión inicial"),
            }
            response = await asyncio.to_thread(send_hash_to_hedera, file_name, file_hash, metadata)
            if response.get("status") != "SUCCESS":
                notify(f"No se pudo registrar en Hedera: {response.get('message', 'error desconocido')}", "bad")
                return

            try:
                await asyncio.to_thread(
                    register_version,
                    file_path=file_path,
                    file_name=file_name,
                    file_hash=file_hash,
                    file_size=os.path.getsize(file_path),
                    file_type=metadata["file_type"],
                    hash_generated_at=state.get("hash_generated_at"),
                    topic_id=response["topic_id"],
                    transaction_id=response["transaction_id"],
                    sequence_number=response.get("sequence_number"),
                    hashscan_url=response["hashscan_url"],
                    diff=diff,
                )
            except Exception as error:  # noqa: BLE001
                apply_hedera_card(
                    {
                        "topic_id": response["topic_id"],
                        "transaction_id": response["transaction_id"],
                        "sequence_number": response.get("sequence_number"),
                        "hashscan_url": response.get("hashscan_url"),
                    },
                    context="Hedera confirmó la transacción; el historial local requiere atención.",
                )
                notify(
                    "Hedera confirmó el registro, pero no se pudo guardar el historial local. "
                    f"Conserva este Tx ID: {response['transaction_id']} ({error})",
                    "bad",
                )
                return
            await analyze_file(file_path)
            notify(f"Versión v{next_version} de {file_name} registrada y confirmada en Hedera.", "ok")
        except Exception as error:  # noqa: BLE001
            notify(f"Error al registrar la versión: {error}", "bad")
        finally:
            set_busy(False)
            register_btn.disabled = False
            page.update()

    async def compare_selected_versions(_=None) -> None:
        try:
            file_name = os.path.basename(state["path"] or "")
            old_number = int(older_version.value or "0")
            new_number = int(newer_version.value or "0")
            if old_number == new_number:
                notify("Selecciona dos versiones diferentes para comparar.", "warn")
                page.update()
                return
            set_busy(True)
            page.update()
            old_record, new_record = await asyncio.gather(
                asyncio.to_thread(get_version, file_name, old_number),
                asyncio.to_thread(get_version, file_name, new_number),
            )
            if not old_record or not new_record:
                raise ValueError("No se encontraron los snapshots locales de ambas versiones.")
            diff = await asyncio.to_thread(
                compare_workbooks, old_record["snapshot_path"], new_record["snapshot_path"]
            )
            render_changes(diff)
            select_tab(1)
        except Exception as error:  # noqa: BLE001
            notify(f"No se pudieron comparar las versiones: {error}", "bad")
        finally:
            set_busy(False)
            page.update()

    select_btn.on_click = pick_excel_and_analyze
    recheck_btn.on_click = recheck_current_file
    verify_btn.on_click = verify_selected_integrity
    register_btn.on_click = register_current_version
    compare_btn.on_click = compare_selected_versions

    file_header = card(
        ft.Row(
            [
                icon_badge(ft.Icons.TABLE_CHART, t.BRAND, 52),
                ft.Column([file_title, file_subtitle], spacing=3, expand=True),
                ft.Row([recheck_btn, select_btn], spacing=10, wrap=True),
            ],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )

    evidence_card = card(
        ft.Column(
            [
                section_title("Evidencia del archivo", ft.Icons.FACT_CHECK),
                kv_row("Versión", ev_version),
                kv_row("Fecha de sello", ev_registered),
                kv_row("Tamaño", ev_size),
                kv_row("Tipo", ev_type),
                ft.Divider(color=t.BORDER, height=18),
                registered_hash.control,
                current_hash.control,
                match_chip_host,
            ],
            spacing=10,
        ),
        col={"xs": 12, "lg": 6},
    )
    hedera_card = card(
        ft.Column(
            [
                section_title("Registro en Hedera", ft.Icons.LAN),
                hedera_context,
                kv_row("Red", hedera_network),
                hedera_topic.control,
                hedera_tx.control,
                kv_row("Consenso", hedera_consensus),
                kv_row("Secuencia", hedera_sequence),
                ft.Container(hashscan_btn, padding=ft.Padding.only(top=4)),
            ],
            spacing=10,
        ),
        col={"xs": 12, "lg": 6},
    )

    file_view = ft.Column(
        [
            file_header,
            seal.control,
            ft.ResponsiveRow([evidence_card, hedera_card], spacing=16, run_spacing=16),
            card(ft.Column([tabs_row, history_panel, changes_panel], spacing=14)),
            ft.Text(
                "Hedera protege el registro de la huella; no almacena ni vuelve inmutable el archivo original.",
                size=12,
                color=t.FAINT,
                italic=True,
            ),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # ------------------------------------------------------------------ #
    # Vista PANEL
    # ------------------------------------------------------------------ #
    kpi_files = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=t.TEXT)
    kpi_versions = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=t.TEXT)
    kpi_intact = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=t.OK)
    kpi_modified = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=t.BAD)
    kpi_unknown = ft.Text("0", size=28, weight=ft.FontWeight.BOLD, color=t.WARN)
    kpi_last = ft.Text("—", size=17, weight=ft.FontWeight.BOLD, color=t.TEXT)
    dashboard_status = ft.Text("", size=12, color=t.MUTED)
    dashboard_rows = ft.Column(spacing=0)

    def go_pick(_=None):
        page.run_task(pick_excel_and_analyze)

    async def open_dashboard_file(event) -> None:
        source_path = getattr(event.control, "data", None)
        if source_path and os.path.isfile(source_path):
            navigate(1, refresh=False)
            await analyze_file(source_path)
        else:
            notify("El archivo original no está en su última ruta local. Selecciónalo desde la pestaña Archivo.", "warn")
            page.update()

    def dashboard_row(record: dict, status: str, remote: dict | None) -> ft.Container:
        label, color, icon = STATUS_STYLE[status]
        stamp = (remote or {}).get("consensus_time") or local_date(record["registered_at"])
        available = bool(record.get("source_path") and os.path.isfile(record["source_path"]))
        return ft.Container(
            content=ft.Row(
                [
                    icon_badge(ft.Icons.TABLE_CHART, t.BRAND, 40),
                    ft.Column(
                        [
                            ft.Text(
                                record["file_name"],
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=t.TEXT,
                                no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                f"v{record['version']}  ·  {stamp}  ·  {human_size(record['file_size'])}  ·  "
                                f"SHA-256 {short_hash(record['file_hash'], 8, 6)}",
                                size=12,
                                color=t.MUTED,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    chip(label, color, icon),
                    ft.IconButton(
                        icon=ft.Icons.OPEN_IN_NEW,
                        icon_size=18,
                        icon_color=t.BRAND if available else t.FAINT,
                        tooltip="Abrir en Archivo" if available else "Original no disponible localmente",
                        data=record.get("source_path"),
                        on_click=open_dashboard_file,
                        disabled=not available,
                    ),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=6, vertical=12),
            border=ft.Border.only(bottom=ft.BorderSide(1, t.BORDER)),
        )

    async def refresh_dashboard(_=None) -> None:
        set_busy(True)
        dashboard_status.value = "Revisando huellas locales y registros confirmados en Hedera…"
        page.update()
        try:
            data = await asyncio.to_thread(get_dashboard_data, 100000)
            latest_records = data["files"]
            semaphore = asyncio.Semaphore(5)

            async def classify(record: dict) -> tuple[str, dict | None]:
                async with semaphore:
                    source_path = record.get("source_path")
                    if not source_path or not os.path.isfile(source_path):
                        return "unknown", None
                    try:
                        current = await asyncio.to_thread(generate_file_sha256, source_path)
                    except OSError:
                        return "unknown", None
                    if current != record["file_hash"]:
                        return "modified", None
                    remote = await asyncio.to_thread(verify_version_record, record)
                if remote.get("status") == "SUCCESS" and remote.get("verified"):
                    return "intact", remote
                return "unknown", remote

            outcomes = await asyncio.gather(*(classify(r) for r in latest_records))
            intact = sum(s == "intact" for s, _ in outcomes)
            modified = sum(s == "modified" for s, _ in outcomes)
            unknown = max(data["file_count"] - intact - modified, 0)

            kpi_files.value = str(data["file_count"])
            kpi_versions.value = str(data["version_count"])
            kpi_intact.value = str(intact)
            kpi_modified.value = str(modified)
            kpi_unknown.value = str(unknown)
            kpi_last.value = local_date(latest_records[0]["registered_at"]) if latest_records else "—"

            if not latest_records:
                dashboard_rows.controls = [
                    empty_state(
                        ft.Icons.UPLOAD_FILE,
                        "Todavía no hay archivos registrados",
                        "Selecciona un libro de Excel para calcular su huella y sellarla en Hedera.",
                        primary_button("Registrar primer archivo", ft.Icons.FOLDER_OPEN, on_click=go_pick),
                    )
                ]
            else:
                dashboard_rows.controls = [
                    dashboard_row(record, status, remote)
                    for record, (status, remote) in zip(latest_records, outcomes)
                ]
            dashboard_status.value = (
                f"{len(latest_records)} archivo(s) analizado(s). "
                "Si el original no está disponible en su ruta local, queda como «Sin verificar»."
            )
        except Exception as error:  # noqa: BLE001
            dashboard_status.value = f"No se pudo actualizar el panel: {error}"
        finally:
            set_busy(False)
            page.update()

    dashboard_view = ft.Column(
        [
            ft.Row(
                [
                    ft.Container(expand=True),
                    soft_button("Actualizar", ft.Icons.REFRESH, on_click=lambda _e: page.run_task(refresh_dashboard)),
                    primary_button("Registrar archivo", ft.Icons.CLOUD_UPLOAD, on_click=go_pick),
                ],
                spacing=10,
            ),
            ft.ResponsiveRow(
                [
                    stat_card("Archivos", kpi_files, ft.Icons.INSERT_DRIVE_FILE, t.BRAND),
                    stat_card("Versiones", kpi_versions, ft.Icons.HISTORY, t.BRAND),
                    stat_card("Íntegros", kpi_intact, ft.Icons.CHECK_CIRCLE, t.OK),
                    stat_card("Modificados", kpi_modified, ft.Icons.WARNING_AMBER, t.BAD),
                    stat_card("Sin verificar", kpi_unknown, ft.Icons.HELP_OUTLINE, t.WARN),
                    stat_card("Último sello", kpi_last, ft.Icons.SCHEDULE, t.MUTED),
                ],
                spacing=14,
                run_spacing=14,
            ),
            card(
                ft.Column(
                    [section_title("Archivos registrados", ft.Icons.FOLDER_OPEN), dashboard_rows, dashboard_status],
                    spacing=10,
                )
            ),
        ],
        spacing=18,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # ------------------------------------------------------------------ #
    # Vista AUDITORÍA
    # ------------------------------------------------------------------ #
    audit_file = ft.Dropdown(label="Archivo", options=[], width=420, dense=True)
    audit_status = ft.Text("", size=12, color=t.MUTED)
    audit_timeline = ft.Column(spacing=0)

    async def render_audit(file_name: str | None) -> None:
        audit_timeline.controls = []
        if not file_name:
            audit_status.value = ""
            audit_timeline.controls = [
                empty_state(
                    ft.Icons.TIMELINE,
                    "Sin archivos para auditar",
                    "Registra un archivo en Hedera y su línea de tiempo aparecerá aquí.",
                )
            ]
            page.update()
            return
        set_busy(True)
        audit_status.value = "Consultando versiones y timestamps de consenso en Hedera…"
        page.update()
        try:
            records = await asyncio.to_thread(get_versions, file_name)
            remote_results = await asyncio.gather(
                *(asyncio.to_thread(verify_version_record, r) for r in records)
            )
            events: list[dict] = []
            for record, remote in zip(records, remote_results):
                label = f"{file_name} · v{record['version']}"
                registered_at = record.get("registered_at")
                consensus_at = remote.get("consensus_timestamp") if remote.get("verified") else None
                events.append({"at": record.get("hash_generated_at"), "fallback_at": registered_at, "title": "SHA-256 generado",
                               "detail": f"{short_hash(record['file_hash'], 16, 8)}  ·  {label}", "order": 0, "kind": "hash"})
                events.append({"at": registered_at, "fallback_at": registered_at, "title": "Archivo registrado",
                               "detail": label, "order": 1, "kind": "reg"})
                events.append({"at": consensus_at, "fallback_at": registered_at,
                               "title": "Registro confirmado en Hedera",
                               "detail": (f"Consensus timestamp  ·  Tx {record['transaction_id']}" if consensus_at
                                          else f"Timestamp de consenso no disponible  ·  Tx {record['transaction_id']}"),
                               "order": 2, "kind": "ok" if consensus_at else "warn"})

            grouped: dict[str, list[dict]] = {}
            for event in events:
                event_dt = event_datetime(event["at"])
                display_dt = event_dt or event_datetime(event["fallback_at"])
                if display_dt is None:
                    continue
                event["display_dt"] = display_dt
                event["time_label"] = event_dt.strftime("%H:%M:%S") if event_dt else "—"
                grouped.setdefault(spanish_day(display_dt), []).append(event)

            for day in sorted(grouped, key=lambda d: max(e["display_dt"] for e in grouped[d]), reverse=True):
                audit_timeline.controls.append(
                    ft.Container(
                        chip(day, t.BRAND, ft.Icons.SCHEDULE),
                        padding=ft.Padding.only(top=16, bottom=8),
                    )
                )
                day_events = sorted(grouped[day], key=lambda e: (e["display_dt"], e["order"]))
                for index, event in enumerate(day_events):
                    dot_color = {"ok": t.OK, "warn": t.WARN}.get(event["kind"], t.BRAND)
                    last = index == len(day_events) - 1
                    audit_timeline.controls.append(
                        ft.Row(
                            [
                                ft.Container(
                                    ft.Text(event["time_label"], size=12, font_family=t.MONO, color=t.MUTED),
                                    width=76,
                                    alignment=ft.Alignment(1, -1),
                                    padding=ft.Padding.only(top=2),
                                ),
                                ft.Column(
                                    [
                                        ft.Icon(
                                            ft.Icons.CHECK_CIRCLE if event["kind"] == "ok" else ft.Icons.CIRCLE,
                                            size=14,
                                            color=dot_color,
                                        ),
                                        ft.Container(width=2, height=34, bgcolor=t.BORDER if not last else None),
                                    ],
                                    spacing=2,
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    width=20,
                                ),
                                ft.Column(
                                    [
                                        ft.Text(event["title"], size=13, weight=ft.FontWeight.W_600, color=t.TEXT),
                                        ft.Text(event["detail"], size=12, color=t.MUTED, selectable=True),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        )
                    )
            audit_status.value = f"{len(records)} versión(es) auditada(s) · hora de consenso tomada de Hedera Mirror Node."
        except Exception as error:  # noqa: BLE001
            audit_status.value = f"No se pudo cargar la auditoría: {error}"
        finally:
            set_busy(False)
            page.update()

    async def load_audit(_=None) -> None:
        data = await asyncio.to_thread(get_dashboard_data, 100000)
        names = [item["file_name"] for item in data["files"]]
        audit_file.options = [ft.DropdownOption(key=name, text=name) for name in names]
        current = os.path.basename(state["path"]) if state.get("path") else None
        if audit_file.value not in names:
            audit_file.value = current if current in names else (names[0] if names else None)
        await render_audit(audit_file.value)

    async def on_audit_file_change(event) -> None:
        await render_audit(event.control.value)

    audit_file.on_select = on_audit_file_change

    audit_view = ft.Column(
        [
            card(
                ft.Column(
                    [
                        ft.Row(
                            [section_title("Línea de tiempo", ft.Icons.TIMELINE), audit_file],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            wrap=True,
                            run_spacing=10,
                        ),
                        audit_status,
                        audit_timeline,
                    ],
                    spacing=10,
                )
            )
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # ------------------------------------------------------------------ #
    # Vista VERIFICAR (pública, sin historial local)
    # ------------------------------------------------------------------ #
    public_seal = SealBanner()
    public_seal.set(
        "idle",
        "LISTO PARA VERIFICAR",
        "Selecciona cualquier archivo. Se calculará su huella y se buscará en el Topic de Hedera.",
    )
    public_details = ft.Column(spacing=10, visible=False)
    public_hash = CopyField("Huella SHA-256 del archivo", copy_text)

    async def verify_any_file(_=None) -> None:
        try:
            files = await file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.ANY)
        except Exception as error:  # noqa: BLE001
            notify(f"No se pudo abrir el selector de archivos: {error}", "bad")
            page.update()
            return
        if not files:
            return
        if not files[0].path:
            notify("No se obtuvo una ruta local para el archivo seleccionado.", "bad")
            page.update()
            return
        path = files[0].path
        set_busy(True)
        public_details.visible = False
        public_seal.set("busy", "Buscando en Hedera…", f"Calculando la huella de {os.path.basename(path)} y consultando el Topic.")
        page.update()
        try:
            digest = await asyncio.to_thread(generate_file_sha256, path)
            public_hash.set(digest)
            result = await asyncio.to_thread(verify_hash, digest)
            if result.get("status") == "SUCCESS" and result.get("found"):
                metadata = result.get("metadata", {}) or {}
                public_seal.set(
                    "ok",
                    "ARCHIVO AUTÉNTICO",
                    "Esta huella fue registrada en Hedera: el contenido es idéntico al que se selló.",
                    digest=digest,
                    timestamp=result.get("consensus_time"),
                )
                rows: list[ft.Control] = [
                    kv_row("Nombre sellado", value_text(result.get("file_name") or "—")),
                    kv_row("Versión", value_text(f"v{metadata['version']}" if metadata.get("version") else "—")),
                    kv_row("Cambios", value_text(metadata.get("change_summary") or "—")),
                    kv_row("Secuencia", value_text(str(result.get("sequence_number") or "—"))),
                ]
                if result.get("hashscan_url"):
                    rows.append(soft_button("Ver Topic en HashScan", ft.Icons.OPEN_IN_NEW, url=result["hashscan_url"]))
                public_details.controls = rows
                public_details.visible = True
            elif result.get("status") == "SUCCESS":
                public_seal.set(
                    "bad",
                    "SIN COINCIDENCIAS",
                    "Esta huella no aparece en el Topic: el archivo fue alterado o nunca se registró.",
                    digest=digest,
                )
            else:
                public_seal.set(
                    "warn",
                    "VERIFICACIÓN INCOMPLETA",
                    result.get("message", "No se pudo completar la búsqueda en Hedera."),
                    digest=digest,
                )
        except Exception as error:  # noqa: BLE001
            public_seal.set("bad", "NO SE PUDO VERIFICAR", str(error))
            notify(str(error), "bad")
        finally:
            set_busy(False)
            page.update()

    public_view = ft.Column(
        [
            card(
                ft.Row(
                    [
                        icon_badge(ft.Icons.VERIFIED, t.OK, 52),
                        ft.Column(
                            [
                                ft.Text("¿Este archivo es el original?", size=17, weight=ft.FontWeight.BOLD, color=t.TEXT),
                                ft.Text(
                                    "No necesitas el historial de esta aplicación: la prueba está en la red pública de Hedera.",
                                    size=13,
                                    color=t.MUTED,
                                ),
                            ],
                            spacing=3,
                            expand=True,
                        ),
                        primary_button("Seleccionar archivo", ft.Icons.FOLDER_OPEN, on_click=verify_any_file),
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            ),
            public_seal.control,
            card(ft.Column([public_hash.control, public_details], spacing=12)),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # ------------------------------------------------------------------ #
    # Estructura general: barra lateral + contenido
    # ------------------------------------------------------------------ #
    views = (dashboard_view, file_view, audit_view, public_view)
    header_title = ft.Text(PAGE_TITLES[0][0], size=22, weight=ft.FontWeight.BOLD, color=t.TEXT)
    header_subtitle = ft.Text(PAGE_TITLES[0][1], size=13, color=t.MUTED)
    host = ft.Container(content=dashboard_view, expand=True, padding=ft.Padding.only(top=8))

    def navigate(index: int, *, refresh: bool = True) -> None:
        rail.selected_index = index
        host.content = views[index]
        header_title.value, header_subtitle.value = PAGE_TITLES[index]
        page.update()
        if refresh and index == 0:
            page.run_task(refresh_dashboard)
        elif refresh and index == 2:
            page.run_task(load_audit)

    def on_nav_change(event) -> None:
        navigate(int(event.control.selected_index))

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=88,
        bgcolor=t.SURFACE,
        indicator_color=t.tint(t.BRAND, 0.18),
        leading=ft.Container(
            content=ft.Column(
                [
                    icon_badge(ft.Icons.SHIELD, t.BRAND, 44),
                    ft.Text("DataOil\nTrace", size=12, weight=ft.FontWeight.BOLD, color=t.TEXT, text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            padding=ft.Padding.only(top=18, bottom=22),
        ),
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.DASHBOARD_OUTLINED, selected_icon=ft.Icons.DASHBOARD, label="Panel"),
            ft.NavigationRailDestination(icon=ft.Icons.DESCRIPTION_OUTLINED, selected_icon=ft.Icons.DESCRIPTION, label="Archivo"),
            ft.NavigationRailDestination(icon=ft.Icons.TIMELINE, selected_icon=ft.Icons.TIMELINE, label="Auditoría"),
            ft.NavigationRailDestination(icon=ft.Icons.VERIFIED_OUTLINED, selected_icon=ft.Icons.VERIFIED, label="Verificar"),
        ],
        on_change=on_nav_change,
    )

    network_chip = chip(
        f"Hedera {HEDERA_NETWORK.title()}  ·  {HEDERA_TOPIC_ID or 'Topic sin configurar'}",
        t.BRAND if HEDERA_TOPIC_ID else t.WARN,
        ft.Icons.LAN,
    )

    content_area = ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=30, vertical=22),
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Column([header_title, header_subtitle], spacing=2, expand=True),
                        network_chip,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                busy_bar,
                host,
            ],
            spacing=10,
            expand=True,
        ),
    )

    page.add(
        ft.Row(
            [rail, ft.VerticalDivider(width=1, color=t.BORDER), content_area],
            expand=True,
            spacing=0,
        )
    )
    page.update()
    page.run_task(refresh_dashboard)

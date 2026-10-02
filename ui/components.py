"""Bloques visuales reutilizables. No contienen lógica de negocio."""
from __future__ import annotations

from typing import Awaitable, Callable

import flet as ft

from ui import theme as t
from ui.utils import short_hash

CENTER = ft.Alignment(0, 0)


# --------------------------------------------------------------------------- #
# Piezas simples
# --------------------------------------------------------------------------- #
def caption(text: str) -> ft.Text:
    return ft.Text(text, size=11, color=t.MUTED, weight=ft.FontWeight.W_500)


def card(
    content: ft.Control,
    *,
    padding: int | ft.Padding = 20,
    accent: str | None = None,
    **kwargs,
) -> ft.Container:
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=t.tint(accent, 0.07) if accent else t.SURFACE,
        border=ft.Border.all(1, t.tint(accent, 0.5) if accent else t.BORDER),
        border_radius=14,
        **kwargs,
    )


def section_title(text: str, icon: ft.IconData | None = None) -> ft.Row:
    controls: list[ft.Control] = []
    if icon is not None:
        controls.append(ft.Icon(icon, size=18, color=t.BRAND))
    controls.append(ft.Text(text, size=15, weight=ft.FontWeight.W_600, color=t.TEXT))
    return ft.Row(controls, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def icon_badge(icon: ft.IconData, color: str = t.BRAND, size: int = 44) -> ft.Container:
    return ft.Container(
        content=ft.Icon(icon, size=size * 0.5, color=color),
        width=size,
        height=size,
        border_radius=size / 3,
        bgcolor=t.tint(color, 0.14),
        alignment=CENTER,
    )


def chip(label: str, color: str, icon: ft.IconData | None = None) -> ft.Container:
    row: list[ft.Control] = []
    if icon is not None:
        row.append(ft.Icon(icon, size=14, color=color))
    row.append(ft.Text(label, size=12, color=color, weight=ft.FontWeight.W_600))
    return ft.Container(
        content=ft.Row(row, spacing=5, tight=True),
        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        border_radius=999,
        bgcolor=t.tint(color, 0.14),
    )


def kv_row(label: str, value: ft.Control, label_width: int = 120) -> ft.Row:
    return ft.Row(
        [
            ft.Container(caption(label), width=label_width),
            ft.Container(value, expand=True),
        ],
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def value_text(text: str = "—", *, mono: bool = False, bold: bool = False) -> ft.Text:
    return ft.Text(
        text,
        size=13,
        color=t.TEXT,
        selectable=True,
        font_family=t.MONO if mono else None,
        weight=ft.FontWeight.W_600 if bold else None,
    )


def empty_state(icon: ft.IconData, title: str, text: str, action: ft.Control | None = None) -> ft.Container:
    column: list[ft.Control] = [
        ft.Icon(icon, size=40, color=t.FAINT),
        ft.Text(title, size=15, weight=ft.FontWeight.W_600, color=t.TEXT),
        ft.Text(text, size=13, color=t.MUTED, text_align=ft.TextAlign.CENTER),
    ]
    if action is not None:
        column.append(action)
    return ft.Container(
        content=ft.Column(
            column,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        padding=ft.Padding.symmetric(horizontal=20, vertical=28),
        alignment=CENTER,
    )


def primary_button(text: str, icon: ft.IconData | None = None, **kwargs) -> ft.FilledButton:
    return ft.FilledButton(
        text,
        icon=icon,
        style=ft.ButtonStyle(
            bgcolor=t.BRAND,
            color="#04141F",
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(horizontal=18, vertical=14),
        ),
        **kwargs,
    )


def soft_button(text: str, icon: ft.IconData | None = None, **kwargs) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        text,
        icon=icon,
        style=ft.ButtonStyle(
            color=t.TEXT,
            side=ft.BorderSide(1, t.BORDER),
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        ),
        **kwargs,
    )


def stat_card(label: str, value: ft.Text, icon: ft.IconData, color: str) -> ft.Container:
    return ft.Container(
        col={"xs": 6, "md": 4, "xl": 2},
        content=ft.Column(
            [
                ft.Row(
                    [icon_badge(icon, color, 34), caption(label.upper())],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                value,
            ],
            spacing=10,
        ),
        padding=16,
        bgcolor=t.SURFACE,
        border=ft.Border.all(1, t.BORDER),
        border_radius=14,
    )


# --------------------------------------------------------------------------- #
# Componentes con estado
# --------------------------------------------------------------------------- #
class CopyField:
    """Etiqueta + valor monoespaciado + botón de copiar."""

    def __init__(
        self,
        label: str,
        copy_fn: Callable[[str], Awaitable[None]],
        *,
        mono: bool = True,
        shorten: bool = False,
    ):
        self._copy_fn = copy_fn
        self._value: str | None = None
        self._shorten = shorten
        self.text = ft.Text(
            "—",
            size=12 if mono else 13,
            color=t.TEXT,
            selectable=True,
            font_family=t.MONO if mono else None,
            expand=True,
        )
        self.button = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            icon_size=15,
            icon_color=t.MUTED,
            tooltip="Copiar",
            visible=False,
            on_click=self._on_click,
        )
        self.control = ft.Column(
            [
                caption(label),
                ft.Row(
                    [self.text, self.button],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=2,
        )

    async def _on_click(self, _):
        if self._value:
            await self._copy_fn(self._value)

    def set(self, value: str | None, placeholder: str = "—") -> None:
        self._value = value or None
        if value:
            self.text.value = short_hash(value, 14, 8) if self._shorten else value
        else:
            self.text.value = placeholder
        self.button.visible = bool(value)


# Tipo de estado -> (color, icono)
SEAL_KINDS: dict[str, tuple[str, ft.IconData]] = {
    "idle": (t.MUTED, ft.Icons.SHIELD_OUTLINED),
    "busy": (t.BRAND, ft.Icons.SYNC),
    "ok": (t.OK, ft.Icons.VERIFIED_USER),
    "bad": (t.BAD, ft.Icons.GPP_BAD),
    "warn": (t.WARN, ft.Icons.GPP_MAYBE),
}


class SealBanner:
    """Banner grande con el veredicto de integridad (el elemento central de la app)."""

    def __init__(self, actions: ft.Control | None = None):
        self.icon = ft.Icon(ft.Icons.SHIELD_OUTLINED, size=32, color=t.MUTED)
        self.icon_box = ft.Container(
            content=self.icon,
            width=64,
            height=64,
            border_radius=32,
            alignment=CENTER,
            bgcolor=t.tint(t.MUTED, 0.14),
        )
        self.title = ft.Text("SIN ARCHIVO", size=19, weight=ft.FontWeight.BOLD, color=t.MUTED)
        self.description = ft.Text("", size=13, color=t.MUTED)
        self.hash_text = ft.Text("", size=12, font_family=t.MONO, color=t.TEXT, selectable=True)
        self.time_text = ft.Text("", size=12, color=t.TEXT)
        self.hash_chip = ft.Container(
            content=ft.Row(
                [ft.Icon(ft.Icons.FINGERPRINT, size=14, color=t.MUTED), self.hash_text],
                spacing=6,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=8,
            bgcolor=t.tint("#FFFFFF", 0.05),
            visible=False,
        )
        self.time_chip = ft.Container(
            content=ft.Row(
                [ft.Icon(ft.Icons.SCHEDULE, size=14, color=t.MUTED), self.time_text],
                spacing=6,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=8,
            bgcolor=t.tint("#FFFFFF", 0.05),
            visible=False,
        )
        body: list[ft.Control] = [
            self.title,
            self.description,
            ft.Row([self.hash_chip, self.time_chip], spacing=8, wrap=True, run_spacing=6),
        ]
        if actions is not None:
            body.append(ft.Container(actions, padding=ft.Padding.only(top=6)))
        self.control = card(
            ft.Row(
                [self.icon_box, ft.Column(body, spacing=6, expand=True)],
                spacing=18,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=22,
        )
        self.set("idle", "SIN ARCHIVO", "Selecciona un libro de Excel para calcular su huella y comprobar su integridad.")

    def set(
        self,
        kind: str,
        title: str,
        description: str,
        *,
        digest: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        color, icon = SEAL_KINDS[kind]
        self.icon.icon = icon
        self.icon.color = color
        self.icon_box.bgcolor = t.tint(color, 0.16)
        self.title.value = title
        self.title.color = color
        self.description.value = description
        self.hash_text.value = short_hash(digest, 12, 8) if digest else ""
        self.hash_chip.visible = bool(digest)
        self.time_text.value = timestamp or ""
        self.time_chip.visible = bool(timestamp)
        self.control.bgcolor = t.tint(color, 0.07)
        self.control.border = ft.Border.all(1, t.tint(color, 0.5))

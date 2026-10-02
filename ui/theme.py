"""Paleta y constantes visuales de DataOilTrace."""
import flet as ft

# Superficies (modo oscuro, azul pizarra)
BG = "#0A0F16"
SURFACE = "#111922"
SURFACE_ALT = "#17212C"
BORDER = "#223041"

# Texto
TEXT = "#E7EDF4"
MUTED = "#8696AA"
FAINT = "#5B6B7E"

# Marca y estados semánticos
BRAND = "#38BDF8"
OK = "#34D399"
WARN = "#FBBF24"
BAD = "#F87171"

MONO = "monospace"


def tint(color: str, opacity: float = 0.12) -> str:
    """Color translúcido para fondos de chips, banners y botones suaves."""
    return ft.Colors.with_opacity(opacity, color)

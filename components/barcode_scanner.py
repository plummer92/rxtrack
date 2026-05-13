from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT_DIR = Path(__file__).resolve().parent / "barcode_scanner"
_barcode_scanner = components.declare_component("barcode_scanner", path=str(_COMPONENT_DIR))


def barcode_scanner(default="", key=None):
    return _barcode_scanner(default=default, key=key)

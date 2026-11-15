"""Paso 4 del diagrama: el evento se valida contra su esquema antes de guardarse."""

import json
from pathlib import Path

import pytest

from lib import esquemas

EJEMPLOS = Path(__file__).parent / "ejemplos"


@pytest.mark.parametrize("archivo", sorted(p.name for p in EJEMPLOS.glob("*.json")))
def test_ejemplos_oficiales_cumplen_su_esquema(archivo):
    evento = json.loads((EJEMPLOS / archivo).read_text(encoding="utf-8"))
    assert esquemas.errores(evento["detail-type"], evento["detail"]) == []



"""Consumidor de enrollment.created/dropped y file.scanned, con los ejemplos oficiales del catálogo."""

from pathlib import Path

from api import comun
from consumidores import eventos
from lib import inbox
from tests.conftest import ALUMNO, TENANT

EJEMPLOS = Path(__file__).parent / "ejemplos"
CURSO = "crs_01JCQRZHQT9F4WY2JYPDV546K7"
ARCHIVO = "fil_01JC8P2K48BKYRT5ATH3DZDA66"


def cuerpo(nombre: str) -> str:
    return (EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8")


def lote(*nombres):
    return {"Records": [{"messageId": f"m{i}", "body": cuerpo(n)} for i, n in enumerate(nombres)]}


def test_matricula_creada_habilita_al_alumno(aws):
    assert eventos.lambda_handler(lote("lms.courses.enrollment.created.v1"), None) == {"batchItemFailures": []}
    proy = comun.matricula(TENANT, CURSO, ALUMNO)
    assert proy["role"] == "STUDENT"
    assert proy["enrollmentId"] == "enr_01JC5F0KPBVF7HK1VV8H23JGMW"
    assert comun.matricula("upc", CURSO, ALUMNO) is None  # la matrícula es del tenant del evento


def test_retiro_borra_la_matricula(aws):
    eventos.lambda_handler(lote("lms.courses.enrollment.created.v1", "lms.courses.enrollment.dropped.v1"), None)
    assert comun.matricula(TENANT, CURSO, ALUMNO) is None


def test_created_atrasado_no_revive_una_matricula_retirada(aws):
    eventos.lambda_handler(lote("lms.courses.enrollment.dropped.v1"), None)
    creado = inbox.leer_mensaje(cuerpo("lms.courses.enrollment.created.v1"))
    assert inbox.procesar(creado, eventos.REACCIONES) == "VIEJO"
    assert comun.matricula(TENANT, CURSO, ALUMNO) is None


def test_archivo_escaneado_guarda_su_resultado(aws):
    eventos.lambda_handler(lote("lms.content.file.scanned.v1"), None)
    proy = comun.archivo(TENANT, ARCHIVO)
    assert proy["result"] == "CLEAN"
    assert proy["ownerId"] == ALUMNO


def test_duplicado_se_descarta(aws):
    evento = inbox.leer_mensaje(cuerpo("lms.content.file.scanned.v1"))
    assert inbox.procesar(evento, eventos.REACCIONES) == "PROCESADO"
    assert inbox.procesar(evento, eventos.REACCIONES) == "DUPLICADO"


def test_un_mensaje_roto_no_frena_el_lote(aws):
    registros = {"Records": [{"messageId": "roto", "body": "{no es json"},
                             {"messageId": "ok", "body": cuerpo("lms.courses.enrollment.created.v1")}]}
    assert eventos.lambda_handler(registros, None) == {"batchItemFailures": [{"itemIdentifier": "roto"}]}
    assert comun.matricula(TENANT, CURSO, ALUMNO) is not None

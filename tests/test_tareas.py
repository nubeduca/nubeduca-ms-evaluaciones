"""Rutas de tareas (EVA-01): crear, listar, ver, editar y publicar."""

import json
from pathlib import Path

from api import tareas
from consumidores import eventos as consumidor
from lib import esquemas
from tests.conftest import ALUMNO, eventos, items, peticion, token

CURSO = "crs_01JCQRZHQT9F4WY2JYPDV546K7"
OTRO_DOCENTE = "11111111-2222-4333-8444-555555555555"
EJEMPLOS = Path(__file__).parent / "ejemplos"
NUEVA = {"title": "Laboratorio 4: SQS y DLQ", "dueAt": "2026-11-20T23:59:00Z", "maxScore": 20}


def llamar(metodo, headers, body=None, curso=None, tarea=None, query=None, resource=None):
    params = {"courseId": curso} if curso else {"assignmentId": tarea}
    resp = tareas.lambda_handler(peticion(metodo, headers, body, params, query, resource), None)
    return resp["statusCode"], json.loads(resp["body"])


def crear(body=None, headers=None):
    status, cuerpo = llamar("POST", headers or token(), body or NUEVA, curso=CURSO)
    assert status == 201, cuerpo
    return cuerpo


def publicar(assignment_id, headers=None):
    return llamar("POST", headers or token(), tarea=assignment_id, resource="/assignments/{assignmentId}/publish")


def matricular_alumno():
    cuerpo = (EJEMPLOS / "lms.courses.enrollment.created.v1.json").read_text(encoding="utf-8")
    consumidor.lambda_handler({"Records": [{"messageId": "m1", "body": cuerpo}]}, None)


def test_sin_token_responde_401(aws):
    status, cuerpo = llamar("GET", {}, curso=CURSO)
    assert status == 401 and cuerpo["code"] == "NO_AUTENTICADO"


def test_crear_deja_la_tarea_en_borrador_y_no_publica_eventos(aws):
    tarea = crear()
    assert tarea["status"] == "DRAFT"
    assert tarea["courseId"] == CURSO
    assert tarea["maxScore"] == 20 and tarea["allowLate"] is False
    assert tarea["assignmentId"].startswith("asg_")
    assert eventos() == []
    assert len(items("ASSIGN#")) == 1 and len(items(f"COURSEASSIGN#{CURSO}#")) == 1


def test_solo_el_docente_crea(aws):
    for rol in ("STUDENT", "ADMIN"):
        status, cuerpo = llamar("POST", token(rol), NUEVA, curso=CURSO)
        assert status == 403 and cuerpo["code"] == "SIN_PERMISO"


def test_crear_valida_el_body(aws):
    for malo in ({**NUEVA, "title": ""}, {**NUEVA, "maxScore": 0}, {**NUEVA, "maxScore": "20"},
                 {**NUEVA, "dueAt": "mañana"}, {**NUEVA, "dueAt": "2026-11-20T23:59:00"},
                 {"title": "Sin fecha", "maxScore": 20}, {**NUEVA, "allowLate": "si"}):
        status, cuerpo = llamar("POST", token(), malo, curso=CURSO)
        assert status == 400 and cuerpo["code"] == "DATOS_INVALIDOS", malo


def test_publicar_emite_assignment_published_que_cumple_el_contrato(aws):
    tarea = crear()
    status, publicada = publicar(tarea["assignmentId"])
    assert status == 200 and publicada["status"] == "PUBLISHED"
    (evento,) = eventos()
    assert evento["_detailType"] == "lms.assessment.assignment.published.v1"
    assert evento["_source"] == "lms.assessment"
    assert evento["metadata"]["aggregateVersion"] == 2
    assert evento["metadata"]["courseId"] == CURSO
    assert evento["data"] == {"assignmentId": tarea["assignmentId"], "title": NUEVA["title"],
                              "dueAt": NUEVA["dueAt"], "maxScore": 20}
    assert esquemas.errores(evento["_detailType"], {"metadata": evento["metadata"], "data": evento["data"]}) == []


def test_publicar_dos_veces_responde_409_y_no_repite_el_evento(aws):
    tarea = crear()
    publicar(tarea["assignmentId"])
    status, cuerpo = publicar(tarea["assignmentId"])
    assert status == 409 and cuerpo["code"] == "TAREA_YA_PUBLICADA"
    assert len(eventos()) == 1


def test_solo_el_duenio_edita_y_publica(aws):
    tarea = crear()
    ajeno = token(user=OTRO_DOCENTE)
    assert llamar("PATCH", ajeno, {"title": "x"}, tarea=tarea["assignmentId"])[0] == 403
    assert publicar(tarea["assignmentId"], ajeno)[0] == 403
    assert llamar("PATCH", token("ADMIN"), {"title": "x"}, tarea=tarea["assignmentId"])[0] == 403


def test_editar_cambia_campos_y_mueve_el_indice_si_cambia_la_fecha(aws):
    tarea = crear()
    status, editada = llamar("PATCH", token(), {"title": " Lab 4 ", "dueAt": "2026-12-01T10:00:00-05:00",
                                                "allowLate": True}, tarea=tarea["assignmentId"])
    assert status == 200
    assert editada["title"] == "Lab 4" and editada["allowLate"] is True
    assert editada["dueAt"] == "2026-12-01T15:00:00Z"  # se guarda en UTC
    (indice,) = items(f"COURSEASSIGN#{CURSO}#")
    assert "2026-12-01T15:00:00Z" in indice["sk"]
    assert llamar("GET", token(), tarea=tarea["assignmentId"])[1]["title"] == "Lab 4"


def test_editar_no_acepta_maxscore_ni_body_vacio(aws):
    tarea = crear()
    for malo in ({"maxScore": 10}, {}, {"dueAt": "ayer"}):
        status, cuerpo = llamar("PATCH", token(), malo, tarea=tarea["assignmentId"])
        assert status == 400 and cuerpo["code"] == "DATOS_INVALIDOS", malo


def test_tarea_inexistente_o_de_otro_tenant_responde_404(aws):
    tarea = crear()
    assert llamar("GET", token(), tarea="asg_01JCQRZHQT9F4WY2JYPDV546K7")[0] == 404
    assert llamar("GET", token(), tarea="no-es-un-id")[0] == 404
    status, cuerpo = llamar("GET", token(tenant="upc"), tarea=tarea["assignmentId"])
    assert status == 404 and cuerpo["code"] == "TAREA_NO_ENCONTRADA"


def test_el_alumno_solo_ve_publicadas_de_cursos_donde_esta_matriculado(aws):
    borrador = crear()
    publicada = crear({**NUEVA, "title": "Publicada"})
    publicar(publicada["assignmentId"])
    alumno = token("STUDENT", user=ALUMNO)

    # Sin matrícula: ni la lista ni la tarea publicada.
    assert llamar("GET", alumno, curso=CURSO)[0] == 404
    assert llamar("GET", alumno, tarea=publicada["assignmentId"])[0] == 404

    matricular_alumno()
    status, pagina = llamar("GET", alumno, curso=CURSO)
    assert status == 200
    assert [i["assignmentId"] for i in pagina["items"]] == [publicada["assignmentId"]]
    assert llamar("GET", alumno, tarea=publicada["assignmentId"])[0] == 200
    assert llamar("GET", alumno, tarea=borrador["assignmentId"])[0] == 404


def test_listar_ordena_por_fecha_de_entrega_y_pagina(aws):
    for dia in ("2026-11-25", "2026-11-05", "2026-11-15"):
        crear({**NUEVA, "title": dia, "dueAt": f"{dia}T12:00:00Z"})
    status, pagina = llamar("GET", token(), curso=CURSO, query={"limit": "2"})
    assert status == 200
    assert [i["title"] for i in pagina["items"]] == ["2026-11-05", "2026-11-15"]
    assert pagina["nextToken"]
    _, resto = llamar("GET", token(), curso=CURSO, query={"limit": "2", "nextToken": pagina["nextToken"]})
    assert [i["title"] for i in resto["items"]] == ["2026-11-25"]
    assert resto["nextToken"] is None
    assert llamar("GET", token(), curso=CURSO, query={"limit": "0"})[0] == 400
    assert llamar("GET", token(), curso=CURSO, query={"nextToken": "basura"})[0] == 400
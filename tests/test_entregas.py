"""Rutas de entregas (EVA-01): entregar, listar, ver y calificar."""

import json
from pathlib import Path

from api import entregas, tareas
from consumidores import eventos as consumidor
from lib import esquemas
from tests.conftest import ALUMNO, DOCENTE, eventos, items, peticion, token

CURSO = "crs_01JCQRZHQT9F4WY2JYPDV546K7"
ARCHIVO = "fil_01JC8P2K48BKYRT5ATH3DZDA66"
OTRO_ARCHIVO = "fil_01JC8P2K48BKYRT5ATH3DZDA77"
OTRO_ALUMNO = "22222222-3333-4444-8555-666666666666"
OTRO_DOCENTE = "11111111-2222-4333-8444-555555555555"
EJEMPLOS = Path(__file__).parent / "ejemplos"
ALUMNO_TOKEN = token("STUDENT", user=ALUMNO)
FUTURO, PASADO = "2099-12-31T23:59:00Z", "2020-01-01T00:00:00Z"


def consumir(nombre, **cambios):
    """Procesa un ejemplo oficial del catálogo; `cambios` pisa campos de metadata o data."""
    evento = json.loads((EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8"))
    for campo, valor in cambios.items():
        seccion = "metadata" if campo in evento["detail"]["metadata"] else "data"
        evento["detail"][seccion][campo] = valor
    respuesta = consumidor.lambda_handler({"Records": [{"messageId": "m1", "body": json.dumps(evento)}]}, None)
    assert respuesta == {"batchItemFailures": []}


def tarea_publicada(due_at=FUTURO, allow_late=False):
    body = {"title": "Laboratorio 4", "dueAt": due_at, "maxScore": 20, "allowLate": allow_late}
    resp = tareas.lambda_handler(peticion("POST", token(), body, {"courseId": CURSO}), None)
    assignment_id = json.loads(resp["body"])["assignmentId"]
    tareas.lambda_handler(peticion("POST", token(), None, {"assignmentId": assignment_id},
                                   resource="/assignments/{assignmentId}/publish"), None)
    return assignment_id


def preparar(due_at=FUTURO, allow_late=False):
    """Tarea publicada + alumno matriculado + archivo CLEAN: lo mínimo para poder entregar."""
    assignment_id = tarea_publicada(due_at, allow_late)
    consumir("lms.courses.enrollment.created.v1")
    consumir("lms.content.file.scanned.v1")
    return assignment_id


def entregar(assignment_id, headers=ALUMNO_TOKEN, body=None):
    resp = entregas.lambda_handler(
        peticion("POST", headers, {"fileIds": [ARCHIVO]} if body is None else body,
                 {"assignmentId": assignment_id}), None)
    return resp["statusCode"], json.loads(resp["body"])


def listar(assignment_id, headers, query=None):
    resp = entregas.lambda_handler(peticion("GET", headers, None, {"assignmentId": assignment_id}, query), None)
    return resp["statusCode"], json.loads(resp["body"])


def ver(submission_id, headers):
    resp = entregas.lambda_handler(peticion("GET", headers, None, {"submissionId": submission_id}), None)
    return resp["statusCode"], json.loads(resp["body"])


def calificar(submission_id, body, headers=None):
    resp = entregas.lambda_handler(peticion("POST", headers or token(), body, {"submissionId": submission_id},
                                            resource="/submissions/{submissionId}/grade"), None)
    return resp["statusCode"], json.loads(resp["body"])


def del_tipo(detail_type):
    return [e for e in eventos() if e["_detailType"] == detail_type]


# --- Entregar -----------------------------------------------------------------------

def test_alumno_matriculado_entrega_y_se_emite_submission_submitted(aws):
    """El "Listo cuando" de EVA-01."""
    assignment_id = preparar()
    status, entrega = entregar(assignment_id, body={"fileIds": [ARCHIVO], "comment": "Listo"})
    assert status == 201, entrega
    assert entrega["studentId"] == ALUMNO and entrega["attempt"] == 1
    assert entrega["status"] == "SUBMITTED" and entrega["late"] is False
    assert entrega["fileIds"] == [ARCHIVO] and entrega["comment"] == "Listo"
    assert entrega["score"] is None

    (evento,) = del_tipo("lms.assessment.submission.submitted.v1")
    assert evento["_source"] == "lms.assessment"
    assert evento["metadata"]["aggregateVersion"] == 1 and evento["metadata"]["courseId"] == CURSO
    assert evento["data"] == {"submissionId": entrega["submissionId"], "assignmentId": assignment_id,
                              "studentId": ALUMNO, "attempt": 1, "fileIds": [ARCHIVO], "late": False}
    assert esquemas.errores(evento["_detailType"], {"metadata": evento["metadata"], "data": evento["data"]}) == []


def test_cada_entrega_nueva_sube_el_attempt(aws):
    assignment_id = preparar()
    assert entregar(assignment_id)[1]["attempt"] == 1
    assert entregar(assignment_id)[1]["attempt"] == 2
    assert len(del_tipo("lms.assessment.submission.submitted.v1")) == 2


def test_no_entrega_quien_no_esta_matriculado_ni_el_docente(aws):
    assignment_id = tarea_publicada()
    consumir("lms.content.file.scanned.v1")
    status, cuerpo = entregar(assignment_id)  # alumno sin matrícula: no ve la tarea
    assert status == 404 and cuerpo["code"] == "TAREA_NO_ENCONTRADA"
    assert entregar(assignment_id, token())[0] == 403
    assert items("SUBMIT#") == []


def test_alumno_retirado_ya_no_puede_entregar(aws):
    assignment_id = preparar()
    consumir("lms.courses.enrollment.dropped.v1")
    assert entregar(assignment_id)[0] == 404


def test_no_se_entrega_en_una_tarea_en_borrador(aws):
    resp = tareas.lambda_handler(peticion("POST", token(), {"title": "Borrador", "dueAt": FUTURO, "maxScore": 20},
                                          {"courseId": CURSO}), None)
    consumir("lms.courses.enrollment.created.v1")
    consumir("lms.content.file.scanned.v1")
    assert entregar(json.loads(resp["body"])["assignmentId"])[0] == 404


def test_archivo_sin_escanear_infectado_o_ajeno_responde_409(aws):
    assignment_id = tarea_publicada()
    consumir("lms.courses.enrollment.created.v1")
    status, cuerpo = entregar(assignment_id)  # file.scanned todavía no llegó
    assert status == 409 and cuerpo["code"] == "ARCHIVO_NO_VERIFICADO"
    consumir("lms.content.file.scanned.v1", result="INFECTED")
    assert entregar(assignment_id)[1]["code"] == "ARCHIVO_NO_VERIFICADO"
    consumir("lms.content.file.scanned.v1", eventId="01JC376JAJ71YFPEAF1QH3N1C3", aggregateId=OTRO_ARCHIVO,
             fileId=OTRO_ARCHIVO, ownerId=OTRO_ALUMNO)
    assert entregar(assignment_id, body={"fileIds": [OTRO_ARCHIVO]})[1]["code"] == "ARCHIVO_NO_VERIFICADO"
    assert items("SUBMIT#") == [] and del_tipo("lms.assessment.submission.submitted.v1") == []


def test_fuera_de_plazo_depende_de_allow_late(aws):
    vencida = preparar(due_at=PASADO)
    status, cuerpo = entregar(vencida)
    assert status == 409 and cuerpo["code"] == "FUERA_DE_PLAZO"
    con_tolerancia = tarea_publicada(due_at=PASADO, allow_late=True)
    status, entrega = entregar(con_tolerancia)
    assert status == 201 and entrega["late"] is True
    assert del_tipo("lms.assessment.submission.submitted.v1")[0]["data"]["late"] is True


def test_entregar_valida_el_body(aws):
    assignment_id = preparar()
    for malo in ({}, {"fileIds": []}, {"fileIds": ["no-es-un-id"]}, {"fileIds": [ARCHIVO, ARCHIVO]},
                 {"fileIds": [ARCHIVO], "comment": "x" * 2001}, {"fileIds": [ARCHIVO] * 11}):
        status, cuerpo = entregar(assignment_id, body=malo)
        assert status == 400 and cuerpo["code"] == "DATOS_INVALIDOS", malo


# --- Ver y listar -------------------------------------------------------------------

def test_el_docente_ve_todas_y_el_alumno_solo_las_suyas(aws):
    assignment_id = preparar()
    consumir("lms.courses.enrollment.created.v1", eventId="01JCN6M3S82KD5RV1ZK8T8QEKQ",
             aggregateId="enr_01JC5F0KPBVF7HK1VV8H23JGMX", userId=OTRO_ALUMNO)
    consumir("lms.content.file.scanned.v1", eventId="01JC376JAJ71YFPEAF1QH3N1C3", aggregateId=OTRO_ARCHIVO,
             fileId=OTRO_ARCHIVO, ownerId=OTRO_ALUMNO)
    mia = entregar(assignment_id)[1]
    otro = token("STUDENT", user=OTRO_ALUMNO)
    ajena = entregar(assignment_id, otro, {"fileIds": [OTRO_ARCHIVO]})[1]

    status, pagina = listar(assignment_id, token())
    assert status == 200 and pagina["nextToken"] is None
    assert {e["submissionId"] for e in pagina["items"]} == {mia["submissionId"], ajena["submissionId"]}
    assert [e["submissionId"] for e in listar(assignment_id, ALUMNO_TOKEN)[1]["items"]] == [mia["submissionId"]]

    assert ver(mia["submissionId"], ALUMNO_TOKEN)[0] == 200
    status, cuerpo = ver(ajena["submissionId"], ALUMNO_TOKEN)
    assert status == 404 and cuerpo["code"] == "ENTREGA_NO_ENCONTRADA"
    assert ver(ajena["submissionId"], token())[0] == 200
    assert ver(mia["submissionId"], token(tenant="upc"))[0] == 404
    assert ver("sbm_01JCY7SYF5X0ZHRJ7KQSVX4BP0", token())[0] == 404


def test_listar_entregas_pagina(aws):
    assignment_id = preparar()
    for _ in range(3):
        entregar(assignment_id)
    _, pagina = listar(assignment_id, token(), {"limit": "2"})
    assert len(pagina["items"]) == 2 and pagina["nextToken"]
    _, resto = listar(assignment_id, token(), {"limit": "2", "nextToken": pagina["nextToken"]})
    assert len(resto["items"]) == 1 and resto["nextToken"] is None
    assert listar(assignment_id, token(), {"nextToken": "basura"})[0] == 400
    assert listar("asg_01JCQRZHQT9F4WY2JYPDV546K7", token())[0] == 404


def test_admin_no_usa_las_rutas_de_entregas(aws):
    assignment_id = preparar()
    assert listar(assignment_id, token("ADMIN"))[0] == 403
    assert listar(assignment_id, {})[0] == 401


# --- Calificar ----------------------------------------------------------------------

def test_calificar_emite_submission_graded_que_cumple_el_contrato(aws):
    assignment_id = preparar()
    entrega = entregar(assignment_id)[1]
    status, calificada = calificar(entrega["submissionId"], {"score": 17, "feedback": "Buen trabajo"})
    assert status == 200, calificada
    assert calificada["status"] == "GRADED" and calificada["score"] == 17
    assert calificada["feedback"] == "Buen trabajo"

    (evento,) = del_tipo("lms.assessment.submission.graded.v1")
    assert evento["metadata"]["aggregateVersion"] == 2 and evento["metadata"]["courseId"] == CURSO
    assert evento["data"] == {"submissionId": entrega["submissionId"], "assignmentId": assignment_id,
                              "studentId": ALUMNO, "graderId": DOCENTE, "score": 17, "maxScore": 20}
    assert esquemas.errores(evento["_detailType"], {"metadata": evento["metadata"], "data": evento["data"]}) == []


def test_se_puede_volver_a_calificar_y_la_version_sube(aws):
    entrega = entregar(preparar())[1]
    calificar(entrega["submissionId"], {"score": 10})
    status, calificada = calificar(entrega["submissionId"], {"score": 15.5})
    assert status == 200 and calificada["score"] == 15.5
    versiones = sorted(e["metadata"]["aggregateVersion"] for e in del_tipo("lms.assessment.submission.graded.v1"))
    assert versiones == [2, 3]


def test_calificar_valida_la_nota(aws):
    entrega = entregar(preparar())[1]
    for malo in ({}, {"score": -1}, {"score": 21}, {"score": "17"}, {"score": True},
                 {"score": 10, "feedback": "x" * 5001}):
        status, cuerpo = calificar(entrega["submissionId"], malo)
        assert status == 400 and cuerpo["code"] == "DATOS_INVALIDOS", malo
    assert del_tipo("lms.assessment.submission.graded.v1") == []


def test_solo_califica_el_docente_de_la_tarea_o_uno_matriculado_en_el_curso(aws):
    entrega = entregar(preparar())[1]
    assert calificar(entrega["submissionId"], {"score": 10}, ALUMNO_TOKEN)[0] == 403
    ajeno = token(user=OTRO_DOCENTE)
    assert calificar(entrega["submissionId"], {"score": 10}, ajeno)[0] == 403
    consumir("lms.courses.enrollment.created.v1", eventId="01JCN6M3S82KD5RV1ZK8T8QEKR",
             aggregateId="enr_01JC5F0KPBVF7HK1VV8H23JGMY", userId=OTRO_DOCENTE, role="TEACHER")
    assert calificar(entrega["submissionId"], {"score": 10}, ajeno)[0] == 200
    assert calificar("sbm_01JCY7SYF5X0ZHRJ7KQSVX4BP0", {"score": 10})[0] == 404


def test_el_alumno_no_ve_la_nota_en_este_servicio(aws):
    """La nota la publica ms-calificaciones (EVA-02); aquí el alumno solo ve que ya se calificó."""
    assignment_id = preparar()
    entrega = entregar(assignment_id)[1]
    calificar(entrega["submissionId"], {"score": 17, "feedback": "Bien"})
    _, propia = ver(entrega["submissionId"], ALUMNO_TOKEN)
    assert propia["status"] == "GRADED" and propia["score"] is None and propia["feedback"] is None
    assert listar(assignment_id, ALUMNO_TOKEN)[1]["items"][0]["score"] is None
    assert ver(entrega["submissionId"], token())[1]["score"] == 17
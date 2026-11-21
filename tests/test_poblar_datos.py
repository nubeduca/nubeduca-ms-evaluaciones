"""Seed de EVA-01: 30.000 entregas (UTEC 15.000, UPC 10.000, PUCP 5.000) sin inundar el bus."""

import json

from api import comun, entregas, tareas
from lib import esquemas
from scripts import poblar_datos
from tests.conftest import items, peticion, token

TAREA_1 = f"asg_{1:026d}"
CURSO_1 = f"crs_{1:026d}"
ALUMNO_DEMO = "11111111-1111-4111-8111-111111111111"


def test_reparto_oficial():
    assert poblar_datos.REPARTO == {"utec": 15000, "upc": 10000, "pucp": 5000}
    assert sum(poblar_datos.REPARTO.values()) == 30000


def test_carga_el_reparto_sin_eventos_y_es_idempotente(aws):
    reparto = {"utec": 300, "upc": 200, "pucp": 150}
    assert poblar_datos.poblar(reparto) == reparto
    assert poblar_datos.poblar(reparto) == reparto  # correrlo de nuevo no duplica
    for tenant, cantidad in reparto.items():
        assert len(items("SUBMIT#", tenant)) == cantidad
        assert len(items("ASSIGNSUBMIT#", tenant)) == cantidad
        assert len(items("ASSIGN#", tenant)) == -(-cantidad // 100)  # 100 entregas por tarea
        assert items("EVENTO#", tenant) == []  # el seed no publica al bus


def test_las_entregas_del_seed_cumplen_el_contrato(aws):
    poblar_datos.poblar({"utec": 20, "upc": 0, "pucp": 0})
    assert {e["status"] for e in items("SUBMIT#")} == {"SUBMITTED", "GRADED"}
    for item in items("SUBMIT#"):
        entrega = comun.a_entrega(item)
        meta = {"eventId": "01JCB7FV64BFKS3ZX0NJD0BXNJ", "tenantId": "utec", "occurredAt": entrega["submittedAt"],
                "aggregateType": "submission", "aggregateId": entrega["submissionId"], "aggregateVersion": 1,
                "actorId": entrega["studentId"], "courseId": item["courseId"]}
        data = {k: entrega[k] for k in ("submissionId", "assignmentId", "studentId", "attempt", "fileIds", "late")}
        assert esquemas.errores("lms.assessment.submission.submitted.v1", {"metadata": meta, "data": data}) == []
        if entrega["status"] == "GRADED":
            assert 0 <= entrega["score"] <= poblar_datos.MAX_SCORE


def test_las_rutas_leen_los_datos_del_seed(aws):
    poblar_datos.poblar({"utec": 120, "upc": 0, "pucp": 0})
    docente = token()
    r = entregas.lambda_handler(peticion("GET", docente, None, {"assignmentId": TAREA_1}, {"limit": "100"}), None)
    pagina = json.loads(r["body"])
    assert r["statusCode"] == 200 and len(pagina["items"]) == 100

    # El alumno demo del frontend queda matriculado en el primer curso y ve su tarea.
    alumno = token("STUDENT", user=ALUMNO_DEMO)
    r = tareas.lambda_handler(peticion("GET", alumno, None, {"courseId": CURSO_1}), None)
    assert [i["assignmentId"] for i in json.loads(r["body"])["items"]] == [TAREA_1]
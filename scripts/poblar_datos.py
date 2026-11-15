"""
Seed de ms-evaluaciones (EVA-01): 30.000 entregas en la tabla principal, UTEC 15.000, UPC 10.000
y PUCP 5.000, repartidas en tareas de 100 entregas (150, 100 y 50 tareas).

    python scripts/poblar_datos.py --stage dev                 # tabla dev-t_evaluaciones
    python scripts/poblar_datos.py --stage dev --escala 0.01   # 1 %: 150/100/50 entregas para probar
    TABLA=otra-tabla python scripts/poblar_datos.py

Usa las credenciales de AWS del entorno (en nubeduca-backend: source scripts/cuenta.sh).

- NO escribe ítems EVENTO#: cargar 30.000 entregas no debe inundar el bus.
- Es idempotente: los ids son fijos, así que correrlo otra vez reescribe los mismos ítems.
- Calza con el seed de ms-cursos: la tarea k va en el curso crs_<k> y su dueño es el mismo
  docente de seed de ese curso.
- Todas las tareas quedan PUBLISHED; 70 % de las entregas GRADED y 30 % SUBMITTED.
- Matricula a los usuarios demo del frontend en el primer curso de seed (PROY#ENROLL#), para
  que puedan ver sus tareas antes de que llegue un enrollment.created real.
"""

import argparse
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import comun  # noqa: E402
from lib import tabla as t  # noqa: E402

REPARTO = {"utec": 15000, "upc": 10000, "pucp": 5000}
ENTREGAS_POR_TAREA = 100
MAX_SCORE = 20
CREADO_EN = "2026-08-17T13:00:00Z"
ENTREGADO_EN = "2026-09-10T15:00:00Z"
ESPACIO = uuid.UUID("6f1c2e8a-3b7d-4c5e-9a10-5eed00000000")  # el mismo del seed de ms-cursos
TITULOS = ("Laboratorio", "Tarea", "Proyecto", "Informe", "Práctica calificada")

# Usuarios demo del frontend (nubeduca-frontend/src/lib/demo-users.ts).
USUARIOS_DEMO = {
    "utec": [("11111111-1111-4111-8111-111111111111", "STUDENT"),
             ("22222222-2222-4222-8222-222222222222", "TEACHER")],
    "upc": [("44444444-4444-4444-8444-444444444444", "STUDENT"),
            ("55555555-5555-4555-8555-555555555555", "TEACHER")],
    "pucp": [("77777777-7777-4777-8777-777777777777", "STUDENT"),
             ("88888888-8888-4888-8888-888888888888", "TEACHER")],
}


def _docente(tenant: str, n: int) -> str:
    return str(uuid.uuid5(ESPACIO, f"{tenant}-docente-{n % 20}"))


def _alumno(tenant: str, n: int) -> str:
    return str(uuid.uuid5(ESPACIO, f"{tenant}-alumno-{n}"))


def _tarea(tenant: str, k: int) -> list[dict]:
    assignment_id, course_id = f"asg_{k:026d}", f"crs_{k:026d}"
    due_at = f"2026-{9 + k % 3:02d}-{1 + k % 28:02d}T23:59:00Z"
    tarea = {
        "tenant_id": tenant, "sk": comun.sk_tarea(assignment_id), "entidad": "ASSIGNMENT", "version": 2,
        "assignmentId": assignment_id, "courseId": course_id,
        "title": f"{TITULOS[k % len(TITULOS)]} {k}",
        "instructions": "Tarea de seed para pruebas de carga.",
        "dueAt": due_at, "maxScore": MAX_SCORE, "allowLate": k % 2 == 0, "status": "PUBLISHED",
        "ownerId": _docente(tenant, k), "createdAt": CREADO_EN, "seed": True,
    }
    indice = {"tenant_id": tenant, "sk": comun.sk_tarea_de_curso(course_id, due_at, assignment_id),
              "entidad": "COURSEASSIGN", "assignmentId": assignment_id, "seed": True}
    return [tarea, indice]


def _entrega(tenant: str, n: int) -> list[dict]:
    """La entrega n (desde 1) va en la tarea (n-1)//100 + 1 y es del alumno (n-1) % 100."""
    k, alumno = (n - 1) // ENTREGAS_POR_TAREA + 1, _alumno(tenant, (n - 1) % ENTREGAS_POR_TAREA)
    submission_id, assignment_id = f"sbm_{n:026d}", f"asg_{k:026d}"
    calificada = n % 10 < 7
    entrega = {
        "tenant_id": tenant, "sk": comun.sk_entrega(submission_id), "entidad": "SUBMISSION",
        "version": 2 if calificada else 1,
        "submissionId": submission_id, "assignmentId": assignment_id, "courseId": f"crs_{k:026d}",
        "studentId": alumno, "attempt": 1, "fileIds": [f"fil_{n:026d}"], "late": False,
        "status": "GRADED" if calificada else "SUBMITTED", "submittedAt": ENTREGADO_EN, "seed": True,
    }
    if calificada:
        entrega |= {"score": Decimal(str(8 + n % 13)), "feedback": "Calificación de seed.",
                    "graderId": _docente(tenant, k), "gradedAt": "2026-09-12T15:00:00Z"}
    indice = {"tenant_id": tenant, "sk": comun.sk_entrega_de_tarea(assignment_id, alumno, submission_id),
              "entidad": "ASSIGNSUBMIT", "submissionId": submission_id, "seed": True}
    intentos = {"tenant_id": tenant, "sk": comun.sk_intentos(assignment_id, alumno),
                "entidad": "ATTEMPTS", "total": 1, "seed": True}
    return [entrega, indice, intentos]


def _matriculas_demo(tenant: str) -> list[dict]:
    course_id = f"crs_{1:026d}"
    return [{"tenant_id": tenant, "sk": comun.sk_matricula(course_id, user_id), "entidad": "PROYECCION",
             "courseId": course_id, "userId": user_id, "role": rol, "seed": True}
            for user_id, rol in USUARIOS_DEMO.get(tenant, [])]


def poblar(reparto: dict[str, int] = REPARTO) -> dict[str, int]:
    """Devuelve {tenant: entregas escritas}."""
    escritos = {}
    with t.tabla().batch_writer(overwrite_by_pkeys=["tenant_id", "sk"]) as lote:
        for tenant, cantidad in reparto.items():
            for item in _matriculas_demo(tenant):
                lote.put_item(Item=item)
            tareas = -(-cantidad // ENTREGAS_POR_TAREA)  # redondeo hacia arriba
            for k in range(1, tareas + 1):
                for item in _tarea(tenant, k):
                    lote.put_item(Item=item)
            for n in range(1, cantidad + 1):
                for item in _entrega(tenant, n):
                    lote.put_item(Item=item)
            escritos[tenant] = cantidad
    return escritos


def main():
    parser = argparse.ArgumentParser(description="Seed de ms-evaluaciones")
    parser.add_argument("--stage", help="usa la tabla <stage>-t_evaluaciones")
    parser.add_argument("--escala", type=float, default=1.0, help="fracción del reparto (1 = 30.000)")
    args = parser.parse_args()
    if args.stage:
        os.environ["TABLA"] = f"{args.stage}-t_evaluaciones"
    if not os.environ.get("TABLA"):
        raise SystemExit("Pasa --stage o define TABLA.")
    reparto = {k: max(1, int(v * args.escala)) for k, v in REPARTO.items()}
    for tenant, cantidad in poblar(reparto).items():
        print(f"{tenant}: {cantidad} entregas")
    print(f"Total: {sum(reparto.values())} entregas en {os.environ['TABLA']}")


if __name__ == "__main__":
    main()
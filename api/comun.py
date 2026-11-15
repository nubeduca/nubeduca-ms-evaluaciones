"""Utilidades de las rutas de ms-evaluaciones: claves de la tabla, lecturas comunes y paginación."""

import base64
import binascii
import json
import re
from decimal import Decimal

from lib import tabla as t

COURSE_ID = re.compile(r"^crs_[0-9A-HJKMNP-TV-Z]{26}$")
ASSIGNMENT_ID = re.compile(r"^asg_[0-9A-HJKMNP-TV-Z]{26}$")
SUBMISSION_ID = re.compile(r"^sbm_[0-9A-HJKMNP-TV-Z]{26}$")
FILE_ID = re.compile(r"^fil_[0-9A-HJKMNP-TV-Z]{26}$")
LIMITE_DEFECTO, LIMITE_MAXIMO = 20, 100


def sk_matricula(course_id: str, user_id: str = "") -> str:
    """Proyección de enrollment.created: existe mientras el usuario está matriculado."""
    return f"PROY#ENROLL#{course_id}#{user_id}"


def sk_archivo(file_id: str) -> str:
    """Proyección de file.scanned: resultado del antivirus de un archivo."""
    return f"PROY#FILE#{file_id}"


def sk_tarea(assignment_id: str) -> str:
    return f"ASSIGN#{assignment_id}"


def sk_tarea_de_curso(course_id: str, due_at: str = "", assignment_id: str = "") -> str:
    """Índice de las tareas de un curso, ordenado por fecha de entrega."""
    if not due_at:
        return f"COURSEASSIGN#{course_id}#"
    return f"COURSEASSIGN#{course_id}#{due_at}#{assignment_id}"


def sk_entrega(submission_id: str) -> str:
    return f"SUBMIT#{submission_id}"


def sk_entrega_de_tarea(assignment_id: str, student_id: str = "", submission_id: str = "") -> str:
    """Índice de las entregas de una tarea; con student_id, solo las de ese alumno."""
    if not student_id:
        return f"ASSIGNSUBMIT#{assignment_id}#"
    return f"ASSIGNSUBMIT#{assignment_id}#{student_id}#{submission_id}"


def sk_intentos(assignment_id: str, student_id: str) -> str:
    """Contador de entregas de un alumno en una tarea (attempt = entregas previas + 1)."""
    return f"ATTEMPTS#{assignment_id}#{student_id}"


def matricula(tenant_id: str, course_id: str, user_id: str) -> dict | None:
    clave = {"tenant_id": tenant_id, "sk": sk_matricula(course_id, user_id)}
    return t.tabla().get_item(Key=clave).get("Item")


def archivo(tenant_id: str, file_id: str) -> dict | None:
    clave = {"tenant_id": tenant_id, "sk": sk_archivo(file_id)}
    return t.tabla().get_item(Key=clave).get("Item")


def leer_tarea(tenant_id: str, assignment_id: str, consistente: bool = False) -> dict | None:
    if not ASSIGNMENT_ID.match(assignment_id or ""):
        return None
    clave = {"tenant_id": tenant_id, "sk": sk_tarea(assignment_id)}
    return t.tabla().get_item(Key=clave, ConsistentRead=consistente).get("Item")


def leer_entrega(tenant_id: str, submission_id: str, consistente: bool = False) -> dict | None:
    if not SUBMISSION_ID.match(submission_id or ""):
        return None
    clave = {"tenant_id": tenant_id, "sk": sk_entrega(submission_id)}
    return t.tabla().get_item(Key=clave, ConsistentRead=consistente).get("Item")


def numero(valor):
    """DynamoDB devuelve Decimal; el contrato pide number (20, no "20")."""
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    return valor


def a_tarea(item: dict) -> dict:
    tarea = {k: item[k] for k in ("assignmentId", "courseId", "title", "dueAt", "status")}
    tarea["maxScore"] = numero(item["maxScore"])
    tarea["allowLate"] = bool(item.get("allowLate", False))
    if item.get("instructions") is not None:
        tarea["instructions"] = item["instructions"]
    return tarea


def a_entrega(item: dict, con_nota: bool = True) -> dict:
    """con_nota=False oculta score y feedback: el alumno ve su nota cuando ms-calificaciones la publica."""
    entrega = {k: item[k] for k in ("submissionId", "assignmentId", "studentId", "status", "submittedAt")}
    entrega["attempt"] = numero(item["attempt"])
    entrega["fileIds"] = list(item["fileIds"])
    entrega["late"] = bool(item["late"])
    entrega["score"] = numero(item.get("score")) if con_nota else None
    entrega["feedback"] = item.get("feedback") if con_nota else None
    if item.get("comment") is not None:
        entrega["comment"] = item["comment"]
    return entrega


def param(event: dict, nombre: str) -> str:
    return ((event or {}).get("pathParameters") or {}).get(nombre) or ""


def limite(params: dict) -> int | None:
    try:
        valor = int(params.get("limit", LIMITE_DEFECTO))
    except (TypeError, ValueError):
        return None
    return valor if 1 <= valor <= LIMITE_MAXIMO else None


def codificar(clave: dict | None) -> str | None:
    if not clave:
        return None
    return base64.urlsafe_b64encode(json.dumps(clave, separators=(",", ":")).encode()).decode()


def decodificar(token: str, tenant_id: str, prefijo: str) -> dict | None:
    try:
        clave = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    valido = (isinstance(clave, dict) and set(clave) == {"tenant_id", "sk"} and clave["tenant_id"] == tenant_id
              and isinstance(clave["sk"], str) and clave["sk"].startswith(prefijo))
    return clave if valido else None
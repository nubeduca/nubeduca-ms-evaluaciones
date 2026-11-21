"""
Lambda de la cola nubeduca-<stage>-evaluaciones-eventos (regla ReglaConsumidor del
serverless.yml). Guarda lo que ms-evaluaciones necesita saber de otros servicios:

- lms.courses.enrollment.created.v1  -> PROY#ENROLL#<courseId>#<userId>  (quién puede entregar)
- lms.courses.enrollment.dropped.v1  -> borra esa proyección
- lms.content.file.scanned.v1        -> PROY#FILE#<fileId>               (qué archivos están CLEAN)

Lector tolerante: solo se leen los campos que se usan. El inbox descarta duplicados y
eventos viejos (un created atrasado no revive una matrícula ya retirada).
"""

from api.comun import sk_archivo, sk_matricula
from lib import inbox


def guardar_matricula(evento: inbox.Evento) -> list:
    datos = evento.data
    return [{
        "Put": {
            "Item": {
                "tenant_id": evento.tenant_id,
                "sk": sk_matricula(evento.course_id, datos["userId"]),
                "entidad": "PROYECCION",
                "enrollmentId": datos.get("enrollmentId"),
                "courseId": evento.course_id,
                "userId": datos["userId"],
                "role": datos.get("role"),
                "recibidoDe": evento.source,
                "version": evento.aggregate_version,
            }
        }
    }]


def borrar_matricula(evento: inbox.Evento) -> list:
    clave = {"tenant_id": evento.tenant_id, "sk": sk_matricula(evento.course_id, evento.data["userId"])}
    return [{"Delete": {"Key": clave}}]


def guardar_archivo(evento: inbox.Evento) -> list:
    datos = evento.data
    return [{
        "Put": {
            "Item": {
                "tenant_id": evento.tenant_id,
                "sk": sk_archivo(datos["fileId"]),
                "entidad": "PROYECCION",
                "fileId": datos["fileId"],
                "result": datos.get("result"),
                "purpose": datos.get("purpose"),
                "ownerId": datos.get("ownerId"),
                "recibidoDe": evento.source,
                "version": evento.aggregate_version,
            }
        }
    }]


REACCIONES = {
    "lms.courses.enrollment.created.v1": guardar_matricula,
    "lms.courses.enrollment.dropped.v1": borrar_matricula,
    "lms.content.file.scanned.v1": guardar_archivo,
}


def lambda_handler(event, context):
    return inbox.manejar_lote(event, REACCIONES)

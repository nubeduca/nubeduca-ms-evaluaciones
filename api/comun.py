"""Utilidades de las rutas de ms-evaluaciones: claves de la tabla y lecturas comunes."""

from lib import tabla as t


def sk_matricula(course_id: str, user_id: str = "") -> str:
    """Proyección de enrollment.created: existe mientras el usuario está matriculado."""
    return f"PROY#ENROLL#{course_id}#{user_id}"


def sk_archivo(file_id: str) -> str:
    """Proyección de file.scanned: resultado del antivirus de un archivo."""
    return f"PROY#FILE#{file_id}"


def matricula(tenant_id: str, course_id: str, user_id: str) -> dict | None:
    clave = {"tenant_id": tenant_id, "sk": sk_matricula(course_id, user_id)}
    return t.tabla().get_item(Key=clave).get("Item")


def archivo(tenant_id: str, file_id: str) -> dict | None:
    clave = {"tenant_id": tenant_id, "sk": sk_archivo(file_id)}
    return t.tabla().get_item(Key=clave).get("Item")

"""Configuración del servicio, leída de las variables de entorno que pone serverless.yml."""

import os

TENANTS = ("utec", "upc", "pucp")
ROLES = ("STUDENT", "TEACHER", "ADMIN")

SERVICIO = os.environ.get("SERVICIO", "ms-evaluaciones")
SOURCE = os.environ.get("EVENT_SOURCE", "lms.assessment")


def tabla_nombre() -> str:
    return os.environ.get("TABLA", "dev-t_evaluaciones")


def bus_nombre() -> str:
    return os.environ.get("EVENT_BUS", "lms")


def auth_mode() -> str:
    """cognito: claims del authorizer de API Gateway · mock: token de demo del frontend."""
    return os.environ.get("AUTH_MODE", "cognito").lower()

from datetime import datetime, timezone
from decimal import Decimal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from api import comun
from lib import auth, http, ids, outbox
from lib import tabla as t

PUBLICADA = "lms.assessment.assignment.published.v1"
CAMPOS_EDITABLES = {"title", "instructions", "dueAt", "allowLate"}

def _texto(valor, minimo: int, maximo: int) -> bool:
    return isinstance(valor, str) and minimo <= len(valor.strip()) and len(valor) <= maximo

def _fecha(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    try:
        fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if fecha.tzinfo is None:
        return None
    return fecha.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _validar(cuerpo: dict, nueva: bool) -> str | None:
    if (nueva or "title" in cuerpo) and not _texto(cuerpo.get("title"), 1, 200):
        return "title es obligatorio y tiene hasta 200 caracteres."
    if "instructions" in cuerpo and not _texto(cuerpo["instructions"], 0, 20000):
        return "instructions tiene hasta 20000 caracteres."
    if (nueva or "dueAt" in cuerpo) and _fecha(cuerpo.get("dueAt")) is None:
        return "dueAt es obligatorio y debe ser una fecha con zona horaria (2026-11-10T15:00:00Z)."
    if "allowLate" in cuerpo and not isinstance(cuerpo["allowLate"], bool):
        return "allowLate debe ser true o false."
    if nueva:
        puntaje = cuerpo.get("maxScore")
        if isinstance(puntaje, bool) or not isinstance(puntaje, (int, float)) or puntaje <= 0:
            return "maxScore es obligatorio y debe ser un número mayor que 0."
    return None
    

def no_encontrada(event: dict) -> dict:
    return http.error(event, 404, "TAREA_NO_ENCONTRADA", "No existe la tarea en este tenant.")


def _puede_ver(usuario, tarea: dict) -> bool:
    if usuario.rol != "STUDENT":
        return True
    return (tarea["status"] == "PUBLISHED"
            and comun.matricula(usuario.tenant_id, tarea["courseId"], usuario.user_id) is not None)


def _indice(tenant_id: str, tarea: dict) -> dict:
    return {
        "tenant_id": tenant_id,
        "sk": comun.sk_tarea_de_curso(tarea["courseId"], tarea["dueAt"], tarea["assignmentId"]),
        "entidad": "COURSEASSIGN",
        "assignmentId": tarea["assignmentId"],
    }


def crear(event, usuario):
    if usuario.rol != "TEACHER":
        return http.sin_permiso(event)
    course_id = comun.param(event, "courseId")
    if not comun.COURSE_ID.match(course_id):
        return http.error(event, 404, "CURSO_NO_ENCONTRADO", "No existe el curso en este tenant.")
    cuerpo = http.leer_json(event)
    if not isinstance(cuerpo, dict):
        return http.datos_invalidos(event)
    problema = _validar(cuerpo, nueva=True)
    if problema:
        return http.datos_invalidos(event, problema)

    tenant_id = usuario.tenant_id  # del token, nunca del body
    tarea = {
        "assignmentId": ids.nuevo("asg"),
        "courseId": course_id,
        "title": cuerpo["title"].strip(),
        "dueAt": _fecha(cuerpo["dueAt"]),
        "maxScore": Decimal(str(cuerpo["maxScore"])),
        "allowLate": cuerpo.get("allowLate", False),
        "status": "DRAFT",
    }
    if "instructions" in cuerpo:
        tarea["instructions"] = cuerpo["instructions"]
    outbox.guardar(
        cambios=[
            {"Put": {"Item": {"tenant_id": tenant_id, "sk": comun.sk_tarea(tarea["assignmentId"]),
                              "entidad": "ASSIGNMENT", "version": 1, "ownerId": usuario.user_id,
                              "createdAt": outbox.ahora_iso(), **tarea},
                     "ConditionExpression": "attribute_not_exists(sk)"}},
            {"Put": {"Item": _indice(tenant_id, tarea)}},
        ],
        eventos=[],
    )
    return http.respuesta(201, comun.a_tarea(tarea))


def obtener(event, usuario):
    tarea = comun.leer_tarea(usuario.tenant_id, comun.param(event, "assignmentId"))
    if tarea is None or not _puede_ver(usuario, tarea):
        return no_encontrada(event)  # al alumno sin acceso no se le revela que existe
    return http.respuesta(200, comun.a_tarea(tarea))


def _del_duenio(event, usuario):
    """Devuelve (tarea, None) si el docente es dueño de la tarea, o (None, respuesta de error)."""
    if usuario.rol != "TEACHER":
        return None, http.sin_permiso(event)
    tarea = comun.leer_tarea(usuario.tenant_id, comun.param(event, "assignmentId"), consistente=True)
    if tarea is None:
        return None, no_encontrada(event)
    if tarea["ownerId"] != usuario.user_id:
        return None, http.sin_permiso(event)
    return tarea, None


def editar(event, usuario):
    tarea, error = _del_duenio(event, usuario)
    if error:
        return error
    cuerpo = http.leer_json(event)
    if not isinstance(cuerpo, dict) or not cuerpo or set(cuerpo) - CAMPOS_EDITABLES:
        return http.datos_invalidos(event, "Solo se pueden cambiar title, instructions, dueAt y allowLate.")
    problema = _validar(cuerpo, nueva=False)
    if problema:
        return http.datos_invalidos(event, problema)

    cambios = dict(cuerpo)
    if "title" in cambios:
        cambios["title"] = cambios["title"].strip()
    if "dueAt" in cambios:
        cambios["dueAt"] = _fecha(cambios["dueAt"])
    nueva = tarea | cambios
    tenant_id = usuario.tenant_id
    nombres = {f"#{k}": k for k in cambios} | {"#v": "version"}
    valores = {f":{k}": v for k, v in cambios.items()} | {":uno": 1, ":actual": tarea["version"]}
    operaciones = [{"Update": {
        "Key": {"tenant_id": tenant_id, "sk": comun.sk_tarea(tarea["assignmentId"])},
        "UpdateExpression": "SET " + ", ".join(f"#{k} = :{k}" for k in cambios) + ", #v = #v + :uno",
        "ConditionExpression": "#v = :actual",
        "ExpressionAttributeNames": nombres,
        "ExpressionAttributeValues": valores,
    }}]
    if nueva["dueAt"] != tarea["dueAt"]:  # la fecha es parte de la clave del índice
        operaciones.append({"Delete": {"Key": {"tenant_id": tenant_id, "sk": _indice(tenant_id, tarea)["sk"]}}})
        operaciones.append({"Put": {"Item": _indice(tenant_id, nueva)}})
    try:
        outbox.guardar(cambios=operaciones, eventos=[])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return http.error(event, 409, "TAREA_CAMBIO", "La tarea cambió mientras la editabas; vuelve a intentarlo.")
        raise
    return http.respuesta(200, comun.a_tarea(nueva))


def publicar(event, usuario):
    tarea, error = _del_duenio(event, usuario)
    if error:
        return error
    if tarea["status"] != "DRAFT":
        return http.error(event, 409, "TAREA_YA_PUBLICADA", "La tarea ya está publicada.")

    version = int(tarea["version"]) + 1
    assignment_id = tarea["assignmentId"]
    evento = outbox.nuevo_evento(
        PUBLICADA,
        tenant_id=usuario.tenant_id,
        aggregate_type="assignment",
        aggregate_id=assignment_id,
        aggregate_version=version,
        actor_id=usuario.user_id,
        course_id=tarea["courseId"],
        data={"assignmentId": assignment_id, "title": tarea["title"], "dueAt": tarea["dueAt"],
              "maxScore": comun.numero(tarea["maxScore"])},
    )
    try:
        outbox.guardar(
            cambios=[{"Update": {
                "Key": {"tenant_id": usuario.tenant_id, "sk": comun.sk_tarea(assignment_id)},
                "UpdateExpression": "SET #s = :publicada, #v = :nueva",
                # Si dos llamadas compiten, solo una pasa y solo una publica el evento.
                "ConditionExpression": "#s = :borrador AND #v = :actual",
                "ExpressionAttributeNames": {"#s": "status", "#v": "version"},
                "ExpressionAttributeValues": {":publicada": "PUBLISHED", ":borrador": "DRAFT",
                                              ":nueva": version, ":actual": version - 1},
            }}],
            eventos=[evento],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return http.error(event, 409, "TAREA_YA_PUBLICADA", "La tarea cambió de estado; vuelve a intentarlo.")
        raise
    return http.respuesta(200, comun.a_tarea(tarea | {"status": "PUBLISHED"}))


def listar(event, usuario):
    course_id = comun.param(event, "courseId")
    if not comun.COURSE_ID.match(course_id):
        return http.error(event, 404, "CURSO_NO_ENCONTRADO", "No existe el curso en este tenant.")
    es_alumno = usuario.rol == "STUDENT"
    if es_alumno and comun.matricula(usuario.tenant_id, course_id, usuario.user_id) is None:
        return http.error(event, 404, "CURSO_NO_ENCONTRADO", "No existe el curso en este tenant.")
    params = (event or {}).get("queryStringParameters") or {}
    limite = comun.limite(params)
    if limite is None:
        return http.datos_invalidos(event, "limit debe ser un número entre 1 y 100.")
    prefijo = comun.sk_tarea_de_curso(course_id)
    clave = None
    if params.get("nextToken"):
        clave = comun.decodificar(params["nextToken"], usuario.tenant_id, prefijo)
        if clave is None:
            return http.datos_invalidos(event, "nextToken no es válido.")

    # El alumno solo ve las PUBLISHED: se sigue leyendo el índice hasta llenar la página.
    encontradas = []
    while True:
        consulta = {"KeyConditionExpression": Key("tenant_id").eq(usuario.tenant_id) & Key("sk").begins_with(prefijo),
                    "Limit": limite - len(encontradas)}
        if clave:
            consulta["ExclusiveStartKey"] = clave
        resultado = t.tabla().query(**consulta)
        for indice in resultado.get("Items", []):
            tarea = comun.leer_tarea(usuario.tenant_id, indice["assignmentId"])
            if tarea and (not es_alumno or tarea["status"] == "PUBLISHED"):
                encontradas.append(tarea)
        clave = resultado.get("LastEvaluatedKey")
        if clave is None or len(encontradas) >= limite:
            break
    return http.respuesta(200, {"items": [comun.a_tarea(i) for i in encontradas],
                                "nextToken": comun.codificar(clave)})


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    metodo = (event or {}).get("httpMethod", "GET")
    recurso = (event or {}).get("resource") or ""
    if recurso.endswith("/publish"):
        return publicar(event, usuario)
    if comun.param(event, "assignmentId"):
        return editar(event, usuario) if metodo == "PATCH" else obtener(event, usuario)
    return crear(event, usuario) if metodo == "POST" else listar(event, usuario)


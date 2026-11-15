"""
Entregas (EVA-01), según apis/ms-evaluaciones.openapi.yaml de nubeduca-contratos.

GET  /assignments/{assignmentId}/submissions  (STUDENT, TEACHER)  TEACHER: todas; STUDENT: solo las suyas
POST /assignments/{assignmentId}/submissions  (STUDENT)           publica submission.submitted
GET  /submissions/{submissionId}              (STUDENT, TEACHER)  STUDENT: solo la suya
POST /submissions/{submissionId}/grade        (TEACHER)           publica submission.graded

Para entregar, el alumno debe estar matriculado (PROY#ENROLL#, de enrollment.created) y cada
archivo debe estar CLEAN (PROY#FILE#, de file.scanned); las dos proyecciones las llena
consumidores/eventos.py.

Ítems: SUBMIT#<submissionId> (la entrega), ASSIGNSUBMIT#<assignmentId>#<studentId>#<submissionId>
(índice para listar) y ATTEMPTS#<assignmentId>#<studentId> (contador de intentos).
"""

from decimal import Decimal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from api import comun
from lib import auth, http, ids, outbox
from lib import tabla as t

ENVIADA = "lms.assessment.submission.submitted.v1"
CALIFICADA = "lms.assessment.submission.graded.v1"
ROLES = ("STUDENT", "TEACHER")


def no_encontrada(event: dict, que: str = "TAREA") -> dict:
    nombre = "la tarea" if que == "TAREA" else "la entrega"
    return http.error(event, 404, f"{que}_NO_ENCONTRADA", f"No existe {nombre} en este tenant.")


def _tarea_visible(usuario, assignment_id: str) -> dict | None:
    """La tarea, o None si no existe o el alumno no puede verla (borrador o sin matrícula)."""
    tarea = comun.leer_tarea(usuario.tenant_id, assignment_id)
    if tarea is None:
        return None
    if usuario.rol == "STUDENT" and (
            tarea["status"] != "PUBLISHED"
            or comun.matricula(usuario.tenant_id, tarea["courseId"], usuario.user_id) is None):
        return None
    return tarea


def _validar_entrega(cuerpo: dict) -> str | None:
    archivos = cuerpo.get("fileIds")
    if (not isinstance(archivos, list) or not 1 <= len(archivos) <= 10
            or not all(isinstance(f, str) and comun.FILE_ID.match(f) for f in archivos)
            or len(set(archivos)) != len(archivos)):
        return "fileIds es obligatorio: de 1 a 10 ids de archivo (fil_...) sin repetir."
    if "comment" in cuerpo and not (isinstance(cuerpo["comment"], str) and len(cuerpo["comment"]) <= 2000):
        return "comment tiene hasta 2000 caracteres."
    return None


def entregar(event, usuario):
    if usuario.rol != "STUDENT":
        return http.sin_permiso(event)
    tarea = _tarea_visible(usuario, comun.param(event, "assignmentId"))
    if tarea is None:
        return no_encontrada(event)
    cuerpo = http.leer_json(event)
    if not isinstance(cuerpo, dict):
        return http.datos_invalidos(event)
    problema = _validar_entrega(cuerpo)
    if problema:
        return http.datos_invalidos(event, problema)

    tenant_id, student_id = usuario.tenant_id, usuario.user_id  # del token, nunca del body
    for file_id in cuerpo["fileIds"]:
        archivo = comun.archivo(tenant_id, file_id)
        ajeno = archivo is not None and archivo.get("ownerId") not in (None, student_id)
        if archivo is None or archivo.get("result") != "CLEAN" or ajeno:
            return http.error(event, 409, "ARCHIVO_NO_VERIFICADO",
                              f"El archivo {file_id} no es tuyo o todavía no pasó el antivirus.")
    ahora = outbox.ahora_iso()
    tarde = ahora > tarea["dueAt"]  # las dos fechas están en UTC con el mismo formato
    if tarde and not tarea.get("allowLate", False):
        return http.error(event, 409, "FUERA_DE_PLAZO", "La fecha de entrega ya venció.")

    assignment_id = tarea["assignmentId"]
    clave_intentos = {"tenant_id": tenant_id, "sk": comun.sk_intentos(assignment_id, student_id)}
    contador = t.tabla().get_item(Key=clave_intentos, ConsistentRead=True).get("Item")
    previas = int(contador["total"]) if contador else 0
    entrega = {
        "submissionId": ids.nuevo("sbm"),
        "assignmentId": assignment_id,
        "studentId": student_id,
        "attempt": previas + 1,
        "fileIds": cuerpo["fileIds"],
        "late": tarde,
        "status": "SUBMITTED",
        "submittedAt": ahora,
    }
    if "comment" in cuerpo:
        entrega["comment"] = cuerpo["comment"]
    evento = outbox.nuevo_evento(
        ENVIADA,
        tenant_id=tenant_id,
        aggregate_type="submission",
        aggregate_id=entrega["submissionId"],
        aggregate_version=1,
        actor_id=student_id,
        course_id=tarea["courseId"],
        data={k: entrega[k] for k in ("submissionId", "assignmentId", "studentId", "attempt", "fileIds", "late")},
    )
    try:
        outbox.guardar(
            cambios=[
                {"Put": {"Item": {"tenant_id": tenant_id, "sk": comun.sk_entrega(entrega["submissionId"]),
                                  "entidad": "SUBMISSION", "version": 1, "courseId": tarea["courseId"], **entrega},
                         "ConditionExpression": "attribute_not_exists(sk)"}},
                {"Put": {"Item": {"tenant_id": tenant_id, "entidad": "ASSIGNSUBMIT",
                                  "sk": comun.sk_entrega_de_tarea(assignment_id, student_id, entrega["submissionId"]),
                                  "submissionId": entrega["submissionId"]}}},
                # Si dos entregas del mismo alumno compiten, solo una pasa: no se repite el attempt.
                {"Update": {"Key": clave_intentos,
                            "UpdateExpression": "SET #n = :nuevo, entidad = :entidad",
                            "ConditionExpression": "attribute_not_exists(#n) OR #n = :previas",
                            "ExpressionAttributeNames": {"#n": "total"},
                            "ExpressionAttributeValues": {":nuevo": previas + 1, ":previas": previas,
                                                          ":entidad": "ATTEMPTS"}}},
            ],
            eventos=[evento],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return http.error(event, 409, "ENTREGA_EN_CURSO", "Ya hay otra entrega tuya en proceso; vuelve a intentarlo.")
        raise
    return http.respuesta(201, comun.a_entrega(entrega))


def listar(event, usuario):
    tarea = _tarea_visible(usuario, comun.param(event, "assignmentId"))
    if tarea is None:
        return no_encontrada(event)
    params = (event or {}).get("queryStringParameters") or {}
    limite = comun.limite(params)
    if limite is None:
        return http.datos_invalidos(event, "limit debe ser un número entre 1 y 100.")
    es_alumno = usuario.rol == "STUDENT"
    prefijo = comun.sk_entrega_de_tarea(tarea["assignmentId"])
    if es_alumno:
        prefijo += f"{usuario.user_id}#"
    consulta = {"KeyConditionExpression": Key("tenant_id").eq(usuario.tenant_id) & Key("sk").begins_with(prefijo),
                "Limit": limite}
    if params.get("nextToken"):
        clave = comun.decodificar(params["nextToken"], usuario.tenant_id, prefijo)
        if clave is None:
            return http.datos_invalidos(event, "nextToken no es válido.")
        consulta["ExclusiveStartKey"] = clave
    resultado = t.tabla().query(**consulta)
    entregas = [comun.leer_entrega(usuario.tenant_id, i["submissionId"]) for i in resultado.get("Items", [])]
    return http.respuesta(200, {"items": [comun.a_entrega(e, con_nota=not es_alumno) for e in entregas if e],
                                "nextToken": comun.codificar(resultado.get("LastEvaluatedKey"))})


def obtener(event, usuario):
    entrega = comun.leer_entrega(usuario.tenant_id, comun.param(event, "submissionId"))
    es_alumno = usuario.rol == "STUDENT"
    if entrega is None or (es_alumno and entrega["studentId"] != usuario.user_id):
        return no_encontrada(event, "ENTREGA")  # al alumno no se le revela la entrega de otro
    return http.respuesta(200, comun.a_entrega(entrega, con_nota=not es_alumno))


def _puede_calificar(usuario, tarea: dict) -> bool:
    """El docente dueño de la tarea, o un docente matriculado en el curso."""
    if tarea["ownerId"] == usuario.user_id:
        return True
    matricula = comun.matricula(usuario.tenant_id, tarea["courseId"], usuario.user_id)
    return matricula is not None and matricula.get("role") == "TEACHER"


def calificar(event, usuario):
    if usuario.rol != "TEACHER":
        return http.sin_permiso(event)
    tenant_id = usuario.tenant_id
    entrega = comun.leer_entrega(tenant_id, comun.param(event, "submissionId"), consistente=True)
    tarea = comun.leer_tarea(tenant_id, entrega["assignmentId"]) if entrega else None
    if entrega is None or tarea is None:
        return no_encontrada(event, "ENTREGA")
    if not _puede_calificar(usuario, tarea):
        return http.sin_permiso(event)
    cuerpo = http.leer_json(event)
    if not isinstance(cuerpo, dict):
        return http.datos_invalidos(event)
    nota = cuerpo.get("score")
    if isinstance(nota, bool) or not isinstance(nota, (int, float)) or nota < 0:
        return http.datos_invalidos(event, "score es obligatorio y debe ser un número mayor o igual a 0.")
    puntaje_max = comun.numero(tarea["maxScore"])
    if nota > puntaje_max:
        return http.datos_invalidos(event, f"score no puede ser mayor que el puntaje máximo ({puntaje_max}).")
    if "feedback" in cuerpo and not (isinstance(cuerpo["feedback"], str) and len(cuerpo["feedback"]) <= 5000):
        return http.datos_invalidos(event, "feedback tiene hasta 5000 caracteres.")

    version = int(entrega["version"]) + 1
    cambios = {"status": "GRADED", "score": Decimal(str(nota)), "feedback": cuerpo.get("feedback"),
               "graderId": usuario.user_id, "gradedAt": outbox.ahora_iso()}
    evento = outbox.nuevo_evento(
        CALIFICADA,
        tenant_id=tenant_id,
        aggregate_type="submission",
        aggregate_id=entrega["submissionId"],
        aggregate_version=version,
        actor_id=usuario.user_id,
        course_id=tarea["courseId"],
        data={"submissionId": entrega["submissionId"], "assignmentId": entrega["assignmentId"],
              "studentId": entrega["studentId"], "graderId": usuario.user_id,
              "score": nota, "maxScore": puntaje_max},
    )
    try:
        outbox.guardar(
            cambios=[{"Update": {
                "Key": {"tenant_id": tenant_id, "sk": comun.sk_entrega(entrega["submissionId"])},
                "UpdateExpression": "SET " + ", ".join(f"#{k} = :{k}" for k in cambios) + ", #v = :nueva",
                # Si dos docentes califican a la vez, solo uno pasa y solo uno publica.
                "ConditionExpression": "#v = :actual",
                "ExpressionAttributeNames": {f"#{k}": k for k in cambios} | {"#v": "version"},
                "ExpressionAttributeValues": {f":{k}": v for k, v in cambios.items()}
                | {":nueva": version, ":actual": version - 1},
            }}],
            eventos=[evento],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            return http.error(event, 409, "ENTREGA_CAMBIO", "La entrega cambió mientras la calificabas; vuelve a intentarlo.")
        raise
    return http.respuesta(200, comun.a_entrega(entrega | cambios))


def lambda_handler(event, context):
    usuario = auth.usuario(event)
    if usuario is None:
        return http.no_autenticado(event)
    if usuario.rol not in ROLES:
        return http.sin_permiso(event)
    metodo = (event or {}).get("httpMethod", "GET")
    recurso = (event or {}).get("resource") or ""
    if recurso.endswith("/grade"):
        return calificar(event, usuario)
    if comun.param(event, "submissionId"):
        return obtener(event, usuario)
    return entregar(event, usuario) if metodo == "POST" else listar(event, usuario)
# nubeduca-ms-evaluaciones

Microservicio de **tareas y entregas** de Nubeduca. Se creó desde [`nubeduca-plantilla-servicio`](https://github.com/nubeduca/nubeduca-plantilla-servicio) (LIB-01) y sigue el contrato [`apis/ms-evaluaciones.openapi.yaml`](https://github.com/nubeduca/nubeduca-contratos/blob/main/apis/ms-evaluaciones.openapi.yaml) y el [catálogo de eventos](https://github.com/nubeduca/nubeduca-contratos/blob/main/eventos/catalogo.yaml) de `nubeduca-contratos`. **No se publica un evento ni se expone una ruta que no esté ahí.**

| Tarea | Responsable | Estado |
|---|---|---|
| [EVA-01 · Tareas y Entregas](https://github.com/nubeduca/nubeduca-backlog/issues/24) | Dev 4 @Edinson695 | Tareas con ciclo DRAFT → PUBLISHED, entregas con intentos y calificación, consumidor de matrículas y archivos, y seed de 30.000 entregas |

## API (EVA-01)

El tenant sale siempre del token (`custom:tenant_id`, o `Bearer mock.<tenant>.<ROL>.<userId>` en modo mock), nunca de la ruta ni del body.

| Método y ruta | Roles | Qué hace | Evento |
|---|---|---|---|
| `GET /health` | pública | Prueba de vida | |
| `GET /courses/{courseId}/assignments?limit=&nextToken=` | STUDENT, TEACHER, ADMIN | Tareas del curso por fecha de entrega. STUDENT: solo `PUBLISHED` y si está matriculado (si no, 404) | |
| `POST /courses/{courseId}/assignments` | TEACHER | `{title, dueAt, maxScore, instructions?, allowLate?}`. Nace en `DRAFT` | |
| `GET /assignments/{assignmentId}` | STUDENT, TEACHER, ADMIN | Al alumno sin acceso se le responde 404 | |
| `PATCH /assignments/{assignmentId}` | TEACHER dueño | Solo `title`, `instructions`, `dueAt` y `allowLate` (`maxScore` no se cambia) | |
| `POST /assignments/{assignmentId}/publish` | TEACHER dueño | `DRAFT` → `PUBLISHED`. 409 `TAREA_YA_PUBLICADA` | `lms.assessment.assignment.published.v1` |
| `GET /assignments/{assignmentId}/submissions?limit=&nextToken=` | STUDENT, TEACHER | TEACHER: todas. STUDENT: solo las suyas | |
| `POST /assignments/{assignmentId}/submissions` | STUDENT matriculado | `{fileIds, comment?}`. `attempt` = entregas previas + 1. 409 `ARCHIVO_NO_VERIFICADO` si un archivo no está `CLEAN` o es de otro alumno; 409 `FUERA_DE_PLAZO` si venció y `allowLate` es false | `lms.assessment.submission.submitted.v1` |
| `GET /submissions/{submissionId}` | STUDENT (la suya), TEACHER | Al alumno no se le revela la entrega de otro (404) | |
| `POST /submissions/{submissionId}/grade` | TEACHER dueño de la tarea o matriculado como TEACHER en el curso | `{score, feedback?}`. 400 si `score` > `maxScore`. Se puede volver a calificar | `lms.assessment.submission.graded.v1` |

Los errores tienen la forma `{code, message, requestId}`.

Tres reglas que conviene conocer:
- **El alumno no ve su nota aquí.** Para un STUDENT, `score` y `feedback` salen en `null` aunque la entrega esté en `GRADED`: la nota la publica ms-calificaciones (EVA-02).
- **No se valida que el curso exista.** Este servicio no consume `course.created`; cualquier TEACHER del tenant crea tareas en un `courseId` con formato válido.
- **ADMIN solo lee tareas.** No edita, no publica y no usa las rutas de entregas, como marca `x-roles` en el contrato.

## Eventos

| Evento | Dirección | Cuándo |
|---|---|---|
| `lms.assessment.assignment.published.v1` | publica → ms-calificaciones, ms-notificaciones | `POST /assignments/{id}/publish` |
| `lms.assessment.submission.submitted.v1` | publica → ms-notificaciones | `POST /assignments/{id}/submissions` |
| `lms.assessment.submission.graded.v1` | publica → ms-calificaciones | `POST /submissions/{id}/grade` (cada recalificación sube `aggregateVersion`) |
| `lms.courses.enrollment.created.v1` | consume ← ms-cursos | Guarda `PROY#ENROLL#<courseId>#<userId>`: quién puede entregar |
| `lms.courses.enrollment.dropped.v1` | consume ← ms-cursos | Borra esa proyección |
| `lms.content.file.scanned.v1` | consume ← ms-contenidos | Guarda `PROY#FILE#<fileId>` con el resultado del antivirus |

Los eventos propios salen por **outbox**: el cambio y el ítem `EVENTO#<eventId>` van en la misma `TransactWriteItems`, y antes se validan contra su JSON Schema de `nubeduca-contratos` (`contratos/esquemas/`). Todos llevan `metadata.courseId`. Los consumidos pasan por el **inbox** de la plantilla, que descarta duplicados y eventos viejos (un `enrollment.created` atrasado no revive una matrícula retirada).

## Tabla `<stage>-t_evaluaciones`

PK `tenant_id` (HASH) + `sk` (RANGE), `PAY_PER_REQUEST`, Stream `NEW_IMAGE` y TTL en `ttl`.

| `sk` | Entidad |
|---|---|
| `ASSIGN#<assignmentId>` | Tarea |
| `COURSEASSIGN#<courseId>#<dueAt>#<assignmentId>` | Índice: tareas de un curso por fecha de entrega |
| `SUBMIT#<submissionId>` | Entrega |
| `ASSIGNSUBMIT#<assignmentId>#<studentId>#<submissionId>` | Índice: entregas de una tarea (y de un alumno en esa tarea) |
| `ATTEMPTS#<assignmentId>#<studentId>` | Contador de entregas del alumno en la tarea (`total`) |
| `PROY#ENROLL#<courseId>#<userId>` | Matrícula, proyección de `enrollment.created` |
| `PROY#FILE#<fileId>` | Archivo escaneado, proyección de `file.scanned` |
| `EVENTO#`, `INBOX#`, `AGG#` | Outbox, inbox y versión de agregados de la plantilla |

```json
{
  "tenant_id": "utec",
  "sk": "ASSIGN#asg_01JCF4SQ7XAMY8HTMRFHAWZ73F",
  "entidad": "ASSIGNMENT",
  "assignmentId": "asg_01JCF4SQ7XAMY8HTMRFHAWZ73F",
  "courseId": "crs_01JCQRZHQT9F4WY2JYPDV546K7",
  "title": "Laboratorio 4: SQS y DLQ",
  "instructions": "Sube tu informe en PDF.",
  "dueAt": "2026-11-20T23:59:00Z",
  "maxScore": 20,
  "allowLate": false,
  "status": "PUBLISHED",
  "ownerId": "c62d9930-b473-4b1d-adec-72fdda7cf599",
  "createdAt": "2026-11-11T15:00:00Z",
  "version": 2
}
```

```json
{
  "tenant_id": "utec",
  "sk": "SUBMIT#sbm_01JCY7SYF5X0ZHRJ7KQSVX4BP0",
  "entidad": "SUBMISSION",
  "submissionId": "sbm_01JCY7SYF5X0ZHRJ7KQSVX4BP0",
  "assignmentId": "asg_01JCF4SQ7XAMY8HTMRFHAWZ73F",
  "courseId": "crs_01JCQRZHQT9F4WY2JYPDV546K7",
  "studentId": "a18577e2-e005-4a49-a29e-755dbf08f5b6",
  "attempt": 1,
  "fileIds": ["fil_01JC8P2K48BKYRT5ATH3DZDA66"],
  "comment": "Adjunto mi informe.",
  "late": false,
  "status": "GRADED",
  "submittedAt": "2026-11-12T15:22:00Z",
  "score": 17,
  "feedback": "Buen trabajo.",
  "graderId": "c62d9930-b473-4b1d-adec-72fdda7cf599",
  "gradedAt": "2026-11-13T15:23:00Z",
  "version": 2
}
```

```json
{
  "tenant_id": "utec",
  "sk": "PROY#ENROLL#crs_01JCQRZHQT9F4WY2JYPDV546K7#a18577e2-e005-4a49-a29e-755dbf08f5b6",
  "entidad": "PROYECCION",
  "enrollmentId": "enr_01JC5F0KPBVF7HK1VV8H23JGMW",
  "courseId": "crs_01JCQRZHQT9F4WY2JYPDV546K7",
  "userId": "a18577e2-e005-4a49-a29e-755dbf08f5b6",
  "role": "STUDENT",
  "recibidoDe": "lms.courses",
  "version": 1
}
```

```json
{
  "tenant_id": "utec",
  "sk": "PROY#FILE#fil_01JC8P2K48BKYRT5ATH3DZDA66",
  "entidad": "PROYECCION",
  "fileId": "fil_01JC8P2K48BKYRT5ATH3DZDA66",
  "result": "CLEAN",
  "purpose": "SUBMISSION",
  "ownerId": "a18577e2-e005-4a49-a29e-755dbf08f5b6",
  "recibidoDe": "lms.content",
  "version": 1
}
```

Los índices solo guardan un puntero (`assignmentId` o `submissionId`), y el contador `ATTEMPTS#` solo `total`.

## Seed: 30.000 entregas

```bash
python scripts/poblar_datos.py --stage dev               # UTEC 15.000, UPC 10.000, PUCP 5.000
python scripts/poblar_datos.py --stage dev --escala 0.01 # 1 % para probar
```

- No escribe `EVENTO#`, así que no inunda el bus.
- Es idempotente: los ids son fijos y al correrlo de nuevo se reescriben los mismos ítems.
- Cada tarea tiene 100 entregas de 100 alumnos de seed: 150, 100 y 50 tareas por tenant, todas `PUBLISHED`.
- 70 % de las entregas quedan `GRADED` y 30 % `SUBMITTED`.
- Calza con el seed de ms-cursos: la tarea `k` va en el curso `crs_<k>` y su dueño es el docente de seed de ese curso.
- Matricula a los usuarios demo del frontend en el primer curso de seed, para que vean sus tareas.
- Con sus índices y contadores son unos 90.000 ítems. Probado a escala completa con moto: unos 25 s.

## Probar

```bash
python -m venv .venv && source .venv/Scripts/activate    # en Linux o macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

44 pruebas con moto (sin AWS). Cubren:
- permisos por rol y por dueño, y aislamiento por tenant;
- validación del body, tareas ya publicadas y notas fuera de rango;
- matrícula, archivos sin verificar o ajenos, y entregas fuera de plazo;
- intentos, paginación y que el alumno no vea la nota;
- que los tres eventos publicados cumplen su esquema;
- el consumidor (duplicados, eventos viejos y mensajes rotos) y el seed.

## Desplegar

En la cuenta de integración (D2) se despliega junto con todo lo demás desde la VM `nubeduca-despliegue`: `scripts/desplegar.sh staging --con-seed` de [`nubeduca-backend`](https://github.com/nubeduca/nubeduca-backend), o solo este servicio con `--solo evaluaciones`.

**Observabilidad (PLT-06):** X-Ray en API Gateway y Lambda, y una alarma de CloudWatch (`nubeduca-<stage>-evaluaciones-eventos-dlq`) si un evento llega a la DLQ del consumidor.

Para desplegarlo a mano en tu propia cuenta, con las credenciales cargadas (en `nubeduca-backend`: `source scripts/cuenta.sh && cargar_cuenta`):

```bash
serverless deploy --stage dev
python scripts/poblar_datos.py --stage dev
```

El despliegue publica la URL de la API en SSM (`/nubeduca/<stage>/api/evaluaciones/url`), de donde la leen el frontend y la prueba de humo de `desplegar.sh`. Para que un alumno pueda entregar hacen falta su matrícula (`enrollment.created`, de ms-cursos) y el escaneo de sus archivos (`file.scanned`, de ms-contenidos), que llegan por el bus.

Reglas del equipo: Python 3.13, Serverless Framework v4, rol `LabRole`, `tenant_id` y rol solo desde el token, ramas `<tipo>/<ID>-descripcion`, commits `<ID>: mensaje` y PRs a `main` con 1 aprobación y la CI en verde.
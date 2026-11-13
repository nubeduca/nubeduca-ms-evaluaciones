# nubeduca-ms-evaluaciones

Microservicio de Nubeduca: Tareas, fechas límite y entregas de los alumnos.

Contrato: [`apis/ms-evaluaciones.openapi.yaml`](https://github.com/nubeduca/nubeduca-contratos/blob/main/apis/ms-evaluaciones.openapi.yaml) y el [catálogo de eventos](https://github.com/nubeduca/nubeduca-contratos/blob/main/eventos/catalogo.yaml) de `nubeduca-contratos`. **No se publica un evento ni se expone una ruta que no esté ahí.**

## Tareas

| Tarea | Responsable | Listo cuando |
|---|---|---|
| [EVA-01 · Tareas y Entregas](https://github.com/nubeduca/nubeduca-backlog/issues/24) | Dev 4 @Edinson695 | Un alumno matriculado entrega y se emite `submission.submitted` |

**Estado:** ⚪ Solo README. EVA-01 espera a ACA-04 (matrícula), que espera a ACA-02. Usa el contrato de `file.scanned` sin esperar a que CON-01 esté implementada.

## Eventos

| | |
|---|---|
| `source` | `lms.assessment` |
| Publica | `lms.assessment.assignment.published.v1` (→ ms-calificaciones, ms-notificaciones), `lms.assessment.submission.submitted.v1` (→ ms-notificaciones), `lms.assessment.submission.graded.v1` (→ ms-calificaciones) |
| Consume | `lms.courses.enrollment.created.v1`, `lms.courses.enrollment.dropped.v1`, `lms.content.file.scanned.v1` |

## Cómo empezar

1. Copia [`nubeduca-plantilla-servicio`](https://github.com/nubeduca/nubeduca-plantilla-servicio) a este repo (D1: un repo por microservicio). Ejemplos ya hechos: [`nubeduca-ms-usuarios`](https://github.com/nubeduca/nubeduca-ms-usuarios) y [`nubeduca-ms-cursos`](https://github.com/nubeduca/nubeduca-ms-cursos).
2. En `serverless.yml`: `service: nubeduca-ms-evaluaciones`, `SERVICIO: ms-evaluaciones`, `EVENT_SOURCE: lms.assessment`, tabla `${sls:stage}-t_evaluaciones`, y una regla, cola, DLQ y alarma para los eventos que consume. Sin línea `org:`.
3. Copia tus esquemas: `python scripts/copiar_esquemas.py --solo lms.assessment`.
4. Implementa las rutas del OpenAPI con pruebas (moto, sin AWS) y CI (ruff, yamllint y pytest).
5. Agrega `scripts/poblar_datos.py --stage <stage>`: 30.000 registros en la tabla principal, **UTEC 15.000, UPC 10.000 y PUCP 5.000**, sin ítems `EVENTO#`.
6. Documenta en este README la **estructura JSON de tus tablas** (lo pide la rúbrica) y publica la URL de tu API en SSM (`/nubeduca/<stage>/api/evaluaciones/url`).

## Desplegar

En la cuenta de integración (D2) se despliega con todo lo demás desde la VM `nubeduca-despliegue`: `scripts/desplegar.sh staging --con-seed` de [`nubeduca-backend`](https://github.com/nubeduca/nubeduca-backend), o solo este servicio con `--solo evaluaciones`. Mientras este repo no tenga `serverless.yml`, `desplegar.sh` lo marca como **PENDIENTE**.

Reglas del equipo: Python 3.13, Serverless Framework v4, rol `LabRole`, `tenant_id` y rol solo desde el token, ramas `<tipo>/<ID>-descripcion`, commits `<ID>: mensaje` y PRs a `main` con 1 aprobación y la CI en verde.

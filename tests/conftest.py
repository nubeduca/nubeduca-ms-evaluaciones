import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import tabla as t  # noqa: E402
from relay import stream_a_bus  # noqa: E402

TABLA = "test-t_evaluaciones"
BUS = "lms"
TENANT = "utec"
DOCENTE = "c62d9930-b473-4b1d-adec-72fdda7cf599"
ALUMNO = "a18577e2-e005-4a49-a29e-755dbf08f5b6"


def token(rol="TEACHER", tenant=TENANT, user=DOCENTE):
    return {"Authorization": f"Bearer mock.{tenant}.{rol}.{user}"}


def peticion(metodo, headers, body=None, path_params=None, query=None, resource=None):
    return {
        "httpMethod": metodo,
        "resource": resource,
        "headers": headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params,
        "queryStringParameters": query,
        "requestContext": {"requestId": "req-test"},
    }


def items(prefijo, tenant=TENANT):
    resp = t.tabla().query(
        KeyConditionExpression="tenant_id = :t AND begins_with(sk, :p)",
        ExpressionAttributeValues={":t": tenant, ":p": prefijo},
    )
    return resp["Items"]


def eventos(tenant=TENANT):
    return [json.loads(i["detail"]) | {"_detailType": i["detailType"], "_source": i["source"]}
            for i in items("EVENTO#", tenant)]


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("TABLA", TABLA)
    monkeypatch.setenv("EVENT_BUS", BUS)
    monkeypatch.setenv("AUTH_MODE", "mock")
    with mock_aws():
        t.reiniciar()
        stream_a_bus.reiniciar()
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName=TABLA,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"},
        )
        sqs = boto3.client("sqs")
        cola = sqs.create_queue(QueueName="evaluaciones-eventos")["QueueUrl"]
        cola_arn = sqs.get_queue_attributes(QueueUrl=cola, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        events = boto3.client("events")
        events.create_event_bus(Name=BUS)
        events.put_rule(
            Name="evaluaciones-consumidor",
            EventBusName=BUS,
            EventPattern='{"source":["lms.courses","lms.content"],"detail-type":["lms.courses.enrollment.created.v1","lms.courses.enrollment.dropped.v1","lms.content.file.scanned.v1"]}',
        )
        events.put_targets(Rule="evaluaciones-consumidor", EventBusName=BUS, Targets=[{"Id": "1", "Arn": cola_arn}])
        yield {"cola": cola, "sqs": sqs, "ddb": ddb}
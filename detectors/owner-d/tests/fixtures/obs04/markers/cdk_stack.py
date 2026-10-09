# Synthetic OBS-04 fixture: CDK Lambda with the JSON log format. Never executed.
from aws_cdk import aws_lambda as lambda_


def build(scope):
    return lambda_.Function(scope, "Orders", logging_format=lambda_.LoggingFormat.JSON)

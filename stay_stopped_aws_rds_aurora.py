#!/usr/bin/env python3
"""Stop AWS RDS and Aurora databases after the forced 7th-day start

github.com/sqlxpert/stay-stopped-aws-rds-aurora  GPLv3  Copyright Paul Marcelin
"""

from logging import getLogger, INFO, WARNING, ERROR
from os import environ as os_environ
from json import dumps as json_dumps, loads as json_loads
from re import match as re_match
from botocore.exceptions import ClientError as botocore_ClientError
from botocore.config import Config as botocore_Config
from boto3 import client as boto3_client

logger = getLogger()
# Skip "credentials in environment" INFO message, unavoidable in AWS Lambda:
getLogger("botocore").setLevel(WARNING)

FOLLOW_UNTIL_STOPPED = ("FOLLOW_UNTIL_STOPPED" in os_environ)  # pylint: disable=superfluous-parens


def log(entry_type, entry_value, log_level):
  """Emit a JSON-format log entry
  """
  entry_value_out = json_loads(json_dumps(entry_value, default=str))
  # Avoids "Object of type datetime is not JSON serializable" in
  # https://github.com/aws/aws-lambda-python-runtime-interface-client/blob/9efb462/awslambdaric/lambda_runtime_log_utils.py#L109-L135
  #
  # The JSON encoder in the AWS Lambda Python runtime isn't configured to
  # serialize datatime values in responses returned by AWS's own Python SDK!
  #
  # Alternative considered:
  # https://docs.powertools.aws.dev/lambda/python/latest/core/logger/

  logger.log(
    log_level, "", extra={"type": entry_type, "value": entry_value_out}
  )


def extract_db_cluster_state(error_msg):
  """Take an InvalidDBClusterStateFault error message, return cluster state

  None indicates state not found.

  Use "status" except in the context of parsing an Aurora
  InvalidDBClusterStateFault error message, which refers to "state".
  """
  db_cluster_state_re_match = re_match(
    r"DbCluster \S+ is in (?P<db_cluster_state>\S+) state", error_msg
  )
  return (
    db_cluster_state_re_match.group("db_cluster_state")
    if db_cluster_state_re_match else
    None
  )


def get_db_instance_status(
  lambda_event, sqs_message, describe_db_instances_kwargs
):
  """Take describe_db_instances kwargs, return RDS database instance status

  None indicates an error.
  """
  log_level = ERROR
  db_instance_status = None

  method_name = "describe_db_instances"
  result = op_do(method_name, describe_db_instances_kwargs)
  if not isinstance(result, Exception):
    db_instances = result.get("DBInstances", [])
    if len(db_instances) == 1:
      db_instance_status = db_instances[0].get("DBInstanceStatus")
      if db_instance_status is not None:
        log_level = INFO

  op_log(
    lambda_event,
    sqs_message,
    method_name,
    describe_db_instances_kwargs,
    result,
    log_level
  )

  return db_instance_status


def assess_db_status(db_status):
  """Take database status, return log level and retry flag

  Focus is on statuses that temporarily or permanently preclude successfully
  requesting a database stop (i.e., not "available") and then on statuses
  through to a successful database stop.

  Aurora database cluster:
    https://docs.aws.amazon.com/en_us/AmazonRDS/latest/AuroraUserGuide/accessing-monitoring.html#Aurora.Status

  Aurora database instance (information only; instance status not assessed)
    https://docs.aws.amazon.com/en_us/AmazonRDS/latest/AuroraUserGuide/accessing-monitoring.html#Overview.DBInstance.Status

  RDS database instance:
    https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/accessing-monitoring.html#Overview.DBInstance.Status
  """
  log_level = ERROR
  retry = False

  if db_status is None:
    retry = True
    # Might be possible to determine status later

  else:
    match db_status.lower():
      # Unless noted, same status values (normalized to lower case) for
      # Aurora database cluster and RDS database instance.

      case "stopped" | "deleting" | "deleted":
        log_level = INFO
        # Terminal status, success!

      case (
          "starting"  # Stop not yet successfully requested
        | "stopping"  # Stop not yet confirmed
        | "backing-up"
        | "maintenance"
        | "modifying"
        | "renaming"
        | "resetting-master-credentials"
        | "storage-optimization"
        | "upgrading"
        # Aurora database cluster only:
        | "backtracking"
        | "failing-over"
        | "migrating"
        | "promoting"
        | "update-iam-db-auth"
        # RDS database instance only:
        | "converting-to-vpc"
        | "configuring-enhanced-monitoring"
        | "configuring-iam-database-auth"
        | "configuring-log-exports"
        | "delete-precheck"
        | "moving-to-vpc"
        | "rebooting"
        | "storage-config-upgrade"
        | "storage-initialization"
      ):
        log_level = INFO
        retry = True
        # Status will probably change

      case (
          "inaccessible-encryption-credentials-recoverable"
        # RDS database instance only:
        | "incompatible-network"
        | "incompatible-option-group"
        | "incompatible-parameters"
      ):
        retry = True
        # Status might change, but log as ERROR

      case (
          "inaccessible-encryption-credentials"
        # Aurora database cluster only:
        | "cloning-failed"
        | "migration-failed"
        | "preparing-data-migration"
        # RDS database instance only:
        | "failed"
        | "incompatible-restore"
        | "insufficient-capacity"
        | "restore-error"
        | "storage-full"
      ):
        pass
        # Status won't change; listing recognized no-retry ERROR conditions

  return (log_level, retry)


def assess_db_invalid_parameter(error_message):
  """Take InvalidParameterCombination message, return log level and retry flag
  """
  log_level = ERROR
  retry = False

  if (
    ("aurora" in error_message)
    and ("not eligible for stopping" in error_message)
  ):
    log_level = INFO
    # Quietly ignore database instance start events for Aurora,
    # https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#RDS-EVENT-0088
    #
    # ...because there will be a corresponding database cluster event,
    # https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#RDS-EVENT-0151
    #
    # ...and Aurora database instances cannot be stopped independently of
    # their Aurora cluster, per:
    # https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-cluster-stop-start.html#aurora-cluster-start-stop-overview
    #
    # This case occurs only in test mode. At the EventPattern level (see
    # CloudFormation), the start event for Aurora database instances is
    # indistinguishable from the start event for RDS database instances,
    # https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.Messages.html#RDS-EVENT-0088

  return (log_level, retry)


def assess_stop_db_exception(
  lambda_event,
  sqs_message,
  source_type_word,
  misc_exception,
  describe_db_instances_kwargs
):
  """Take a boto3 exception, return log level and retry flag

  ClientError is general but statically-defined, making comparison
  easier than for RDS-specific but dynamically-defined exceptions...

  RDS-specific exception name:  ClientError code:
  InvalidDBClusterStateFault    InvalidDBClusterStateFault
  InvalidDBInstanceStateFault   InvalidDBInstanceState (Fault suffix missing!)

  https://boto3.amazonaws.com/v1/documentation/api/latest/guide/error-handling.html#parsing-error-responses-and-catching-exceptions-from-aws-services
  """
  log_level = ERROR
  retry = False

  if isinstance(misc_exception, botocore_ClientError):
    error_dict = getattr(misc_exception, "response", {}).get("Error", {})
    error_message = error_dict.get("Message", "")
    match error_dict.get("Code"):

      case "InvalidDBClusterStateFault":
        db_status = extract_db_cluster_state(error_message)
        (log_level, retry) = assess_db_status(db_status)

      case "InvalidDBInstanceState" if source_type_word == "INSTANCE":  # RDS
        db_status = get_db_instance_status(
          lambda_event, sqs_message, describe_db_instances_kwargs
        )
        (log_level, retry) = assess_db_status(db_status)

      case "InvalidDBInstanceState":  # Aurora
        # Status of this and any other cluster members will probably change
        log_level = INFO
        retry = True

      case "InvalidParameterCombination":
        (log_level, retry) = assess_db_invalid_parameter(error_message)

  return (log_level, retry)


rds_client = None  # pylint: disable=invalid-name


def rds_client_get():
  """Return a boto3 RDS client, creating it if needed

  boto3 method references can only be resolved at run-time, against an
  instance of an AWS service's Client class.
  http://boto3.readthedocs.io/en/latest/guide/events.html#extensibility-guide

  Alternatives considered:
  https://github.com/boto/boto3/issues/3197#issue-1175578228
  https://github.com/aws-samples/boto-session-manager-project
  """
  global rds_client  # pylint: disable=global-statement
  if not rds_client:
    rds_client = boto3_client(
      "rds", config=botocore_Config(retries={"mode": "standard"})
    )
  return rds_client


def op_do(op_method_name, op_kwargs):
  """Take a boto3 method name and kwargs, return response or exception
  """
  try:
    op_method = getattr(rds_client_get(), op_method_name)
    op_result = op_method(**op_kwargs)
  except Exception as misc_exception:  # pylint: disable=broad-exception-caught
    op_result = misc_exception
  return op_result


def op_log(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  lambda_event, sqs_message, op_method_name, op_kwargs, result, log_level
):
  """Log Lambda event, batch message, boto3 method, kwargs, exception/response

  op_log() is separate from op_do() so that log_level can be decided later:
  - A result originally logged as INFO can be re-logged as an ERROR after a
    downstream error occurs.
  - An expected exception can be logged as INFO instead of an ERROR.
  """
  if log_level > INFO:
    log("LAMBDA_EVENT", lambda_event, log_level)
    log("SQS_MESSAGE", sqs_message, log_level)
  if op_method_name:
    log(f"{op_method_name.upper()}_KWARGS", op_kwargs, log_level)
  log(
    "EXCEPTION" if isinstance(result, Exception) else "AWS_RESPONSE",
    result,
    log_level
  )


def lambda_handler(lambda_event, context):  # pylint: disable=unused-argument
  """Try to request stopping Aurora database clusters, RDS database instances

  Called in response to a batch of forced database start events (see
  EventPattern in CloudFormation) stored as messages in the main SQS queue.

  Batch item failure:
    Event message remains in main queue. Retry after VisibilityTimeout ,
    in hopes that database status will change. After maxReceiveCount (see
    CloudFormation) total tries, message goes to error (dead letter) queue.

  Batch item success:
    Event message is deleted from main queue. Do not retry, because:
      1. Database was already stopped, deleted, or being deleted, or
      2. It was not possible to request stopping, due to:
         a. An unexpected error while trying
            (boto3 handles transient errors; see retries config parameter)
         b. An abnormal database status that won't change or is unfamiliar
  """
  log("LAMBDA_EVENT", lambda_event, INFO)
  batch_item_failures = []

  for sqs_message in lambda_event.get("Records", []):
    log("SQS_MESSAGE", sqs_message, INFO)
    sqs_message_id = ""

    method_name = ""
    stop_db_kwargs = {}
    result = None
    log_level = INFO
    retry = FOLLOW_UNTIL_STOPPED

    try:
      sqs_message_id = sqs_message["messageId"]
      db_event = json_loads(sqs_message["body"])
      db_event_detail = db_event["detail"]
      # Events have "CLUSTER" (Aurora) or "DB_INSTANCE" (RDS); take last word:
      source_type_word = db_event_detail["SourceType"].split("_")[-1]
      source_identifier = db_event_detail["SourceIdentifier"]

      method_name = f"stop_db_{source_type_word.lower()}"
      stop_db_kwargs = {
        f"DB{source_type_word.title()}Identifier": source_identifier,
      }
      result = op_do(method_name, stop_db_kwargs)
      if isinstance(result, Exception):
        (log_level, retry) = assess_stop_db_exception(
          lambda_event, sqs_message, source_type_word, result, stop_db_kwargs
        )

    except Exception as misc_exception:  # pylint: disable=broad-exception-caught
      result = misc_exception
      log_level = ERROR
      retry = False

    op_log(
      lambda_event,
      sqs_message,
      method_name,
      stop_db_kwargs,
      result,
      log_level
    )

    if retry and sqs_message_id:
      batch_item_failures.append({"itemIdentifier": sqs_message_id})

  # https://repost.aws/knowledge-center/lambda-sqs-report-batch-item-failures
  return {"batchItemFailures": batch_item_failures}

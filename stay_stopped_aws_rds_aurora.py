#!/usr/bin/env python3
"""Stop AWS RDS and Aurora databases after the forced 7th-day start

github.com/sqlxpert/stay-stopped-aws-rds-aurora  GPLv3  Copyright Paul Marcelin
"""

import logging
import json
import re
import botocore
import boto3

logger = logging.getLogger()
# Skip "credentials in environment" INFO message, unavoidable in AWS Lambda:
logging.getLogger("botocore").setLevel(logging.WARNING)


def log(entry_type, entry_value, log_level):
  """Emit a JSON-format log entry
  """
  entry_value_out = json.loads(json.dumps(entry_value, default=str))
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


def op_log(
  lambda_event, op_method_name, op_kwargs, main_entry_value, log_level
):
  """Log Lambda function event, boto3 operation kwargs, response or exception

  A response that preceded a downstream error can be logged at log_level
  logging.ERROR instead of logging.INFO , or an expected exception can be
  logged at logging.INFO instead of logging.ERROR .
  """
  if log_level > logging.INFO:
    log("LAMBDA_EVENT", lambda_event, log_level)
  log(f"KWARGS_{op_method_name.upper()}", op_kwargs, log_level)
  main_entry_type = (
    "EXCEPTION" if isinstance(main_entry_value, Exception) else "AWS_RESPONSE"
  )
  log(main_entry_type, main_entry_value, log_level)


# Use "status" except in the context of parsing an Aurora
# InvalidDBClusterStateFault error message, which refers to "state".

INVALID_DB_CLUSTER_STATE_RE = re.compile(
  r"DbCluster \S+ is in (?P<db_cluster_state>\S+) state "
)


def extract_db_cluster_state(error_msg):
  """Take an InvalidDBClusterStateFault error message, return cluster state

  None indicates state not found
  """
  db_cluster_state_re_match = INVALID_DB_CLUSTER_STATE_RE.match(error_msg)
  return (
    db_cluster_state_re_match.group("db_cluster_state")
    if db_cluster_state_re_match else
    None
  )


def get_db_instance_status(lambda_event, describe_db_kwargs):
  """Take describe_db_instances kwargs, return RDS database instance status

  None indicates an error
  """
  log_level = logging.ERROR
  db_instance_status = None

  describe_db_method_name = "describe_db_instances"
  describe_db_result = op_do(
    describe_db_method_name,
    describe_db_kwargs
  )
  if not isinstance(describe_db_result, Exception):
    db_instances = describe_db_result.get("DBInstances", [])
    if len(db_instances) == 1:
      db_instance_status = db_instances[0].get("DBInstanceStatus")
      if db_instance_status is not None:
        log_level = logging.INFO

  op_log(
    lambda_event,
    describe_db_method_name,
    describe_db_kwargs,
    describe_db_result,
    log_level
  )

  return db_instance_status


def assess_db_status(db_status):
  """Take database status, return log level and retry flag

  Focus is on statuses that might temporarily or permanently preclude
  stop_db_instance or stop_db_cluster , i.e., not "available".

  Aurora database cluster:
    https://docs.aws.amazon.com/en_us/AmazonRDS/latest/AuroraUserGuide/accessing-monitoring.html#Aurora.Status

  Aurora database instance (information only; instance status not assessed)
    https://docs.aws.amazon.com/en_us/AmazonRDS/latest/AuroraUserGuide/accessing-monitoring.html#Overview.DBInstance.Status

  RDS database instance:
    https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/accessing-monitoring.html#Overview.DBInstance.Status
  """
  log_level = logging.ERROR
  retry = False

  if db_status is None:
    retry = True
    # Status could not be determined this time

  else:
    match db_status.lower():
      # Unless noted, same status values (normalized to lower case) for
      # Aurora database cluster and RDS database instance.

      case "deleting" | "deleted" | "stopped":
        log_level = logging.INFO

      case (
          "starting"
        | "stopping"  # To check for successful completion
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
        log_level = logging.INFO
        retry = True
        # Also monitor error (dead letter) queue; database will not be stopped
        # if operations take longer than VisibilityTimeout * maxReceiveCount
        # (SQS main queue properties in CloudFormation).

      case (
          "inaccessible-encryption-credentials-recoverable"
        # RDS database instance only:
        | "incompatible-network"
        | "incompatible-option-group"
        | "incompatible-parameters"
      ):
        log_level = logging.ERROR
        retry = True

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
        pass  # Just wanted to list the known no-retry error conditions!

  return (log_level, retry)


def assess_db_invalid_parameter(error_message):
  """Take InvalidParameterCombination message, return log level and retry flag
  """
  log_level = logging.ERROR
  retry = False

  if (
    ("aurora" in error_message)
    and ("not eligible for stopping" in error_message)
  ):
    log_level = logging.INFO
    # Quietly ignore database instance-level event for Aurora,
    # because there will be a corresponding database cluster-level event.
    # https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#USER_Events.Messages.instance
    # https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-cluster-stop-start.html#aurora-cluster-start-stop-overview

  return (log_level, retry)


def assess_stop_db_exception(
  lambda_event, source_type_word, misc_exception, describe_db_kwargs
):
  """Take a boto3 exception, return log level and retry flag

  ClientError is general but statically-defined, making comparison
  easier than for RDS-specific but dynamically-defined exceptions
  like InvalidDBClusterStateFault and InvalidDBInstanceStateFault .

  https://boto3.amazonaws.com/v1/documentation/api/latest/guide/error-handling.html#parsing-error-responses-and-catching-exceptions-from-aws-services
  """
  log_level = logging.ERROR
  retry = False

  if isinstance(misc_exception, botocore.exceptions.ClientError):
    error_dict = getattr(misc_exception, "response", {}).get("Error", {})
    error_message = error_dict.get("Message", "")
    match error_dict.get("Code"):

      case "InvalidDBClusterStateFault":
        db_status = extract_db_cluster_state(error_message)
        (log_level, retry) = assess_db_status(db_status)

      case "InvalidDBInstanceState":  # "Fault" suffix is missing here!
        if source_type_word == "CLUSTER":
          # stop_db_cluster produces invalid status exceptions not just for
          # the database cluster but also for member database instances.
          # Retry in hopes that all members eventually reach acceptable
          # statuses. Could call describe_db_instances with
          #   Filters=[
          #     {"Name": "db-cluster-id", "Values": [DBClusterIdentifier]},
          #   ]
          # but the only benefits, in the rare case of an unrecoverable
          # member, would be fewer retries and a specific, error-level log
          # entry instead of a non-specific error (dead letter) queue entry.
          log_level = logging.INFO
          retry = True
        else:
          db_status = get_db_instance_status(lambda_event, describe_db_kwargs)
          (log_level, retry) = assess_db_status(db_status)

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
    rds_client = boto3.client(
      "rds", config=botocore.config.Config(retries={"mode": "standard"})
    )
  return rds_client


def op_do(op_method_name, op_kwargs):
  """Take a boto3 method name and kwargs, call, return response or exception
  """
  try:
    op_method = getattr(rds_client_get(), op_method_name)
    op_result = op_method(**op_kwargs)
  except Exception as misc_exception:  # pylint: disable=broad-exception-caught
    op_result = misc_exception
  return op_result


def lambda_handler(lambda_event, context):  # pylint: disable=unused-argument
  """Try to stop Aurora database clusters and RDS database instances

  Called in response to a batch of forced database start events (see
  EventPattern in CloudFormation) stored as messages in the main SQS queue.

  Batch item failure:
    Event message remains in main queue; retry after VisibilityTimeout ,
    up to maxReceiveCount (see CloudFormation) total times.

  Batch item success:
    Event message deleted from main queue; do not retry, because:
      1. Database was already stopped, deleted, or being deleted, or
      2. It was not possible to request that database be stopped, due to:
         a. An unexpected error while trying
            (boto3 handles transient errors; config parameter: retries )
         b. An error while getting RDS database instance status
         c. An abnormal, unexpected, or unfamiliar database status
  """
  log("LAMBDA_EVENT", lambda_event, logging.INFO)
  batch_item_failures = []

  for sqs_message in lambda_event.get("Records", []):

    try:
      sqs_message_id = sqs_message["messageId"]
      db_event = json.loads(sqs_message["body"])
      db_event_detail = db_event["detail"]

      # Events have "CLUSTER" (Aurora) or "DB_INSTANCE" (RDS); take last word
      source_type_word = db_event_detail["SourceType"].split("_")[-1]
      source_identifier = db_event_detail["SourceIdentifier"]

      log_level = logging.INFO
      retry = True

      stop_db_method_name = f"stop_db_{source_type_word.lower()}"
      stop_db_kwargs = {
        f"DB{source_type_word.title()}Identifier": source_identifier,
      }
      stop_db_result = op_do(stop_db_method_name, stop_db_kwargs)
      if isinstance(stop_db_result, Exception):
        (log_level, retry) = assess_stop_db_exception(
          lambda_event, source_type_word, stop_db_result, stop_db_kwargs
        )

      op_log(
        lambda_event,
        stop_db_method_name,
        stop_db_kwargs,
        stop_db_result,
        log_level
      )

      if retry:
        batch_item_failures.append({"itemIdentifier": sqs_message_id, })

    except Exception as misc_exception:  # pylint: disable=broad-exception-caught
      log_level = logging.ERROR
      log("LAMBDA_EVENT", lambda_event, log_level)
      log("SQS_MESSAGE", sqs_message, log_level)
      log("EXCEPTION", misc_exception, log_level)

  # https://repost.aws/knowledge-center/lambda-sqs-report-batch-item-failures
  return {"batchItemFailures": batch_item_failures, }

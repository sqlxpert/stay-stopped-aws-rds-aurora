# Stay Stopped, RDS and Aurora!

You can keep an EC2 compute instance stopped as long as you want, but it's not
possible to stop an RDS or Aurora database longer than 7 days. When AWS starts
your database on the 7th day, this tool automatically stops it again.

It's for databases you use sporadically, maybe for development and testing. If
it would cost too much to keep a database running all the time but take too
long to re-create it, this tool might save you money, time, or both.

AWS does not charge for database instance hours while an
[RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html#USER_StopInstance.Benefits)
or
[Aurora](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-cluster-stop-start.html#aurora-cluster-start-stop-overview)
database is stopped. (Other charges, such as for storage and snapshots,
will continue.)

Jump to:
[Get Started](#get-started)
&bull;
[Multi-Account, Multi-Region](#multi-account-multi-region)
&bull;
[Terraform](#terraform)
&bull;
[Security](#security)

## Design

The design is simple but robust:

- You can start your database manually or on a schedule (try
  [github.com/sqlxpert/lights-off-aws](/../../../lights-off-aws#lights-off)
  !) whenever you like. This tool will not interfere.

- This tool only stops databases that _AWS_ is starting after they've been
  stopped for 7 days:
  [RDS-EVENT-0154](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.Messages.html#USER_Events.Messages.instance)
  (RDS)
  and
  [RDS-EVENT-0153](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#USER_Events.Messages.cluster)
  (Aurora).
  You do not need to set any opt-in or opt-out tags. As long as _you_, rather
  than _AWS_, started your database, this tool will not try to stop it.

- Stopping stuff is inherently idempotent: keep trying until it is stopped!
  Some well-intentioned Step Function solutions introduce an intermittent bug
  (a
  [race condition](https://en.wikipedia.org/wiki/Race_condition))
  by checking whether a database is ready _before_ trying to stop it. This
  tool intercepts temporary errors and keeps trying every 9 minutes until the
  database is stopped, an unexpected error occurs, or 24 hours pass.

- It's not enough to call `stop_db_instance` or `stop_db_cluster` and hope for
  the best. Unlike the typical "Hello, world!"-level AWS Lambda functions
  you'll find &mdash; even in some official AWS re:Post knowledge base
  solutions &mdash; this tool handles error cases. Look for a queue message or
  a log entry, in case something unexpected prevented stopping your database.
  [Budget alerts](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-action-configure.html)
  and
  [cost anomaly detection](https://docs.aws.amazon.com/cost-management/latest/userguide/manage-ad.html)
  are still essential.

- It's still important to start a database before its maintenance window and
  leave it running, once in a while.

## Get Started

 1. Log in to the AWS Console as an administrator. Choose an AWS account and a
    region where you have an RDS or Aurora database that is normally stopped,
    or that you can stop now and leave stopped for 8 days.

 2. Create a
    [CloudFormation stack](https://console.aws.amazon.com/cloudformation/home)
    "With new resources (standard)". Select "Upload a template file", then
    select "Choose file" and navigate to a locally-saved copy of
    [stay_stopped_rds_aurora.yaml](/stay_stopped_aws_rds_aurora.yaml?raw=true)
    [right-click to save as...]. On the next page, set:

    - Stack name: `StayStoppedRdsAurora`

 3. Wait 8 days, then check that your
    [RDS or Aurora database](https://console.aws.amazon.com/rds/home#databases:)
    is in the stopped state.

    - So much for a "quick" start! If you don't want to wait, see
      [Testing](#testing),
      below.

 4. Optional: Double-check in the
    [StayStopped CloudWatch log group](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups$3FlogGroupNameFilter$3DStayStoppedRdsAurora-).

## Multi-Account, Multi-Region

For reliability, Stay Stopped works completely independently in each region,
in each AWS account. To deploy in multiple regions and/or AWS accounts,

 1. Delete any standalone `StayStoppedRdsAurora` CloudFormation _stacks_ in
    your target regions and/or AWS accounts.

 2. Complete the prerequisites for creating a _StackSet_ with
    [service-managed permissions](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-enable-trusted-access.html).

 3. In the management AWS account (or a delegated administrator account),
    create a
    [CloudFormation StackSet](https://console.aws.amazon.com/cloudformation/home#/stacksets).
    Select Upload a template file, then select Choose file and upload a
    locally-saved copy of
    [stay_stopped_rds_aurora.yaml](/stay_stopped_aws_rds_aurora.yaml?raw=true)
    [right-click to save as...]. On the next page, set:

    - StackSet name: `StayStoppedRdsAurora`

 4. Two pages later, under Deployment targets, select Deploy to Organizational
    Units. Enter your target `ou-` ID. Stay Stopped will be deployed in all
    AWS accounts in your target OU. Toward the bottom of the page, specify
    your target region(s).

## Terraform

Terraform users are often willing to wrap a CloudFormation stack in HashiCorp
Configuration Language, because AWS supplies tools in the form of
CloudFormation templates. See
[aws_cloudformation_stack](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudformation_stack)
.

Wrapping a CloudFormation StackSet in HCL is much easier than configuring and
using Terraform to deploy and maintain identical resources in multiple regions
and/or AWS accounts. See
[aws_cloudformation_stack_set](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudformation_stack_set)
.

## Security

_In accordance with the software license, nothing in this section establishes
indemnification, a warranty, assumption of liability, etc. Use this software
entirely at your own risk. Paul encourages you to review the source code._

<details>
  <summary>Security details...</summary>

### Security Design Goals

- A least-privilege role for the AWS Lambda function.

- Least-privilege queue policies. The main queue can only consume messages
  from EventBridge and produce messages for the Lambda function, or for the
  error (dead letter) queue if there is a problem. Encryption in transit is
  required.

- Optional encryption at rest with the AWS Key Management System, for the
  queues and the log. This can protect EventBridge events containing database
  identifiers and metadata, such as tags. KMS keys housed in a different AWS
  account, and multi-region keys, are supported.

- No data storage other than in the queues and the log, both of which have
  configurable retention periods.

- A retry mechanism (every 9 minutes) and a time limit (24 hours), to increase
  the likelihood that a database will be stopped as intended.

- A concurrency limit, to prevent exhaustion of available Lambda resources.

- Readable Identity and Access Management policies, formatted as
  CloudFormation YAML rather than JSON, and broken down into discrete
  statements by service, resource or principal.

### Your Security Steps

- Prevent people from modifying components of this tool, most of which can be
  identified by `StayStoppedRdsAurora` in ARNs and in the automatic
  `aws:cloudformation:stack-name` tag.

- Log infrastructure changes using CloudTrail, and set up alerts.

- Prevent people from directly invoking the Lambda function and from passing
  the function role to arbitrary functions.

- Separate production workloads. Although this tool only affects databases
  that _AWS_ is starting after they've been stopped for 7 days, the Lambda
  function has permission to stop any RDS or Aurora database and could do so
  if invoked directly, with a contrived event as input. You might choose not
  to deploy this tool in AWS accounts used for production, or you might add a
  custom IAM policy to the function role, denying authority to stop certain
  production databases (`AttachLocalPolicy` in CloudFormation).

- Enable the test mode only in a non-critical AWS account and region, and turn
  the test mode off again as quickly as possible.

- Monitor the error (dead letter) queue, and monitor the log for `ERROR`-level
  entries.

- Configure [budget alerts](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-action-configure.html)
  and use
  [cost anomaly detection](https://docs.aws.amazon.com/cost-management/latest/userguide/manage-ad.html).

- Occasionally start a database before its maintenance window and leave it
  running, to catch up with RDS and Aurora security updates.

</details>

## Troubleshooting

Check the:

- [StayStopped CloudWatch log group](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups$3FlogGroupNameFilter$3DStayStoppedRdsAurora-)
  - Log entries are JSON objects.
    - Stay Stopped includes `"level"` , `"type"` and `"value"` keys.
    - Other software components may use different keys.
  - For more data, change the `LogLevel` in CloudFormation.
  - Scrutinize log entries at the `ERROR` level.
    - One `InvalidDBInstanceState` or `InvalidDBClusterStateFault` entry at
      the `ERROR` level indicates that a database could not be stopped because
      it was in a highly irregular state. Multiple such entries for the same
      resource indicate that a database was in an irregular but potentially
      recoverable state. Stay Stopped retries every 9 minutes, until 24 hours
      have passed.
- `ErrorQueue` (dead letter)
  [SQS queue](https://console.aws.amazon.com/sqs/v3/home#/queues)
  - Queue messages are EventBridge events for RDS or Aurora forced database
    start.
  - A message in this queue indicates that Stay Stopped could not stop a
    database after trying for 24 hours.
- [CloudTrail Event history](https://console.aws.amazon.com/cloudtrailv2/home?ReadOnly=false/events?ReadOnly=false)
  - CloudTrail events with an "Error code" may indicate permissions problems.
  - To see more events, change "Read-only" from `false` to `true` .

## Testing

<details>
  <summary>Testing details...</summary>

AWS starts RDS and Aurora databases that have been stopped for 7 days, but we
need a faster mechanism for realistic, end-to-end testing. When you
temporarily change `Test` to `true` in CloudFormation, Stay Stopped:

- Responds to user-initiated, non-forced database starts:
  [RDS-EVENT-0088](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.Messages.html#USER_Events.Messages.instance)
  (RDS)
  and
  [RDS-EVENT-0151](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#USER_Events.Messages.cluster)
  (Aurora). Although Stay Stopped won't stop databases that have already been
  started, it **will stop any database that you create or start**.

- Relaxes the queue policy for the main SQS queue, allowing message sources
  other than EventBridge, and targets other than the Lambda function or the
  error (dead letter) queue. Using the AWS Console, you can send test
  EventBridge event messages to stop particular databases. In the list of
  [SQS queues](https://console.aws.amazon.com/sqs/v3/home#/queues),
  select `StayStoppedRdsAurora-MainQueue` and then select the "Send and
  receive messages" button above the list. You can "Send message". If
  necessary, you can also "Poll for messages", select a message, read it and
  delete it.

Given the operational and security risks, change `Test` back to `false` to
**exit test mode as quickly as possible**. Several minutes should be
sufficient, if you have a test database ready.

Paul recommends testing on an RDS database instance ( `db.t4g.micro` ,
`20` GiB of gp3 storage, `0` days' worth of automated backups). This is
cheaper than a typical Aurora cluster, not to mention faster to create, stop,
and start.

You can insert the names of your RDS database instance and Aurora database
cluster and use the following as a minimal Lambda function test event:

```json
{
  "Records": [
    {
      "messageId": "8314a964-e5b5-479a-8abe-b1954b1e8020",
      "body": "{ \"version\": \"0\", \"source\": \"aws.rds\", \"detail-type\": \"RDS DB Instance Event\", \"detail\": { \"SourceIdentifier\": \"MY_RDS_DATABASE_INSTANCE\", \"SourceType\": \"DB_INSTANCE\", \"EventID\": \"RDS-EVENT-0154\" } }"
    },
    {
      "messageId": "8314a964-e5b5-479a-8abe-b1954b1e8021",
      "body": "{ \"version\": \"0\", \"source\": \"aws.rds\", \"detail-type\": \"RDS DB Cluster Event\", \"detail\": { \"SourceIdentifier\": \"MY_AURORA_DATABASE_CLUSTER\", \"SourceType\": \"CLUSTER\", \"EventID\": \"RDS-EVENT-0153\" } }"
    }
  ]
}

```

For further help with testing, temporarily change:

- `LogLevel` from `ERROR` to `INFO`
- `QueueVisibilityTimeoutSecs` from `540` to `60`
- `QueueMaxReceiveCount` from `160` (24 hours, at one retry every 9 minutes)
   to `6` (54 minutes)

After ruling out local causes such as permissions &mdash; especially Service
and Resource control policies (SCPs and RCPs) &mdash; please
[report bugs](/../../issues).

</details>

## Licenses

|Scope|Link|Included Copy|
|:---|:---|:---|
|Source code, and source code in documentation|[GNU General Public License (GPL) 3.0](http://www.gnu.org/licenses/gpl-3.0.html)|[LICENSE-CODE.md](/LICENSE-CODE.md)|
|Documentation, including this ReadMe file|[GNU Free Documentation License (FDL) 1.3](http://www.gnu.org/licenses/fdl-1.3.html)|[LICENSE-DOC.md](/LICENSE-DOC.md)|

Copyright Paul Marcelin

Contact: `marcelin` at `cmu.edu` (replace "at" with `@`)

# Stay Stopped, RDS and Aurora!

You can keep an EC2 compute instance stopped as long as you want, but it's not
possible to stop an RDS database instance or an Aurora database cluster longer
than 7 days. After AWS starts your database on the 7th day, this tool
automatically stops it again.

It's for databases you use sporadically, maybe for development and testing. If
it would cost too much to keep a database running all the time but take too
long to re-create it, this tool might save you money, time, or both.

AWS does not charge for database instance hours while an
[RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html#USER_StopInstance.Benefits)
or
[Aurora](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-cluster-stop-start.html#aurora-cluster-start-stop-overview)
database is stopped. (Other charges, such as for storage and snapshots, will
continue.)

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

- You can start your database manually or on a schedule (check out
  [github.com/sqlxpert/lights-off-aws](/../../../lights-off-aws#lights-off)
  ! ), whenever you like.

- This tool only stops databases that _AWS_ is starting after they've been
  stopped for 7 days:
  [RDS-EVENT-0154](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.Messages.html#USER_Events.Messages.instance)
  (RDS)
  and
  [RDS-EVENT-0153](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#USER_Events.Messages.cluster)
  (Aurora).
  You do not need to set any opt-in or opt-out tags. As long as _you_, rather
  than _AWS_, started your database this time, Stay Stopped won't stop it.

- Stopping stuff is inherently idempotent: keep trying until it is stopped!
  Some otherwise very intenti solutions introduce a latent bug (a
  [race condition](https://en.wikipedia.org/wiki/Race_condition))
  by checking whether a database is ready _before_ trying to stop it. This
  tool tries every 9 minutes until the database is stopped, an unexpected
  error occurs, or 24 hours pass.

  <details>
    <summary>About idempotence and latent bugs...</summary>

  Here are two of the best solutions found online for keeping an RDS or Aurora
  database stopped, one singled out for its simplicity and the other, for its
  thoroughness. The artifacts are from May, 2025, and newer versions may be
  available by the time you read this.

  [Stop Amazon RDS/Aurora Whenever They Start](https://aws.plainenglish.io/stop-amazon-rds-aurora-whenever-they-start-with-lambda-and-eventbridge-c8c1a88f67d6)
  \[[code](https://gist.github.com/shimo164/cc9bb3c425e13f0f2fa14f29c633aa84/0e714a830352e6e6d29904e0629b82df5473393f)\]
  by shimo, from the _AWS In Plain English_ blog on Medium, avoids the
  complexity of an AWS Step Function or an SQS queue. The single Lambda
  function checks that the database is `available` before stopping it
  ([L48-L51](https://gist.github.com/shimo164/cc9bb3c425e13f0f2fa14f29c633aa84/0e714a830352e6e6d29904e0629b82df5473393f#file-lambda_stop_rds-py-L48-L51)).
  If not, the code waits
  ([L63-L65](https://gist.github.com/shimo164/cc9bb3c425e13f0f2fa14f29c633aa84/0e714a830352e6e6d29904e0629b82df5473393f#file-lambda_stop_rds-py-L63-L65))
  and checks again
  ([L76-L78](https://gist.github.com/shimo164/cc9bb3c425e13f0f2fa14f29c633aa84/0e714a830352e6e6d29904e0629b82df5473393f#file-lambda_stop_rds-py-L76-L78)).
  After the database finishes `starting` and becomes `available`, what if a
  long `maintenance` procedure begins _before_ the next status check? What if
  the database just takes long to start? "The startup process can take minutes
  to hours", according to the
  [RDS User Guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StartInstance.html).
  There might not be time to stop the database before the
  [15-minute Lambda time-out](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html)!

  [Stopping an Automatically Started Database Instance](https://aws.amazon.com/jp/blogs/architecture/field-notes-stopping-an-automatically-started-database-instance-with-amazon-rds/)
  \[[code](https://github.com/aws-samples/amazon-rds-auto-restart-protection/tree/cfdd3a1)\]
  by Islam Ghanim, on AWS's own _Architecture Blog_, uses a Step Function
  to increase reliability by seeing the stop attempt through until the
  database's status changes from `stopping` to `stopped`. Before attempting
  to stop the database, the state machine waits as long as it takes for the
  database to become `available`. After the database finishes `starting` and
  becomes `available`, what if an independent person or automated system
  deletes it? That's far-fetched, but what if the independent actor simply
  _stops_ the database before the next status check? Because `available` is
  the only non-error way out of the status check loop
  ([stop-rds-instance-state-machine.json, L30-L40](https://github.com/aws-samples/amazon-rds-auto-restart-protection/blob/48e0587/sources/stepfunctions-code/stop-rds-instance-state-machine.json#L30-L40)),
  and no
  [timeout](https://docs.aws.amazon.com/step-functions/latest/dg/statemachine-structure.html#statemachinetimeoutseconds)
  is defined at the top level
  ([L1-L4](https://github.com/aws-samples/amazon-rds-auto-restart-protection/blob/48e0587/sources/stepfunctions-code/stop-rds-instance-state-machine.json#L1-L4)),
  the Step Function would keep running until AWS starts the database again
  next week. With a status check every 5 minutes, that's a lot of Lambda time!

  ![retrieveRdsInstanceState, isInstanceAvailable, and waitFiveMinutes are joined in a loop. The only exit paths are from isInstanceAvailable to stopRdsInstance if rdsInstanceState is "available"; and from retrieveRdsInstanceState and stopRdsInstance to fallback, if an error is caught](media/stop-rds-instance-state-machine-part.png "Part of the AWS Architecture Blog solution's state machine")

  The point is certainly not to criticize. Rather, it's to demonstrate that
  the problem is not as simple as it seemed originally. The pitfalls, and the
  ways to solve them, apply to many of the distributed computing problems that
  we work on. Each professional who tackles a problem contributes a piece of
  the puzzle, and we learn from each other. Please get in touch if you have
  ideas for improving Stay Stopped!

  For further reading:

  - [Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
    by Malcolm Featonby, in the _Amazon Builder's Library_.

  - [Idempotence: Doing It More than Once](https://sqlxpert.github.io/2025/05/17/idempotence-doing-it-more-than-once.html),
    by yours truly.

  </details>

- It's not enough to call `stop_db_instance` or `stop_db_cluster` and hope for
  the best. This tool handles error cases. Look for a queue message or a log
  entry, in case something unexpected prevented stopping your database.
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
    is in the stopped state. So much for a "quick" start! If you don't want to
    wait, see
    [Testing](#testing),
    below.

 4. Optional: Double-check in the
    [StayStopped CloudWatch log group](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups$3FlogGroupNameFilter$3DStayStoppedRdsAurora-).

## Multi-Account, Multi-Region

For reliability, Stay Stopped works completely independently in each region, in
each AWS account. To deploy in multiple regions and/or AWS accounts,

 1. Delete any standalone `StayStoppedRdsAurora` CloudFormation _stacks_ in
    your target regions and/or AWS accounts.

 2. Complete the prerequisites for creating a _StackSet_ with
    [service-managed permissions](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-enable-trusted-access.html).

 3. In the management AWS account (or a delegated administrator account),
    create a
    [CloudFormation StackSet](https://console.aws.amazon.com/cloudformation/home#/stacksets).
    Select "Upload a template file", then select "Choose file" and upload a
    locally-saved copy of
    [stay_stopped_rds_aurora.yaml](/stay_stopped_aws_rds_aurora.yaml?raw=true)
    [right-click to save as...]. On the next page, set:

    - StackSet name: `StayStoppedRdsAurora`

 4. Two pages later, under "Deployment targets", select "Deploy to
    Organizational Units". Enter your target `ou-` identifier. Stay Stopped
    will be deployed in all AWS accounts in your target OU. Toward the bottom
    of the page, specify your target region(s).

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

- Separate production workloads. Although this tool only stops databases that
  _AWS_ is starting after they've been stopped for 7 days, the Lambda function
  could stop _any_ database if invoked directly, with a contrived event as
  input. You might choose not to deploy this tool in AWS accounts used for
  production, or you might add a custom IAM policy to the function role,
  denying authority to stop certain production databases (`AttachLocalPolicy`
  in CloudFormation).

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
  - Scrutinize log entries at the `ERROR` level:

    `InvalidDBInstanceState` or `InvalidDBClusterStateFault` :

    - One time:
      A database could not be stopped because it was in a highly irregular
      state.
    - Multiple times for the same database:
      The database was in an irregular but potentially recoverable state. Stay
      Stopped retries every 9 minutes, until 24 hours have passed.

  - Log entries are JSON objects.
    - Stay Stopped includes `"level"` , `"type"` and `"value"` keys.
    - Other software components may use different keys.
  - For more data, change the `LogLevel` in CloudFormation.
- `ErrorQueue` (dead letter)
  [SQS queue](https://console.aws.amazon.com/sqs/v3/home#/queues)
  - A message in this queue indicates that Stay Stopped could not stop a
    database after trying for 24 hours.
  - Queue messages are EventBridge events for RDS or Aurora forced database
    start.
- [CloudTrail Event history](https://console.aws.amazon.com/cloudtrailv2/home?ReadOnly=false/events?ReadOnly=false)
  - CloudTrail events with an "Error code" may indicate permissions problems.
  - To see more events, change "Read-only" from `false` to `true` .

## Testing

<details>
  <summary>Testing details...</summary>

### Recommended Test Database

An RDS database instance ( `db.t4g.micro` , `20` GiB of gp3 storage, `0` days'
worth of automated backups) is cheaper than a typical Aurora cluster, not to
mention faster to create, stop, and start.

### Test Mode

AWS starts RDS and Aurora databases that have been stopped for 7 days, but we
need a faster mechanism for realistic, end-to-end testing. Temporarily change
these parameters in CloudFormation:

|Parameter|Normal|Test|
|:---|:---:|:---:|
|`Test`|`false`|`true`|
|`LogLevel`|`ERROR`|`INFO`|
|`QueueVisibilityTimeoutSecs`|`540`|`60`|
||Retry every 9 minutes|Retry every 1 minute|
|`QueueMaxReceiveCount`|`160`|`30`|
||24 hours, at one retry every 9 minutes|30 minutes, at one retry every 1 minute|

Given the operational and security risks explained below, **exit test mode as
quickly as possible**. If your test database is ready, several minutes should
be sufficient.

### Test by Manually Starting a Database

In test mode, Stay Stopped responds to user-initiated, non-forced database
starts:
[RDS-EVENT-0088](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.Messages.html#USER_Events.Messages.instance)
(RDS)
and
[RDS-EVENT-0151](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#USER_Events.Messages.cluster)
(Aurora). Although this tool won't stop databases that have already been
started, it **will stop any database that you create or start**. To test,
manually start a stopped
[RDS or Aurora database](https://console.aws.amazon.com/rds/home#databases:).

### Test by Sending a Message

Test mode relaxes the queue policy for the main SQS queue, allowing sources
other than EventBridge, and targets other than the Lambda function or the
error (dead letter) queue. Test by using the AWS Console to send a simulated
EventBridge event message. In the list of
[SQS queues](https://console.aws.amazon.com/sqs/v3/home#/queues),
select `StayStoppedRdsAurora-MainQueue` and then select the "Send and receive
messages" button above the list. You can:

- "Send message", or
- "Poll for messages", select a message, read it and delete it, or
- "Purge" all messages.

Edit the database names in these test messages:

```json
{
  "detail": {
    "SourceIdentifier": "NAME_OF_YOUR_RDS_DATABASE_INSTANCE",
    "SourceType": "DB_INSTANCE",
    "EventID": "RDS-EVENT-0154"
  },
  "detail-type": "RDS DB Instance Event",
  "source": "aws.rds",
  "version": "0"
}
```

```json
{
  "detail": {
    "SourceIdentifier": "NAME_OF_YOUR_AURORA_DATABASE_INSTANCE",
    "SourceType": "CLUSTER",
    "EventID": "RDS-EVENT-0153"
  },
  "detail-type": "RDS DB Cluster Event",
  "source": "aws.rds",
  "version": "0"
}
```

### Test by Invoking the Lambda Function

Depending on locally-determined permissions, you may also be able to invoke
the
[StayStopped Lambda function](https://console.aws.amazon.com/lambda/home#/functions?fo=and&o0=%3A&v0=StayStoppedRdsAurora-LambdaFn-)
manually. Edit the database names in this Lambda test event:

```json
{
  "Records": [
    {
      "body": "{ \"detail\": { \"SourceIdentifier\": \"NAME_OF_YOUR_RDS_DATABASE_INSTANCE\", \"SourceType\": \"DB_INSTANCE\", \"EventID\": \"RDS-EVENT-0154\" }, \"detail-type\": \"RDS DB Instance Event\", \"source\": \"aws.rds\", \"version\": \"0\"}",
      "messageId": "test-message-1-rds"
    },
    {
      "body": "{ \"detail\": { \"SourceIdentifier\": \"NAME_OF_YOUR_AURORA_DATABASE_INSTANCE\", \"SourceType\": \"CLUSTER\", \"EventID\": \"RDS-EVENT-0153\" }, \"detail-type\": \"RDS DB Cluster Event\", \"source\": \"aws.rds\", \"version\": \"0\"}",
      "messageId": "test-message-2-aurora"
    }
  ]
}
```

### Report Bugs

After following the
[troubleshooting](#troubleshooting)
steps and ruling out local issues such as permissions &mdash; especially
hidden controls such as Service and Resource control policies (SCPs and RCPs)
&mdash; please
[report bugs](/../../issues). Thank you!

</details>

## Licenses

|Scope|Link|Included Copy|
|:---|:---|:---|
|Source code, and source code in documentation|[GNU General Public License (GPL) 3.0](http://www.gnu.org/licenses/gpl-3.0.html)|[LICENSE-CODE.md](/LICENSE-CODE.md)|
|Documentation, including this ReadMe file|[GNU Free Documentation License (FDL) 1.3](http://www.gnu.org/licenses/fdl-1.3.html)|[LICENSE-DOC.md](/LICENSE-DOC.md)|

Copyright Paul Marcelin

Contact: `marcelin` at `cmu.edu` (replace "at" with `@`)

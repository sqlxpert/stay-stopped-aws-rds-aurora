# Stay Stopped, RDS and Aurora!

You can keep an EC2 instance stopped as long as you want, but if you stop an
RDS or Aurora database, AWS restarts it after 7 days.

It's not possible to stop an RDS or Aurora database longer than 7 days, so
this tool automatically stops the database again.

It's for databases you use sporadically, perhaps for development and testing.
If it would cost too much to keep a database running all the time but take too
long to re-create it, this tool might save you money, time, or both.

## Design

The design is simple but robust:

- There are no opt-in or opt-out tags to set. This tool only affects databases
  that _AWS_ is starting after they've been stopped for 7 days:
  [RDS-EVENT-0154](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.Messages.html#USER_Events.Messages.instance)
  (RDS)
  and
  [RDS-EVENT-0153](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Events.Messages.html#USER_Events.Messages.cluster)
  (Aurora).

- Stopping stuff is inherently idempotent: keep trying until it is stopped!
  Some well-intentioned Step Function solutions introduce an intermittent bug
  (a
  [race condition](https://en.wikipedia.org/wiki/Race_condition))
  by checking whether a database is ready _before_ trying to stop it. This
  tool intercepts expected, temporary errors and keeps trying every 9 minutes
  until the database is stopped, an unexpected error occurs, or 24 hours pass.

- It's not enough to call `stop_db_instance` or `stop_db_cluster` and hope for
  the best. Unlike the typical "Hello, world!"-level AWS Lambda functions
  you'll find &mdash; even in some official AWS re:Post knowledge base
  solutions &mdash; this tool checks for errors. Look for an `ERROR`-level
  log entry or an error (dead letter) queue entry, in case your database could
  not be stopped.
  [Budget alerts](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-action-configure.html)
  and
  [cost anomaly detection](https://docs.aws.amazon.com/cost-management/latest/userguide/manage-ad.html)
  are still essential.

- It's important to start a database before its maintenance window and leave it
  running, once in a while. This tool _might_ stop a database right before
  accumulated maintenance can begin.

## Quick Start

## Multi-Account, Multi-Region Installation

## Security

## Testing and Troubleshooting

## Licenses

|Scope|Link|Included Copy|
|:---|:---|:---|
|Source code, and source code in documentation|[GNU General Public License (GPL) 3.0](http://www.gnu.org/licenses/gpl-3.0.html)|[LICENSE-CODE.md](/LICENSE-CODE.md)|
|Documentation, including this ReadMe file|[GNU Free Documentation License (FDL) 1.3](http://www.gnu.org/licenses/fdl-1.3.html)|[LICENSE-DOC.md](/LICENSE-DOC.md)|

Copyright Paul Marcelin

Contact: `marcelin` at `cmu.edu` (replace "at" with `@`)

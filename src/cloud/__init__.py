"""Cloud adapters (AWS) behind the interfaces of INTERFACES.md, selected by environment variables (#9).

The application never calls AWS directly: it depends on the protocols of ``conversation.store``,
``conversation.lifecycle`` and ``gateway``, and these modules are the AWS implementations:

- ``dynamodb_store``          ``STORE_BACKEND=dynamodb``: SessionStore, CaseStore, ReviewStore.
- ``traces``                  TraceSink for Lambda (``TRACE_SINK``: stdout JSON for CloudWatch, or S3).
- ``dynamodb_gateway``        ``GATEWAY_BACKEND=dynamodb`` (or ``composite`` with
                              ``GATEWAY_DATA_BACKEND=dynamodb``): the #2 tools over the serving table.
- ``stepfunctions_lifecycle`` ``LIFECYCLE_BACKEND=stepfunctions``: the case lifecycle on the Step
                              Functions state machine of ``infra/stacks/workflow_stack.py`` plus the
                              steps its Lambdas run (``handlers/case_steps.py``, ``handlers/investigator.py``).
- ``lake_sync``               the small S3 copy the pipeline Lambda does around ``run_partition``.

boto3 is imported lazily (it is in the Lambda runtime; locally only the tests with moto need it).
Everything here is tested offline with moto (``tests/test_cloud_*.py``); nothing was run against AWS.
"""

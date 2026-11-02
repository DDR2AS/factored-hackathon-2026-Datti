"""Case lifecycle (plan v2 section 6): investigate -> await analyst -> notify customer.

Owner: andres. Waiting for the analyst uses a Step Functions task token, so a case can
wait for days at no cost. SLA timers are one-time EventBridge Scheduler schedules that
application code creates in this stack's schedule group when a case opens.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from .common import allow_bedrock, allow_task_responses, model_environment, python_function
from .config import StageConfig
from .data_stack import DataStack

_LAMBDA_TRANSIENT_ERRORS = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
]


class WorkflowStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, cfg: StageConfig, data: DataStack, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        environment = {**data.table_environment(), **model_environment(cfg)}
        state_machine_name = cfg.name("case-lifecycle")

        self.investigator = python_function(
            self,
            "InvestigatorFn",
            cfg,
            slug="investigator",
            handler="handlers.investigator.handler",
            description="G2 investigator: read-only gateway tools, cited report (INTERFACES.md #6)",
            memory_mb=1024,
            timeout=cdk.Duration.seconds(120),
            environment={**environment, "INVESTIGATOR_MAX_TOOL_CALLS": "12"},
        )
        data.serving.grant_read_data(self.investigator)
        data.cases.grant_read_write_data(self.investigator)  # the case and its report (ReviewStore items)
        data.traces.grant_put(self.investigator)
        data.artifacts.grant_read(self.investigator)
        allow_bedrock(self.investigator, cfg)

        self.case_steps = python_function(
            self,
            "CaseStepsFn",
            cfg,
            slug="case-steps",
            handler="handlers.case_steps.handler",
            description="Case lifecycle steps: task tokens, notifications, SLA timers",
            timeout=cdk.Duration.seconds(30),
            environment=environment,
        )
        data.cases.grant_read_write_data(self.case_steps)
        data.traces.grant_put(self.case_steps)
        # A retried AwaitAnalyst on a case already decided is answered at once (register_task_token).
        # No reference to the state machine here (it would be circular: the machine invokes this
        # function), and none is possible anyway: SendTask* take Resource "*" (common.py).
        allow_task_responses(self.case_steps)

        # --- SLA timers --------------------------------------------------------------------
        self.schedule_group_name = cfg.name("sla-timers")
        scheduler.CfnScheduleGroup(self, "SlaTimerGroup", name=self.schedule_group_name)
        self.scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            description="Lets EventBridge Scheduler fire SLA timers into the case-steps Lambda",
        )
        self.case_steps.grant_invoke(self.scheduler_role)

        # --- State machine ---------------------------------------------------------------
        def step(construct_id: str, action: str, **extra) -> tasks.LambdaInvoke:
            payload = {"action": action, "case_id.$": "$.case_id", **extra.pop("payload_extra", {})}
            task = tasks.LambdaInvoke(
                self,
                construct_id,
                lambda_function=self.case_steps,
                payload=sfn.TaskInput.from_object(payload),
                payload_response_only=extra.pop("payload_response_only", True),
                result_path=extra.pop("result_path", sfn.JsonPath.DISCARD),
                **extra,
            )
            task.add_retry(errors=_LAMBDA_TRANSIENT_ERRORS, max_attempts=2, backoff_rate=2)
            return task

        investigate = tasks.LambdaInvoke(
            self,
            "Investigate",
            lambda_function=self.investigator,
            payload=sfn.TaskInput.from_object({"case_id.$": "$.case_id"}),
            payload_response_only=True,
            result_path="$.investigation",
            task_timeout=sfn.Timeout.duration(cdk.Duration.seconds(150)),
        )
        investigate.add_retry(errors=_LAMBDA_TRANSIENT_ERRORS, max_attempts=2, backoff_rate=2)

        # Contract with src/cloud/stepfunctions_lifecycle.py (the API starts executions named
        # <case_id>-r<round> with input {case_id, money_dispute, round}).
        mark_incomplete = step(
            "MarkInvestigationIncomplete",
            "mark_incomplete",
            payload_extra={"error.$": "$.investigation_error.Error"},
        )
        await_analyst = step(
            "AwaitAnalyst",
            "register_task_token",
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload_extra={
                "task_token": sfn.JsonPath.task_token,
                "execution_arn.$": "$$.Execution.Id",
                "round.$": "$.round",
            },
            payload_response_only=False,
            result_path="$.decision",  # SendTaskSuccess output {action, next, status}
            task_timeout=sfn.Timeout.duration(cdk.Duration.days(30)),
        )
        notify_customer = step(
            "NotifyCustomer",
            "notify_customer",
            payload_extra={"decision.$": "$.decision"},
            result_path="$.notify",  # {close, close_after_seconds}
        )
        close_case = step("CloseCase", "close_case")
        wait_before_close = sfn.Wait(
            self, "WaitBeforeClose", time=sfn.WaitTime.seconds_path("$.notify.close_after_seconds")
        )
        investigate.add_catch(mark_incomplete, result_path="$.investigation_error")
        mark_incomplete.next(await_analyst)
        investigate.next(await_analyst)
        await_analyst.next(notify_customer).next(
            sfn.Choice(self, "CloseAfterNotice")
            .when(
                sfn.Condition.boolean_equals("$.notify.close", True),
                wait_before_close.next(close_case).next(sfn.Succeed(self, "CaseClosed")),
            )
            # escalated (handed_off) or waiting for the customer's answer: people take it from here
            .otherwise(sfn.Succeed(self, "LeftToPeople"))
        )

        start = sfn.Choice(self, "MoneyDispute").when(
            sfn.Condition.boolean_equals("$.money_dispute", True), investigate
        ).otherwise(await_analyst)

        self.state_machine = sfn.StateMachine(
            self,
            "CaseStateMachine",
            state_machine_name=state_machine_name,
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_chainable(start),
            timeout=cdk.Duration.days(60),
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=logs.LogGroup(
                    self,
                    "CaseStateMachineLogs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
                level=sfn.LogLevel.ERROR,
            ),
        )

        cdk.CfnOutput(self, "CaseStateMachineArn", value=self.state_machine.state_machine_arn)

    def workflow_environment(self) -> dict[str, str]:
        """Environment variables the API needs to start cases and create timers (INTERFACES.md #9)."""
        return {
            "CASE_STATE_MACHINE_ARN": self.state_machine.state_machine_arn,
            "SLA_SCHEDULE_GROUP": self.schedule_group_name,
            "SLA_SCHEDULER_ROLE_ARN": self.scheduler_role.role_arn,
            "SLA_TARGET_FUNCTION_ARN": self.case_steps.function_arn,
        }

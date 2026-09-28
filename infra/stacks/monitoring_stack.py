"""Alarms and the optional account budget.

Owner: andres. Alarms publish to an SNS topic; set ``-c alertEmail=you@example.com`` to
receive them. The monthly budget is account-wide, so only one stage should create it
(``-c createBudget=true``, normally prod).
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct

from .api_stack import ApiStack
from .config import StageConfig
from .pipeline_stack import PipelineStack
from .workflow_stack import WorkflowStack


class MonitoringStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: StageConfig,
        api: ApiStack,
        workflow: WorkflowStack,
        pipeline: PipelineStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        topic = sns.Topic(self, "Alerts", topic_name=cfg.name("alerts"))
        if cfg.alert_email:
            topic.add_subscription(subscriptions.EmailSubscription(cfg.alert_email))
        action = cw_actions.SnsAction(topic)

        five_minutes = cdk.Duration.minutes(5)
        alarms = {
            "ApiErrors": api.function.metric_errors(period=five_minutes),
            "Api5xx": api.http_api.metric_server_error(period=five_minutes),
            "InvestigatorErrors": workflow.investigator.metric_errors(period=five_minutes),
            "CaseExecutionsFailed": workflow.state_machine.metric_failed(period=five_minutes),
            "PipelineExecutionsFailed": pipeline.state_machine.metric_failed(period=five_minutes),
        }
        for alarm_id, metric in alarms.items():
            alarm = cloudwatch.Alarm(
                self,
                alarm_id,
                alarm_name=cfg.name(alarm_id),
                metric=metric,
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(action)

        if cfg.create_budget:
            budgets.CfnBudget(
                self,
                "MonthlyBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=cfg.name("monthly"),
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(
                        amount=max(cfg.budget_limits_usd), unit="USD"
                    ),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type="ACTUAL",
                            comparison_operator="GREATER_THAN",
                            threshold=limit,
                            threshold_type="ABSOLUTE_VALUE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                subscription_type="EMAIL", address=cfg.alert_email
                            )
                        ],
                    )
                    for limit in cfg.budget_limits_usd
                ],
            )

        cdk.CfnOutput(self, "AlertsTopicArn", value=topic.topic_arn)

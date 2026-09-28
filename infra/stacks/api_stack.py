"""HTTP API for the chat, case status and analyst console (INTERFACES.md #1).

Owners: arturo (routes and handler code in src/handlers/api.py), andres (this stack).
Customer sessions are the app's own short-lived tokens stored in the sessions table.
Analyst routes are protected by a Cognito user pool JWT authorizer.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from constructs import Construct

from .common import allow_bedrock, model_environment, python_function
from .config import StageConfig
from .data_stack import DataStack
from .workflow_stack import WorkflowStack

# Public routes: the handler checks the app session token itself.
PUBLIC_ROUTES = [
    ("/health", apigwv2.HttpMethod.GET),
    ("/session", apigwv2.HttpMethod.POST),
    ("/chat", apigwv2.HttpMethod.POST),
    ("/cases/{case_id}", apigwv2.HttpMethod.GET),
]
# Analyst routes: Cognito JWT required before the Lambda runs.
ANALYST_ROUTES = [
    ("/analyst/cases", apigwv2.HttpMethod.GET),
    ("/analyst/cases/{case_id}", apigwv2.HttpMethod.GET),
    ("/analyst/cases/{case_id}/decision", apigwv2.HttpMethod.POST),
]


class ApiStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: StageConfig,
        data: DataStack,
        workflow: WorkflowStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.function = python_function(
            self,
            "ApiFn",
            cfg,
            slug="api",
            handler="handlers.api.handler",
            description="Chat API, case status and analyst console routes",
            memory_mb=1769,
            timeout=cdk.Duration.seconds(29),  # API Gateway stops waiting at 30 s
            environment={
                **data.table_environment(),
                **workflow.workflow_environment(),
                **model_environment(cfg),
                "SESSION_TTL_MINUTES": "15",
                "LLM_TIMEOUT_SECONDS": "8",
            },
        )
        data.sessions.grant_read_write_data(self.function)
        data.cases.grant_read_write_data(self.function)
        data.demo.grant_read_write_data(self.function)
        data.serving.grant_read_data(self.function)
        data.artifacts.grant_read(self.function)
        data.traces.grant_put(self.function)
        workflow.state_machine.grant_start_execution(self.function)
        workflow.state_machine.grant_task_response(self.function)
        allow_bedrock(self.function)
        # Create and delete one-time SLA timers in the workflow's schedule group.
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["scheduler:CreateSchedule", "scheduler:DeleteSchedule", "scheduler:GetSchedule"],
                resources=[
                    f"arn:aws:scheduler:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:schedule/"
                    f"{workflow.schedule_group_name}/*"
                ],
            )
        )
        workflow.scheduler_role.grant_pass_role(self.function)

        # --- Analyst sign-in ----------------------------------------------------------------
        self.user_pool = cognito.UserPool(
            self,
            "Analysts",
            user_pool_name=cfg.name("analysts"),
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=cfg.removal_policy,
        )
        self.user_pool_client = self.user_pool.add_client(
            "AnalystConsole",
            auth_flows=cognito.AuthFlow(user_srp=True),
            generate_secret=False,
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
        )
        analyst_authorizer = authorizers.HttpUserPoolAuthorizer(
            "AnalystAuthorizer", self.user_pool, user_pool_clients=[self.user_pool_client]
        )

        # --- HTTP API -----------------------------------------------------------------------
        integration = integrations.HttpLambdaIntegration("ApiIntegration", self.function)
        self.http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name=cfg.name("api"),
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=list(cfg.allowed_origins),
                allow_methods=[apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST],
                allow_headers=["content-type", "authorization"],
                max_age=cdk.Duration.hours(1),
            ),
        )
        for path, method in PUBLIC_ROUTES:
            self.http_api.add_routes(path=path, methods=[method], integration=integration)
        for path, method in ANALYST_ROUTES:
            self.http_api.add_routes(
                path=path, methods=[method], integration=integration, authorizer=analyst_authorizer
            )

        # Throttle the whole API: a runaway client or a judge's script can't run up the bill.
        default_stage = self.http_api.default_stage.node.default_child
        default_stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=50, throttling_rate_limit=20
        )

        # Optional warm-up ping during the judging window (-c warmup=true).
        events.Rule(
            self,
            "WarmupPing",
            enabled=cfg.warmup,
            schedule=events.Schedule.rate(cdk.Duration.minutes(5)),
            targets=[
                targets.LambdaFunction(
                    self.function, event=events.RuleTargetInput.from_object({"warmup": True})
                )
            ],
        )

        cdk.CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint)
        cdk.CfnOutput(self, "AnalystUserPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(self, "AnalystUserPoolClientId", value=self.user_pool_client.user_pool_client_id)

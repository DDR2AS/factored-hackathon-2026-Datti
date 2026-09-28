"""Team access: an IAM group that can deploy through CDK, plus optional IAM users.

Owner: andres. Deployed only by andres, once per account (``-c withAccess=true``).
Users are created without credentials; andres issues console passwords or access
keys by hand and hands them over privately. Nobody commits credentials.

Members of the group can assume the roles created by ``cdk bootstrap``, which is how
``cdk deploy`` works. By default the bootstrap execution role has broad permissions;
bootstrap with ``--cloudformation-execution-policies`` to narrow what CloudFormation
may create on the team's behalf.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

from .config import PROJECT, StageConfig


class AccessStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: StageConfig, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        group = iam.Group(self, "Deployers", group_name=f"{PROJECT}-deployers")

        # cdk deploy / diff: assume the bootstrap roles (deploy, file publishing, lookup).
        group.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{cdk.Aws.ACCOUNT_ID}:role/cdk-*"],
            )
        )
        # Read-only visibility into what the team deployed, for debugging.
        group.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudformation:Describe*",
                    "cloudformation:List*",
                    "cloudformation:GetTemplate",
                    "logs:Describe*",
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "states:Describe*",
                    "states:List*",
                    "states:GetExecutionHistory",
                    "xray:Get*",
                    "xray:BatchGet*",
                    "cloudwatch:Describe*",
                    "cloudwatch:Get*",
                    "cloudwatch:List*",
                ],
                resources=["*"],
            )
        )
        # Let each person manage their own password and access keys.
        group.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "iam:ChangePassword",
                    "iam:GetUser",
                    "iam:CreateAccessKey",
                    "iam:DeleteAccessKey",
                    "iam:ListAccessKeys",
                    "iam:UpdateAccessKey",
                ],
                resources=[f"arn:aws:iam::{cdk.Aws.ACCOUNT_ID}:user/${{aws:username}}"],
            )
        )

        for person in cfg.deployers:
            iam.User(self, f"User-{person}", user_name=f"{PROJECT}-{person}", groups=[group])

        cdk.CfnOutput(self, "DeployersGroupName", value=group.group_name)

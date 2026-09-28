"""Web app hosting: private S3 bucket behind CloudFront, with /api/* forwarded to the HTTP API.

Owners: arturo + diego (front end), andres (this stack). The site is uploaded from
``frontend/dist`` when that folder exists, otherwise from ``infra/web_placeholder``.
The browser calls ``/api/<route>``; a CloudFront Function strips the ``/api`` prefix so
the API keeps the routes defined in INTERFACES.md #1.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

from .api_stack import ApiStack
from .common import REPO_ROOT
from .config import StageConfig

FRONTEND_BUILD = REPO_ROOT / "frontend" / "dist"
PLACEHOLDER = Path(__file__).resolve().parents[1] / "web_placeholder"

_STRIP_API_PREFIX = """
function handler(event) {
  var request = event.request;
  request.uri = request.uri.replace(/^\\/api/, '');
  if (request.uri === '') { request.uri = '/'; }
  return request;
}
"""


class WebStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, cfg: StageConfig, api: ApiStack, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        strip_api_prefix = cloudfront.Function(
            self,
            "StripApiPrefix",
            code=cloudfront.FunctionCode.from_inline(_STRIP_API_PREFIX),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="Removes /api before forwarding to the HTTP API",
        )
        # api_endpoint looks like https://abc123.execute-api.us-east-2.amazonaws.com
        api_domain = cdk.Fn.select(2, cdk.Fn.split("/", api.http_api.api_endpoint))

        self.distribution = cloudfront.Distribution(
            self,
            "Site",
            comment=f"Expediente Vivo web ({cfg.stage})",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(api_domain),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    function_associations=[
                        cloudfront.FunctionAssociation(
                            function=strip_api_prefix,
                            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        )
                    ],
                )
            },
        )

        site_source = FRONTEND_BUILD if FRONTEND_BUILD.is_dir() else PLACEHOLDER
        s3deploy.BucketDeployment(
            self,
            "DeploySite",
            sources=[s3deploy.Source.asset(str(site_source))],
            destination_bucket=site_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )

        cdk.CfnOutput(self, "SiteUrl", value=f"https://{self.distribution.distribution_domain_name}")

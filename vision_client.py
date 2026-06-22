from __future__ import annotations
"""Thin wrapper around the Claude-on-Vertex client used for damage assessment."""

import os

from anthropic import AnthropicVertex

GCP_PROJECT = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "up-ems-hdr-dsc")
GCP_REGION = os.environ.get("CLOUD_ML_REGION", "us-east5")
MODEL_ID = "claude-sonnet-4-6"


def get_client() -> AnthropicVertex:
    return AnthropicVertex(project_id=GCP_PROJECT, region=GCP_REGION)

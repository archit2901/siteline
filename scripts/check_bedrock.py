"""Check the Bedrock setup: credentials, the strongest Nova model this account can call, and Nova web grounding.

Run: PYTHONPATH=. python scripts/check_bedrock.py
Uses the default AWS credential chain and AWS_REGION (default us-east-1). Costs a few cents at most.
"""

import os
import sys
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

REGION = os.environ.get("AWS_REGION") or "us-east-1"
# Strongest first. Cross-region inference profile ids (us.*) are what on-demand calls need.
CANDIDATES = [
    "us.amazon.nova-2-pro-v1:0",
    "us.amazon.nova-premier-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-2-lite-v1:0",
]
FAST = os.environ.get("MODEL_FAST") or "us.amazon.nova-2-lite-v1:0"


def ping(runtime: Any, model: str) -> str | None:
    """None if the model answers, else the error."""
    try:
        runtime.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": "Reply with the word ok."}]}],
            inferenceConfig={"maxTokens": 10, "temperature": 0},
        )
        return None
    except ClientError as exc:
        return f"{exc.response['Error']['Code']}: {exc.response['Error']['Message'][:150]}"


def check_grounding(model: str) -> None:
    from langchain_aws import ChatBedrockConverse

    llm = ChatBedrockConverse(model=model, region_name=REGION, temperature=0, max_tokens=800)
    reply = llm.bind_tools(["nova_grounding"]).invoke(
        "Find the official page where Houston METRO publishes its GTFS feed. Answer with the URL only."
    )
    blocks = reply.content if isinstance(reply.content, list) else [reply.content]
    urls: list[str] = []
    text: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            text.append(block)
            continue
        if block.get("type") == "text":
            text.append(block.get("text", ""))
        for citation in (block.get("citations") or []) + (block.get("citationsContent", {}).get("citations") or []):
            url = (citation.get("location") or {}).get("web", {}).get("url") or citation.get("url")
            if url:
                urls.append(url)
    print(f"  answer: {' '.join(text).strip()[:300]}")
    print(f"  citations: {urls or 'none found in content blocks'}")
    print(f"  raw block types: {[b.get('type') if isinstance(b, dict) else 'str' for b in blocks]}")


def main() -> int:
    session = boto3.Session(region_name=REGION)
    try:
        account = session.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS credentials do not work: {exc}")
        return 1
    print(f"account {account}, region {REGION}")

    bedrock = session.client("bedrock")
    listed = {m["modelId"] for m in bedrock.list_foundation_models(byProvider="Amazon")["modelSummaries"] if "nova" in m["modelId"]}
    print(f"Nova models in the catalog: {sorted(listed)}")

    runtime = session.client("bedrock-runtime")
    smart = None
    for model in CANDIDATES:
        error = ping(runtime, model)
        print(f"  {model}: {'OK' if error is None else error}")
        if error is None and smart is None:
            smart = model
    print(f"MODEL_SMART -> {smart}")
    fast_error = ping(runtime, FAST)
    print(f"MODEL_FAST {FAST}: {'OK' if fast_error is None else fast_error}")

    print("Nova web grounding through ChatBedrockConverse:")
    try:
        check_grounding(FAST)
    except Exception as exc:  # report, don't crash: this decides grounding vs Tavily
        print(f"  failed: {type(exc).__name__}: {str(exc)[:300]}")
    return 0 if smart else 1


if __name__ == "__main__":
    sys.exit(main())

"""Runnable fixture CLI. Uses real loopback HTTPS, not a provider account."""
import argparse
import asyncio
import json
import os
import time
from uuid import uuid4
from agentic_gateway.providers.inference.adapter import ENDPOINT,Target,TextInput,invoke
from inference_upstream import upstream,LoopbackTransport


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--scenario",choices=["ok","timeout","slow_drip","no_usage","bad_body","401","429","500","redirect"],default="timeout")
    parser.add_argument("--deadline-seconds",type=float,default=1)
    args=parser.parse_args()
    os.environ["NVIDIA_API_KEY"]="fixture-secret-token"
    target=Target("fixture-probe",1,"nvidia",ENDPOINT,"meta/llama-3.3-70b-instruct","NVIDIA_API_KEY",mode="fixture")
    with upstream(args.scenario) as server:
        # Prepare the test CA/loopback routing before starting invocation's clock.
        transport=LoopbackTransport(server.url)
        started=time.monotonic()
        report=asyncio.run(invoke(target,TextInput("Reply READY",max_output_tokens=16),
            time.monotonic()+args.deadline_seconds,_transport=transport))
        report.update(attempt_id=str(uuid4()),deployment_id=target.deployment_id,generation=target.generation,
            mode="fixture",upstream_received_count=server.count,
            observed_elapsed_ms=round((time.monotonic()-started)*1000,2))
        print(json.dumps(report),flush=True)
        return 0 if server.count==1 and (report["result_code"]=="timeout" if args.scenario in {"timeout","slow_drip"} else report["client_send_count"]==1) else 1


if __name__=="__main__":
    raise SystemExit(main())

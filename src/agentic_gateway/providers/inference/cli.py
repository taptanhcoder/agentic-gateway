"""Local operator CLI. Probing live is explicit and always at most one invocation."""
import argparse
import asyncio
import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from agentic_gateway.config import Settings
from agentic_gateway.database import make_engine
from .adapter import TextInput
from .deployments import DeploymentError, attempts, configure, invoke_deployment, resolve, set_enabled


def main(argv=None, *, _transport=None, _fixture=False):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    create=commands.add_parser("configure")
    create.add_argument("deployment_id")
    create.add_argument("--offering-id",required=True)
    create.add_argument("--account-id",required=True)
    show=commands.add_parser("show");show.add_argument("deployment_id")
    enabled=commands.add_parser("set-enabled");enabled.add_argument("deployment_id");enabled.add_argument("value",choices=["true","false"])
    probe=commands.add_parser("probe");probe.add_argument("deployment_id")
    probe.add_argument("--live",action="store_true")
    probe.add_argument("--ack-account-terms",action="store_true",help="Operator has checked account pricing/quota for this single probe")
    probe.add_argument("--deadline-seconds",type=float,default=10)
    report=commands.add_parser("attempt");report.add_argument("attempt_id")
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format="%(message)s")
    # Suppress HTTPX's default URL request logs; only sanitized attempt events are emitted.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    engine=make_engine(Settings.from_env())
    try:
        if args.command=="configure":
            value=configure(engine,args.deployment_id,args.offering_id,args.account_id,_fixture=_fixture)
        elif args.command=="show":
            target,row=resolve(engine,args.deployment_id,_fixture=_fixture)
            value={key:row[key] for key in ("deployment_id","generation","offering_id","account_id","provider","endpoint","upstream_model","region","adapter_version","enabled")}
            value["secret_reference_present"]=bool(row["env_name"])
            import os
            value["credential_present"]=bool(os.getenv(row["env_name"]))
        elif args.command=="set-enabled":
            set_enabled(engine,args.deployment_id,args.value=="true")
            value={"deployment_id":args.deployment_id,"enabled":args.value=="true"}
        elif args.command=="attempt":
            with engine.connect() as connection:
                value=connection.scalar(select(attempts.c.report).where(attempts.c.id==args.attempt_id))
            if value is None:
                raise DeploymentError("attempt_not_found")
        else:
            if not args.live or not args.ack_account_terms:
                print(json.dumps({"result_code":"live_probe_not_authorized","provider_outcome":"not_sent","client_send_count":0}))
                return 2
            if not 0 < args.deadline_seconds <= 60:
                raise DeploymentError("invalid_deadline_seconds")
            value=asyncio.run(invoke_deployment(engine,args.deployment_id,
                TextInput("Reply with the word READY.","Answer briefly.",16),
                time.monotonic()+args.deadline_seconds,_fixture=_fixture,_transport=_transport))
        print(json.dumps(value,default=str),flush=True)
        return 0 if value.get("result_code","ok")=="ok" else 2
    except (DeploymentError,LookupError) as error:
        print(json.dumps({"result_code":str(error),"provider_outcome":"not_sent","client_send_count":0}),flush=True)
        return 2
    except SQLAlchemyError:
        # A started attempt remains unknown if its final persistence fails; never replay.
        print(json.dumps({"result_code":"storage_error","provider_outcome":"unknown"}),flush=True)
        return 2
    finally:
        engine.dispose()


if __name__=="__main__":
    raise SystemExit(main())

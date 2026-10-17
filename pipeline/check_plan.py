"""Reject a lab's `terraform show -json` plan before apply. Usage: check_plan.py plan.json"""
import json, sys

ALLOWED = {"t3.medium", "t3.large", "t4g.medium", "t4g.large", "m7g.large", "c7g.large"}
BLOCKED = {"aws_autoscaling_group", "aws_eks_node_group", "aws_spot_fleet_request",
           "aws_ec2_fleet", "aws_spot_instance_request", "aws_nat_gateway"}

def main(path):
    errs = []
    for rc in json.load(open(path)).get("resource_changes", []):
        if not {"create", "update"} & set(rc["change"]["actions"]):
            continue
        t, a, addr = rc["type"], rc["change"].get("after") or {}, rc["address"]
        if t in BLOCKED:
            errs.append(f"{addr}: {t} is not allowed in labs")
        if t in ("aws_instance", "aws_launch_template"):
            it = a.get("instance_type")
            if it not in ALLOWED and not (t == "aws_launch_template" and it is None):
                errs.append(f"{addr}: instance type {it!r} not in allowlist")
    print("\n".join(errs) or "plan OK")
    sys.exit(1 if errs else 0)

if __name__ == "__main__":
    main(sys.argv[1])

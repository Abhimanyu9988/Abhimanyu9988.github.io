"""Reject a lab's `terraform show -json` plan before apply. Usage: check_plan.py plan.json

The IAM permissions boundary is the real fence; this catches cost mistakes before AWS does.
"""
import json, sys

ALLOWED = {"t3.medium", "t3.large", "t4g.medium", "t4g.large", "m7g.large", "c7g.large"}
BLOCKED = {"aws_autoscaling_group", "aws_eks_node_group", "aws_spot_fleet_request",
           "aws_ec2_fleet", "aws_spot_instance_request", "aws_nat_gateway",
           "aws_db_instance", "aws_rds_cluster", "aws_elasticache_cluster", "aws_opensearch_domain",
           "aws_msk_cluster", "aws_eks_auto_mode", "aws_ec2_capacity_reservation"}
MAX_INSTANCES, MAX_RESOURCES, MAX_VOLUME_GB, MAX_TASKS = 3, 150, 50, 6

def problems(plan):
    errs, instances, creates = [], 0, 0
    for rc in plan.get("resource_changes", []):
        acts = set(rc["change"]["actions"])
        if not {"create", "update"} & acts:
            continue
        creates += "create" in acts
        t, a, addr = rc["type"], rc["change"].get("after") or {}, rc["address"]
        if t in BLOCKED:
            errs.append(f"{addr}: {t} is not allowed in labs")
        if t in ("aws_instance", "aws_launch_template"):
            it = a.get("instance_type")
            if it not in ALLOWED and not (t == "aws_launch_template" and it is None):
                errs.append(f"{addr}: instance type {it!r} not in allowlist")
        if t == "aws_instance":
            instances += 1
            if a.get("instance_market_options"):
                errs.append(f"{addr}: spot/market options are not allowed")
        if t == "aws_ebs_volume" and (a.get("size") or 0) > MAX_VOLUME_GB:
            errs.append(f"{addr}: volume over {MAX_VOLUME_GB} GB")
        for bd in (a.get("root_block_device") or []) + (a.get("ebs_block_device") or []):
            if (bd.get("volume_size") or 0) > MAX_VOLUME_GB:
                errs.append(f"{addr}: block device over {MAX_VOLUME_GB} GB")
        if t == "aws_ecs_service" and (a.get("desired_count") or 0) > MAX_TASKS:
            errs.append(f"{addr}: desired_count over {MAX_TASKS}")
    if instances > MAX_INSTANCES:
        errs.append(f"{instances} aws_instance resources; at most {MAX_INSTANCES} allowed")
    if creates > MAX_RESOURCES:
        errs.append(f"{creates} resources to create; at most {MAX_RESOURCES} allowed")
    return errs

def main(path):
    errs = problems(json.load(open(path)))
    print("\n".join(errs) or "plan OK")
    sys.exit(1 if errs else 0)

if __name__ == "__main__":
    main(sys.argv[1])

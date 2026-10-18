"""Reject a lab's `terraform show -json` plan before apply. Usage: check_plan.py plan.json

The IAM permissions boundary is the real fence; this catches cost mistakes before AWS does.
"""
import json, re, sys

ALLOWED = {"t3.medium", "t3.large", "t4g.medium", "t4g.large", "m7g.large", "c7g.large"}
# Only these resource types may be created or changed. Anything else needs a deliberate edit here.
ALLOWED_TYPES = re.compile(r"""(
    aws_(vpc|vpc_ipv4_cidr_block_association|subnet|internet_gateway|egress_only_internet_gateway|route|route_table
        |route_table_association|main_route_table_association|default_route_table|default_security_group|security_group
        |security_group_rule|vpc_security_group_(ingress|egress)_rule|network_acl|network_acl_rule|vpc_endpoint
        |network_interface|network_interface_attachment|eip|eip_association|key_pair
        |instance|launch_template|ebs_volume|volume_attachment
        |eks_(cluster|addon|access_entry|access_policy_association|fargate_profile|pod_identity_association
             |identity_provider_config)
        |iam_role|iam_role_policy|iam_role_policy_attachment|iam_instance_profile|iam_openid_connect_provider
        |iam_policy_document
        |ecs_(cluster|cluster_capacity_providers|service|task_definition|tag)|ecr_(repository|repository_policy
             |lifecycle_policy|pull_through_cache_rule)
        |lb|alb|lb_listener|alb_listener|lb_listener_rule|lb_target_group|alb_target_group|lb_target_group_attachment
        |cloudwatch_log_group|cloudwatch_metric_alarm|cloudwatch_event_rule|cloudwatch_event_target
        |sqs_queue|sqs_queue_policy|appautoscaling_target|appautoscaling_policy)
  | kubernetes_[a-z0-9_]+ | helm_release | kubectl_manifest | tls_[a-z_]+ | random_[a-z_]+ | null_resource
  | terraform_data | time_sleep | local_file | local_sensitive_file | cloudinit_config
)""", re.X)
BLOCKED_VOLUME_TYPES = {"io1", "io2"}
MAX_INSTANCES, MAX_RESOURCES, MAX_VOLUME_GB, MAX_TASKS = 3, 150, 50, 6

def problems(plan):
    errs, instances, creates = [], 0, 0
    for rc in plan.get("resource_changes", []):
        acts = set(rc["change"]["actions"])
        if not {"create", "update"} & acts:
            continue
        creates += "create" in acts
        t, a, addr = rc["type"], rc["change"].get("after") or {}, rc["address"]
        if rc.get("mode", "managed") == "managed" and not ALLOWED_TYPES.fullmatch(t):
            errs.append(f"{addr}: resource type {t} is not on the lab allowlist")
        if t in ("aws_instance", "aws_launch_template"):
            it = a.get("instance_type")
            if it not in ALLOWED and not (t == "aws_launch_template" and it is None):
                errs.append(f"{addr}: instance type {it!r} not in allowlist")
        if t == "aws_instance":
            instances += 1
            if a.get("instance_market_options"):
                errs.append(f"{addr}: spot/market options are not allowed")
        if t == "aws_ebs_volume" and ((a.get("size") or 0) > MAX_VOLUME_GB or a.get("type") in BLOCKED_VOLUME_TYPES):
            errs.append(f"{addr}: volume over {MAX_VOLUME_GB} GB or of type io1/io2")
        if a.get("tenancy") not in (None, "default"):
            errs.append(f"{addr}: only default tenancy is allowed")
        for bd in (a.get("root_block_device") or []) + (a.get("ebs_block_device") or []):
            if (bd.get("volume_size") or 0) > MAX_VOLUME_GB or bd.get("volume_type") in BLOCKED_VOLUME_TYPES:
                errs.append(f"{addr}: block device over {MAX_VOLUME_GB} GB or of type io1/io2")
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

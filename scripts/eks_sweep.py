#!/usr/bin/env python3
"""Failsafe sweeper (PLAN.md §5 Phase 3, task 4).

The eks-failsafe workflow runs ``terraform destroy`` first; this is the backstop for the
cases that leaves behind — a failed destroy, lost/locked state, a cancelled demo run. It
finds and deletes any surviving **billable** ephemeral resources (EKS clusters, NAT
gateways, EIPs, load balancers, EC2 instances) and then tears down the tagged VPC.

SAFETY INVARIANT: it only ever touches resources tagged ``tier=ephemeral``. Everything in
``infra/persistent`` is tagged ``tier=persistent`` and is structurally unreachable here —
the sweeper never selects a resource that doesn't carry the ephemeral tag (VPC children are
scoped by being inside a tagged VPC). It exits non-zero if any deletion errored, so a real
leak turns the workflow red.
"""

from __future__ import annotations

import argparse
import os

import boto3

TAG_KEY = "tier"
TAG_VALUE = "ephemeral"
_TAG_FILTER = [{"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]}]


class Report:
    """Collects human-readable actions and errors for the job summary."""

    def __init__(self) -> None:
        self.actions: list[str] = []
        self.errors: list[str] = []

    def did(self, msg: str) -> None:
        print(f"  - {msg}")
        self.actions.append(msg)

    def failed(self, msg: str, err: Exception) -> None:
        line = f"{msg}: {type(err).__name__}: {err}"
        print(f"  ! {line}")
        self.errors.append(line)


def _is_ephemeral(tags: list[dict]) -> bool:
    return any(t.get("Key") == TAG_KEY and t.get("Value") == TAG_VALUE for t in tags or [])


def sweep_eks(region: str, rpt: Report) -> None:
    eks = boto3.client("eks", region_name=region)
    for name in eks.list_clusters().get("clusters", []):
        arn = eks.describe_cluster(name=name)["cluster"]["arn"]
        if not _is_ephemeral(
            [
                {"Key": k, "Value": v}
                for k, v in eks.list_tags_for_resource(resourceArn=arn)["tags"].items()
            ]
        ):
            continue
        try:
            for ng in eks.list_nodegroups(clusterName=name).get("nodegroups", []):
                eks.delete_nodegroup(clusterName=name, nodegroupName=ng)
                rpt.did(f"deleting EKS nodegroup {name}/{ng}")
                eks.get_waiter("nodegroup_deleted").wait(clusterName=name, nodegroupName=ng)
            eks.delete_cluster(name=name)
            rpt.did(f"deleting EKS cluster {name}")
            eks.get_waiter("cluster_deleted").wait(name=name)
        except Exception as err:  # noqa: BLE001 — report and continue sweeping
            rpt.failed(f"EKS cluster {name}", err)


def sweep_load_balancers(region: str, rpt: Report) -> None:
    elb = boto3.client("elbv2", region_name=region)
    for lb in elb.describe_load_balancers().get("LoadBalancers", []):
        arn = lb["LoadBalancerArn"]
        tags = elb.describe_tags(ResourceArns=[arn])["TagDescriptions"][0]["Tags"]
        if not _is_ephemeral(tags):
            continue
        try:
            elb.delete_load_balancer(LoadBalancerArn=arn)
            rpt.did(f"deleting load balancer {lb['LoadBalancerName']}")
        except Exception as err:  # noqa: BLE001
            rpt.failed(f"load balancer {lb['LoadBalancerName']}", err)


def sweep_vpcs(region: str, rpt: Report) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=_TAG_FILTER).get("Vpcs", [])
    for vpc in vpcs:
        vid = vpc["VpcId"]
        in_vpc = [{"Name": "vpc-id", "Values": [vid]}]
        try:
            # Instances first (they hold ENIs in the subnets).
            ids = [
                i["InstanceId"]
                for r in ec2.describe_instances(Filters=in_vpc).get("Reservations", [])
                for i in r["Instances"]
                if i["State"]["Name"] not in ("terminated", "shutting-down")
            ]
            if ids:
                ec2.terminate_instances(InstanceIds=ids)
                rpt.did(f"terminating {len(ids)} instance(s) in {vid}")
                ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)

            # NAT gateways (billable) + release their EIPs.
            for nat in ec2.describe_nat_gateways(Filter=[{"Name": "vpc-id", "Values": [vid]}]).get(
                "NatGateways", []
            ):
                if nat["State"] in ("deleted", "deleting"):
                    continue
                ec2.delete_nat_gateway(NatGatewayId=nat["NatGatewayId"])
                rpt.did(f"deleting NAT gateway {nat['NatGatewayId']}")
            for addr in ec2.describe_addresses(Filters=_TAG_FILTER).get("Addresses", []):
                if "AllocationId" in addr and "AssociationId" not in addr:
                    ec2.release_address(AllocationId=addr["AllocationId"])
                    rpt.did(f"releasing EIP {addr.get('PublicIp')}")

            # Subnets, non-default SGs, route tables, IGW — needed before the VPC deletes.
            for sn in ec2.describe_subnets(Filters=in_vpc).get("Subnets", []):
                _try(rpt, f"subnet {sn['SubnetId']}", ec2.delete_subnet, SubnetId=sn["SubnetId"])
            for sg in ec2.describe_security_groups(Filters=in_vpc).get("SecurityGroups", []):
                if sg["GroupName"] != "default":
                    _try(
                        rpt,
                        f"security group {sg['GroupId']}",
                        ec2.delete_security_group,
                        GroupId=sg["GroupId"],
                    )
            for rt in ec2.describe_route_tables(Filters=in_vpc).get("RouteTables", []):
                if not any(a.get("Main") for a in rt.get("Associations", [])):
                    _try(
                        rpt,
                        f"route table {rt['RouteTableId']}",
                        ec2.delete_route_table,
                        RouteTableId=rt["RouteTableId"],
                    )
            for igw in ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [vid]}]
            ).get("InternetGateways", []):
                _try(
                    rpt,
                    f"detach IGW {igw['InternetGatewayId']}",
                    ec2.detach_internet_gateway,
                    InternetGatewayId=igw["InternetGatewayId"],
                    VpcId=vid,
                )
                _try(
                    rpt,
                    f"IGW {igw['InternetGatewayId']}",
                    ec2.delete_internet_gateway,
                    InternetGatewayId=igw["InternetGatewayId"],
                )

            ec2.delete_vpc(VpcId=vid)
            rpt.did(f"deleting VPC {vid}")
        except Exception as err:  # noqa: BLE001
            rpt.failed(f"VPC {vid}", err)


def _try(rpt: Report, what: str, fn, **kwargs) -> None:
    try:
        fn(**kwargs)
        rpt.did(f"deleting {what}")
    except Exception as err:  # noqa: BLE001
        rpt.failed(what, err)


def write_summary(rpt: Report) -> None:
    lines = ["## Failsafe sweep\n"]
    if not rpt.actions and not rpt.errors:
        lines.append("Nothing to sweep — no `tier=ephemeral` resources found. ✅")
    else:
        if rpt.actions:
            lines.append(f"**Swept {len(rpt.actions)} resource(s):**\n")
            lines += [f"- {a}" for a in rpt.actions]
        if rpt.errors:
            lines.append(f"\n**{len(rpt.errors)} error(s) — needs attention:**\n")
            lines += [f"- {e}" for e in rpt.errors]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete leaked tier=ephemeral resources.")
    parser.add_argument("--region", required=True)
    args = parser.parse_args(argv)

    rpt = Report()
    print(f"Sweeping tier={TAG_VALUE} resources in {args.region}…")
    sweep_eks(args.region, rpt)
    sweep_load_balancers(args.region, rpt)
    sweep_vpcs(args.region, rpt)
    write_summary(rpt)

    if rpt.errors:
        print(f"FAILSAFE: {len(rpt.errors)} error(s); leak may remain.")
        return 1
    print("FAILSAFE: clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

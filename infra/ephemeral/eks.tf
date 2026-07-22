# EKS module v21 (the first major to support AWS provider 6). Nodes run in the private
# subnets; one managed node group of Graviton (arm64) on-demand instances — see the
# node-group block for why arm64 and why on-demand rather than spot.
#
# endpoint_public_access = true so the GitHub Actions runner (outside the VPC) can reach
# the API server to run kubectl/helm. The cluster is short-lived and auth still gates
# every call (access entries below), so a public endpoint is an acceptable demo trade.
#
# enable_cluster_creator_admin_permissions = true grants the identity that runs
# `terraform apply` (in CI, the gha-app role) cluster-admin via an EKS access entry —
# so the same workflow can immediately helm-install and kubectl without extra RBAC glue.
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  endpoint_public_access                   = true
  enable_cluster_creator_admin_permissions = true

  # No envelope encryption / KMS key: the ephemeral demo holds no real secrets, and a
  # per-run KMS key would linger in pending-deletion (7-30d) after every destroy. Setting
  # this to null (not {}) is what disables it — the module gates on `encryption_config != null`.
  encryption_config = null

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Manage the core networking addons as EKS-managed addons instead of leaning on the
  # cluster's self-bootstrapped defaults. vpc-cni gets before_compute = true so the CNI is
  # installed and configured BEFORE the node group is created — otherwise nodes register
  # with no working network plugin, stay NotReady, and the node group create fails with
  # `NodeCreationFailure: Unhealthy nodes` (exactly what bit the first real run). coredns
  # and kube-proxy take the cluster-version default and settle once nodes are Ready.
  # (v21 renamed this arg from `cluster_addons` to `addons`.)
  addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni = {
      before_compute = true
    }
  }

  # The node-group key becomes its name, and the module derives the node IAM role from it
  # (`<name>-eks-node-group-*`). Prefixing the key with the cluster name keeps that role
  # under `quickdraw-ephemeral*`, which is exactly what the gha-eks IAM policy is scoped to.
  eks_managed_node_groups = {
    "${var.cluster_name}-ng" = {
      # Graviton (arm64) on purpose: the serving image is built single-platform
      # linux/arm64 to match the Lambda tier, so the pods only run on arm64 hardware.
      # t4g is the free-plan-eligible Graviton family; picking an x86 type (t3.small)
      # would boot but then fail to run the arm64 pod (exec format error). ami_type must
      # be set explicitly — the module defaults to x86_64 and does not infer it.
      ami_type       = "AL2023_ARM_64_STANDARD"
      instance_types = var.node_instance_types

      # On-demand, not spot: on the post-July-2025 free plan the eligible types (incl.
      # t4g.small) are free-tier On-Demand for 6 months, so on-demand is the actually-
      # free path; spot would bill spot price against credits for no benefit. Launching
      # any of these still needs a vCPU service-quota increase (default is 1) — see MEMORY.
      capacity_type = "ON_DEMAND"

      min_size     = var.node_min_size
      max_size     = var.node_max_size
      desired_size = var.node_desired_size
    }
  }
}

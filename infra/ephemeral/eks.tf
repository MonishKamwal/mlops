# EKS module v21 (the first major to support AWS provider 6). Nodes run in the private
# subnets; one managed node group on SPOT instances — the demo tolerates interruption,
# and spot is a large cost saving on an already credit-funded account.
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

  # The node-group key becomes its name, and the module derives the node IAM role from it
  # (`<name>-eks-node-group-*`). Prefixing the key with the cluster name keeps that role
  # under `quickdraw-ephemeral*`, which is exactly what the gha-eks IAM policy is scoped to.
  eks_managed_node_groups = {
    "${var.cluster_name}-ng" = {
      instance_types = var.node_instance_types
      capacity_type  = "SPOT"
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
    }
  }
}

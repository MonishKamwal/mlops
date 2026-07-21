# A dedicated VPC (never the default) so the weekly destroy wipes the whole network
# cleanly. 2 AZs is the EKS minimum; a SINGLE NAT gateway (not one per AZ) is the main
# cost lever — a per-run demo doesn't need NAT high-availability.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)

  # /20 subnets carved from the /16: two private (nodes/pods), two public (NAT + any ELB).
  private_subnets = [for i in range(2) : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i in range(2) : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true

  # Subnet role tags so the AWS load balancer controller could place ELBs correctly
  # (the weekly run uses ClusterIP + port-forward, but this keeps the VPC EKS-correct).
  public_subnet_tags  = { "kubernetes.io/role/elb" = "1" }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = "1" }
}

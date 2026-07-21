variable "aws_region" {
  description = "Region for the ephemeral cluster. Matches the rest of the project."
  type        = string
  default     = "us-east-2"
}

variable "cluster_name" {
  description = "EKS cluster name; also the prefix for the VPC and node group."
  type        = string
  default     = "quickdraw-ephemeral"
}

variable "kubernetes_version" {
  description = "EKS control-plane version."
  type        = string
  default     = "1.33"
}

variable "vpc_cidr" {
  description = "CIDR for the ephemeral VPC. Deliberately not 172.31/16 (the default VPC)."
  type        = string
  default     = "10.0.0.0/16"
}

variable "node_instance_types" {
  description = "Instance types for the managed node group. Spot-priced (see eks.tf)."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "node_desired_size" {
  description = "Desired node count. Two is enough to demo scheduling across nodes."
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximum node count — a little headroom for the k6 load window."
  type        = number
  default     = 3
}

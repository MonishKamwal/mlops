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
  description = "Instance types for the managed node group. Graviton/arm64 (t4g) to match the arm64 serving image and stay free-plan-eligible; on-demand (see eks.tf)."
  type        = list(string)
  default     = ["t4g.small"]
}

variable "node_desired_size" {
  description = "Desired node count. Three t4g.small (2 GB each) so the API pods AND the kube-prometheus-stack (Prometheus/Grafana/exporters, task 5) both fit — two nodes (4 GB) was too tight once monitoring was added."
  type        = number
  default     = 3
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 3
}

variable "node_max_size" {
  description = "Maximum node count — a little headroom for the k6 load window."
  type        = number
  default     = 4
}

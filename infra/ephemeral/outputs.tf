# What eks-demo.yml needs to reach the cluster:
#   aws eks update-kubeconfig --name $(cluster_name) --region $(region)
# (update-kubeconfig fetches the endpoint + CA itself, so those aren't strictly needed,
# but the endpoint is handy for evidence/debugging.)
output "cluster_name" {
  description = "EKS cluster name — feed to `aws eks update-kubeconfig`."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "region" {
  description = "Region the cluster lives in."
  value       = var.aws_region
}

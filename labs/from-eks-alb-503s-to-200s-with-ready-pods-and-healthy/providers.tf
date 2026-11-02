# Use this instead of the runner's backend file: terraform init && terraform apply
variable "region" {
  type    = string
  default = "eu-central-1"
}
variable "lab_name" {
  type    = string
  default = "from-eks-alb-503s-to-200"
}
variable "permissions_boundary" {
  type    = string
  default = null
}
provider "aws" {
  region = var.region
}

# Use this instead of the runner's backend file: terraform init && terraform apply
variable "region" {
  type    = string
  default = "eu-central-1"
}
variable "lab_name" {
  type    = string
  default = "eks-app-slow-cpu-fine-sc"
}
variable "permissions_boundary" {
  type    = string
  default = null
}
provider "aws" {
  region = var.region
}

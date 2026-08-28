terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local backend by default so this repo is clonable and plannable without
  # any pre-existing AWS state infrastructure. A real deployment should use
  # a remote backend instead:
  #
  # backend "s3" {
  #   bucket         = "your-org-terraform-state"
  #   key            = "dbre-platform/prod/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
}

module "database" {
  source = "../../modules/rds_postgresql"

  name        = var.name
  environment = "prod"

  engine_version               = var.engine_version
  instance_class               = var.instance_class
  allocated_storage_gb         = var.allocated_storage_gb
  max_allocated_storage_gb     = var.max_allocated_storage_gb
  multi_az                     = true  # non-negotiable in prod - see policies/environments/prod.yaml
  backup_retention_days        = var.backup_retention_days
  deletion_protection          = true  # non-negotiable in prod
  enhanced_monitoring          = true  # non-negotiable in prod
  performance_insights_enabled = true
  connection_limit             = var.connection_limit
  cluster_parameters           = var.cluster_parameters

  vpc_id                     = var.vpc_id
  subnet_ids                 = var.subnet_ids
  allowed_security_group_ids = var.allowed_security_group_ids
  allowed_cidr_blocks        = [] # never allow raw CIDR ingress in prod

  tags = var.tags
}

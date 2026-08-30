terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Local backend for dev -- see environments/prod/main.tf for the remote
  # backend pattern a real deployment should use instead.
}

provider "aws" {
  region = var.aws_region
}

module "database" {
  source = "../../modules/rds_postgresql"

  name        = var.name
  environment = "dev"

  engine_version        = var.engine_version
  instance_class        = var.instance_class
  allocated_storage_gb  = var.allocated_storage_gb
  multi_az              = var.multi_az
  backup_retention_days = var.backup_retention_days
  # dev is deliberately disposable; var.deletion_protection lets an operator tear it down without a manual RDS console override. Prod hardcodes true (see environments/prod/main.tf) and that value is non-negotiable.
  # (tfsec's aws-rds-enable-deletion-protection check is suppressed at its actual source -- modules/rds_postgresql/main.tf -- since tfsec attributes the finding to the module's resource, not this call site.)
  deletion_protection = var.deletion_protection
  enhanced_monitoring = var.enhanced_monitoring
  # Not worth the extra cost for a disposable dev database; prod hardcodes this to true.
  # (tfsec's aws-rds-enable-performance-insights check is suppressed at its actual source -- modules/rds_postgresql/main.tf.)
  performance_insights_enabled = false
  connection_limit             = var.connection_limit
  cluster_parameters           = var.cluster_parameters

  vpc_id                     = var.vpc_id
  subnet_ids                 = var.subnet_ids
  allowed_security_group_ids = var.allowed_security_group_ids
  allowed_cidr_blocks        = var.allowed_cidr_blocks

  tags = var.tags
}

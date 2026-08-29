variable "name" {
  description = "Application/database identifier, e.g. 'orders-api'. Matches request metadata.name."
  type        = string
}

variable "environment" {
  description = "dev | staging | prod"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev, staging, prod."
  }
}

variable "engine_version" {
  description = "PostgreSQL major.minor version, e.g. '16.4'."
  type        = string
}

variable "instance_class" {
  type = string
}

variable "allocated_storage_gb" {
  type = number
}

variable "max_allocated_storage_gb" {
  description = "Upper bound for RDS storage autoscaling. 0 disables autoscaling."
  type        = number
  default     = 0
}

variable "multi_az" {
  type = bool
}

variable "backup_retention_days" {
  type = number
}

variable "deletion_protection" {
  type = bool
}

variable "enhanced_monitoring" {
  description = "Enables 1-minute-granularity OS metrics via the RDS enhanced monitoring role."
  type        = bool
  default     = false
}

variable "performance_insights_enabled" {
  type    = bool
  default = true
}

variable "connection_limit" {
  description = "Used to size the DB parameter group's max_connections; not an RDS-native limit."
  type        = number
  default     = 100
}

variable "cluster_parameters" {
  description = "Additional DB parameter group entries beyond the platform baseline, as produced by dbre_platform.postgres.standards.build_cluster_parameters()."
  type        = map(string)
  default     = {}
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the DB subnet group. Assumes the VPC/subnets already exist -- this module intentionally does not create networking. See docs/decisions/0006-terraform-assumes-existing-vpc.md."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups (e.g. application tiers) permitted to reach PostgreSQL on 5432."
  type        = list(string)
  default     = []
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks permitted to reach PostgreSQL, in addition to allowed_security_group_ids. Leave empty in production; RDS should never be publicly accessible."
  type        = list(string)
  default     = []
}

variable "kms_key_id" {
  description = "KMS key (ARN) used for RDS storage encryption, Performance Insights, and the Secrets Manager master credential. Empty string (the default) makes this module provision its own dedicated customer-managed key instead of relying on the AWS-managed default -- see local.effective_kms_key_id in main.tf. Set this to an existing key's ARN (e.g. an org-wide DBRE key) to reuse one instead."
  type        = string
  default     = ""
}

variable "egress_cidr_blocks" {
  description = "CIDR blocks the database's security group may reach outbound on 443/tcp. Empty by default -- RDS does not need outbound access for normal operation, so leave this empty unless a specific integration (e.g. an extension calling out to an external HTTPS endpoint) requires it."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Full resolved tag set, as produced by dbre_platform.tagging.governance.resolve_tags()."
  type        = map(string)
}

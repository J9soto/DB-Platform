# ADR 0006: Terraform assumes an existing VPC/subnets, and does not create networking

## Status

Accepted.

## Context

`terraform/modules/rds_postgresql` needs to place an RDS instance inside
some network. The module could either create its own VPC, subnets, NAT
gateways, and route tables, or accept an existing network's IDs as input
variables and only manage the RDS-specific resources (security group,
subnet group, parameter group, the instance itself, alarms).

## Decision

Accept `vpc_id` and `subnet_ids` as required input variables
(`terraform/modules/rds_postgresql/variables.tf`) and never create VPC-level
resources. `allowed_security_group_ids` and `allowed_cidr_blocks` (both
empty by default -- no ingress is possible from module defaults) further
scope who can reach the database within that existing network.

This reflects how real organizations actually run RDS: networking
(VPCs, subnets, transit gateways, NAT strategy, IP address planning) is
almost always owned by a platform/network team as shared infrastructure
provisioned and evolved independently of any single database, often in a
completely separate Terraform state and repository with its own review
process. A self-service database platform that tried to create its own
VPC per request would either produce one throwaway VPC per database
(operationally absurd and expensive) or would need to reach into and
mutate shared networking state from a per-database module (a blast-radius
and ownership problem: an error provisioning `orders-api-prod`'s database
should never be able to affect `payments-api-prod`'s network).

## Consequences

- Every environment (`terraform/environments/dev`, `.../prod`) must
  supply real `vpc_id`/`subnet_ids` values in its `terraform.tfvars`
  (see the `.tfvars.example` file in each environment directory) before
  `terraform plan` will succeed -- this module cannot stand up a fully
  working AWS environment from nothing with zero prior AWS setup, only
  the database layer within networking that already exists.
- This is also exactly why the local Docker path exists as a first-class,
  equally-supported mode: cloning this repository and running
  `dbre request provision` in local mode requires no AWS account, no
  VPC, and no network setup at all. AWS mode is the production-realistic
  path; local mode is the zero-prerequisite demo/dev path. See
  `docs/local-vs-aws.md`.
- A team adopting this platform for real needs to decide, once, how
  database subnets are laid out (dedicated DB subnets vs. shared
  application subnets, one subnet group per environment vs. per
  database) and supply those IDs -- this module intentionally does not
  make that decision for them.

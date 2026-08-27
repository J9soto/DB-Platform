output "endpoint" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "db_instance_id" {
  value = aws_db_instance.this.id
}

output "security_group_id" {
  value = aws_security_group.this.id
}

output "master_secret_arn" {
  description = "Secrets Manager ARN holding the master credentials. Never output the credentials themselves."
  value       = aws_secretsmanager_secret.master.arn
}

output "parameter_group_name" {
  value = aws_db_parameter_group.this.name
}

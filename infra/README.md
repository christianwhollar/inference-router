# ECS deployment reference

Validated with Terraform 1.9.8 and AWS provider 5.100.0. No plan was executed against an account and no infrastructure was applied.

Supply an existing VPC using private RFC1918 addresses, two public subnets for the ALB, private subnets with NAT or appropriate VPC endpoints, a same-region ACM certificate, and an ARM64 container image addressed by digest. Supply a PostgreSQL database accessible from the task security group. The service creates its quota table at startup; production database migrations should normally be a separate release step.

The existing Secrets Manager secret must contain `api_keys_json` (a JSON-encoded API key mapping) and `postgres_dsn` (a PostgreSQL connection string using TLS). API keys must have at least 24 characters. This module references secret ARNs and never reads secret values into Terraform state. The execution role can read this one secret. If a customer-managed KMS key encrypts it, add the corresponding scoped decrypt permission; the provided module assumes the AWS-managed key.

`model_config_json` must describe a reachable Ollama-compatible provider endpoint. No GPU model server, database, DNS record, VPC, certificate, or paid model subscription is created by this module. Configure DNS to the ALB separately. The model endpoint's confidentiality approval is an operator policy decision.

```bash
terraform init
terraform validate
terraform plan -var-file=deployment.tfvars
```

Review the plan and recurring resource costs before any apply. Use encrypted remote state and a deployment role appropriate to your account. Deployment secrets do not auto-refresh in running ECS tasks; secret rotation requires a new task deployment. Task egress is open in this reference module and should be narrowed to the approved database and provider routes for a real environment. ALB ingress is restricted to explicitly supplied client CIDRs.

Reference: [AWS ECS Secrets Manager injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html).

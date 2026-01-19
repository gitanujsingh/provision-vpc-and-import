# VPC Deployment Quick Reference

## Architecture

```
provision-vpc-and-import/
├── main.tf              ← Single main.tf for ALL VPCs
├── providers.tf         ← AWS provider config
├── variables.tf         ← Variable definitions
├── modules/             ← Shared modules
└── env/
    ├── dev/
    │   └── vpc1-us-east-1/
    │       ├── backend-config      ← S3 state location
    │       └── terraform.tfvars    ← VPC-specific values
    └── npe/
        ├── ntw-npe-vpc-us-west-2/
        │   ├── backend-config
        │   └── terraform.tfvars
        └── ntw-npe-vpc-import/     ← Import folder
            ├── backend-config
            └── terraform.tfvars
```

## Key Principle

**Always run Terraform from the repository root** using:
- `-backend-config=env/...path.../backend-config`
- `-var-file=env/...path.../terraform.tfvars`

## Deploy New VPC

```bash
# 1. Create environment folder
mkdir -p env/<environment>/<vpc-name>

# 2. Create backend-config
cat > env/<environment>/<vpc-name>/backend-config << 'EOF'
bucket = "tmo-aws-tf-state-bucket"
key    = "envs/<environment>/<vpc-name>.tfstate"
region = "us-east-1"
EOF

# 3. Create terraform.tfvars (copy from existing and modify)
cp env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars env/<environment>/<vpc-name>/terraform.tfvars
# Edit the tfvars file with your VPC configuration

# 4. Initialize from root
terraform init -backend-config=env/<environment>/<vpc-name>/backend-config -reconfigure

# 5. Plan from root
terraform plan -var-file=env/<environment>/<vpc-name>/terraform.tfvars

# 6. Apply from root
terraform apply -var-file=env/<environment>/<vpc-name>/terraform.tfvars
```

## Manage Existing VPC

```bash
# From repository root

# Plan
terraform init -backend-config=env/npe/ntw-npe-vpc-us-west-2/backend-config -reconfigure
terraform plan -var-file=env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars

# Apply changes
terraform apply -var-file=env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars

# Destroy
terraform destroy -var-file=env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars
```

## Import Existing AWS VPC

```bash
# From repository root

# 1. Run discovery (creates import folder automatically)
python discover_vpc_resources.py --account-id 359416636780 --region us-west-2 --profile default

# 2. Generate tfvars and import script
python json_to_tfvars.py env/<environment>/<vpc-name>-import
python generate_import_commands.py env/<environment>/<vpc-name>-import

# 3. Initialize with import backend
terraform init -backend-config=env/<environment>/<vpc-name>-import/backend-config -reconfigure

# 4. Run import (from import folder)
cd env/<environment>/<vpc-name>-import
bash import_all.sh
cd ../../..

# 5. Verify with plan (from root)
terraform plan -var-file=env/<environment>/<vpc-name>-import/terraform.tfvars
```

## Common Commands

```bash
# Show current state
terraform init -backend-config=env/npe/ntw-npe-vpc-us-west-2/backend-config
terraform state list

# Show specific resource
terraform state show module.vpc.aws_vpc.child_module

# Refresh state
terraform refresh -var-file=env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars

# Format code
terraform fmt -recursive
```

## Tips

1. **Always from root**: Never `cd` into env folders to run terraform
2. **Backend first**: Always init with `-backend-config` before other commands
3. **Var file required**: Use `-var-file` for plan/apply/destroy
4. **One main.tf**: Edit only the root main.tf for code changes
5. **Environment isolation**: Each VPC has separate state file in S3

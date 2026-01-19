# VPC Environment Deployment Guide

## Structure

**Single source of truth**: All VPCs are deployed using the **root-level** Terraform configuration:
- `/main.tf` - Main VPC configuration (single file for all VPCs)
- `/providers.tf` - AWS provider configuration  
- `/variables.tf` - Variable definitions
- `/modules/` - Reusable Terraform modules

**Environment folders** contain ONLY environment-specific configuration:
- `backend-config` - S3 backend configuration (specifies state file location)
- `terraform.tfvars` - VPC-specific variable values (CIDR, subnets, region, etc.)

**No duplicate Terraform files** - All environments use the root main.tf.

## Resource Validation

Both `json_to_tfvars.py` and `generate_import_commands.py` now include **automatic validation**:
- ✅ Verifies all discovered resources are included
- ✅ Lists skipped resources with detailed reasons
- ✅ Reports coverage percentage
- ✅ Identifies potential issues before import

See [VALIDATION_REPORT.md](../VALIDATION_REPORT.md) for details.

## Deployment Process

All Terraform commands are run **from the root directory**, using `-backend-config` and `-var-file` to target specific VPCs.

### Deploy a VPC

```bash
# From repository root directory
cd /path/to/provision-vpc-and-import

# Initialize Terraform with VPC's backend config
terraform init -backend-config=env/<environment>/<vpc-name>/backend-config -reconfigure

# Plan the deployment with VPC's tfvars
terraform plan -var-file=env/<environment>/<vpc-name>/terraform.tfvars

# Apply the changes
terraform apply -var-file=env/<environment>/<vpc-name>/terraform.tfvars
```

### Example: Deploy NPE VPC

```bash
# From repository root
cd /path/to/provision-vpc-and-import

# Initialize with NPE VPC backend
terraform init -backend-config=env/npe/ntw-npe-vpc-us-west-2/backend-config -reconfigure

# Plan with NPE VPC variables
terraform plan -var-file=env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars

# Apply
terraform apply -var-file=env/npe/ntw-npe-vpc-us-west-2/terraform.tfvars
```

## Import Existing VPC

Use the autonomous discovery workflow to import existing VPCs:

```bash
# From repository root
cd /path/to/provision-vpc-and-import

# 1. Discover VPC resources (creates env/<environment>/<vpc-name>-import/)
python discover_vpc_resources.py --account-id <ACCOUNT_ID> --region <REGION> --profile <PROFILE>

# 2. Generate tfvars from discovery
python json_to_tfvars.py env/<environment>/<vpc-name>-import

# 3. Generate import commands
python generate_import_commands.py env/<environment>/<vpc-name>-import

# 4. Initialize Terraform from root with import folder's backend
terraform init -backend-config=env/<environment>/<vpc-name>-import/backend-config -reconfigure

# 5. Run import script
cd env/<environment>/<vpc-name>-import
bash import_all.sh

# 6. Verify import
cd /path/to/provision-vpc-and-import
terraform plan -var-file=env/<environment>/<vpc-name>-import/terraform.tfvars
```

### Example: Import NPE VPC

```bash
# Discover
python discover_vpc_resources.py --account-id 359416636780 --region us-west-2 --profile default

# Generate (creates env/npe/ntw-npe-vpc-import/)
python json_to_tfvars.py env/npe/ntw-npe-vpc-import
python generate_import_commands.py env/npe/ntw-npe-vpc-import

# Initialize from root
terraform init -backend-config=env/npe/ntw-npe-vpc-import/backend-config -reconfigure

# Import
cd env/npe/ntw-npe-vpc-import
bash import_all.sh

# Verify
cd ../../..
terraform plan -var-file=env/npe/ntw-npe-vpc-import/terraform.tfvars
```

## Environment Naming Convention

- `dev/` - Development environment VPCs
- `npe/` - Non-production environment VPCs  
- Format: `<vpc-name>-<region>/` (e.g., `ntw-npe-vpc-us-west-2/`)
- Import folders: `<vpc-name>-import/` (e.g., `ntw-npe-vpc-import/`)

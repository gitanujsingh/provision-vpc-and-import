# Resource Validation Reports

## Overview

Both `json_to_tfvars.py` and `generate_import_commands.py` now include comprehensive validation logic that verifies all resources from the discovery JSON are properly included, and reports any missing resources with detailed reasons.

## Validation Features

### 1. json_to_tfvars.py Validation

**What it validates:**
- Compares discovered resources against generated terraform.tfvars
- Tracks which resources are included vs skipped
- Provides reasons for why resources are skipped

**Output example:**
```
============================================================
TFVARS GENERATION VALIDATION REPORT
============================================================
Total resources discovered: 87
Resources included in tfvars: 79
Resources skipped: 8

Skipped Resources (with reasons):
  • route: 0.0.0.0/0
    Reason: Default route (managed by modules)
  • security_group: sg-0abc123def456
    Reason: VPC endpoint SG (managed by vpc_endpoints_sg module)
  • nacl: nacl-0xyz789
    Reason: Default NACL (AWS managed, cannot be imported)

Coverage: 90.8% of discovered resources will be managed in Terraform
============================================================
```

**Resources tracked:**
- VPC and CIDR blocks
- Subnets (validates tier: public/private/nonroutable)
- Route tables and routes (skips default 0.0.0.0/0 and local routes)
- Security Groups (excludes default SG and VPC endpoint SGs)
- NAT Gateways
- Internet Gateway
- VPC Endpoints
- DHCP Options
- NACLs (excludes default NACLs)

### 2. generate_import_commands.py Validation

**What it validates:**
- Compares discovered resources against generated import script
- Verifies resources exist in both discovery JSON and tfvars
- Identifies resources that won't be imported and why

**Output example:**
```
============================================================
IMPORT SCRIPT VALIDATION REPORT
============================================================
Total importable resources in discovery JSON: 82
Resources that will get import commands: 74
Resources skipped: 8

Skipped Resources (will NOT be imported):
  • route_table_association: rtbassoc-0abc123
    Reason: Main route table association (AWS managed)
  • security_group: sg-0def456
    Reason: Not found in tfvars security_groups map
  • vpc_endpoint: vpce-0ghi789
    Reason: Gateway endpoint but enable_s3_gateway_endpoint=false in tfvars
  • nacl: nacl-0jkl012
    Reason: Default NACL (AWS managed, will cause import errors)

Import Coverage: 90.2% of discovered resources will be imported
============================================================
```

**Resources tracked:**
- All importable resources from discovery JSON
- Cross-references with tfvars to ensure consistency
- Checks module enablement flags (e.g., enable_s3_gateway_endpoint)
- Validates route table associations (excludes main associations)
- Verifies security groups exist in tfvars

## Common Skip Reasons

### Legitimate Skips (Expected)

1. **Default routes (0.0.0.0/0)**
   - Reason: Managed by route table modules, not as separate resources
   
2. **Local routes**
   - Reason: AWS automatically creates these, cannot be imported

3. **Default VPC Security Group**
   - Reason: AWS managed, created automatically with VPC

4. **VPC Endpoint Security Groups**
   - Reason: Managed by vpc_endpoints_sg module, not extra_security_groups

5. **Default NACLs**
   - Reason: AWS managed, cannot be imported via Terraform

6. **Main Route Table Associations**
   - Reason: AWS managed, automatically created with VPC

### Potential Issues (Need Action)

1. **Security Group not found in tfvars**
   - Action: Verify the SG was properly discovered and added to tfvars
   
2. **Route not found in extra_routes**
   - Action: Check if the route should be in public/private/nonroutable_extra_routes

3. **VPC endpoint but module disabled**
   - Action: Set enable_s3_gateway_endpoint or enable_interface_endpoints = true

4. **Subnet with unknown tier**
   - Action: Ensure subnet tags have Tier = "public"/"private"/"nonroutable"

## Usage

### Run json_to_tfvars.py
```bash
python json_to_tfvars.py env/npe/ntw-npe-vpc-import/vpc_resources_*.json
```

The validation report will print after tfvars generation.

### Run generate_import_commands.py
```bash
python generate_import_commands.py env/npe/ntw-npe-vpc-import/
```

The validation report will print after import script generation.

## What to Check

1. **Review skipped resources** - Ensure they should actually be skipped
2. **Check coverage percentage** - Should be 85-95% (some skips are normal)
3. **Investigate warnings** - Unexpected resource types or missing data
4. **Compare both reports** - Resources skipped in tfvars should also skip in import

## Next Steps After Validation

1. If coverage looks good (>85%), proceed with import:
   ```bash
   cd <workspace-root>
   terraform init -backend-config=env/xxx/backend-config -reconfigure
   bash env/xxx/import_all.sh
   ```

2. If coverage is low (<80%), investigate:
   - Check discovery JSON for missing data
   - Verify tfvars has all required variables
   - Review skip reasons for unexpected entries

3. After import, verify:
   ```bash
   terraform plan -var-file=env/xxx/terraform.tfvars
   ```
   Should show "No changes" if all went well.

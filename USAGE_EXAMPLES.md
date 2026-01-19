# Usage Examples - Interactive Mode

## Overview

Both scripts already support **automatic path detection and interactive selection**. Just run them without arguments!

## Step-by-Step Workflow

### 1. Discover VPC Resources

```bash
# Run discovery script (it will prompt for selection)
python discover_vpc_resources.py

# The script will:
# - Connect to AWS
# - List all VPCs
# - Ask you to select which VPC(s) to discover
# - Automatically create env/<environment>/<vpc-name>-import/ folder
# - Generate vpc_resources_vpc-xxxxx_timestamp.json
```

**Interactive prompts:**
```
Select VPCs to discover:
  1. vpc-006c00fc888b2750c (Non-production::Dev / ntw-npe-vpc)
  2. vpc-0abc123def456 (Production / prod-vpc)
  3. vpc-0xyz789ghi012 (QA / qa-vpc)

Enter selection (comma-separated numbers, 'all', or press Enter for all): 1
```

### 2. Generate terraform.tfvars (Interactive)

```bash
# Run WITHOUT path argument
python json_to_tfvars.py

# The script will:
# - Automatically search env/*/*-import/ for discovery JSON files
# - List all available files with VPC names
# - Ask you to select which one(s) to process
# - Generate terraform.tfvars and backend-config
# - Show validation report
```

**Interactive prompts:**
```
Available discovery JSON files:
  1. env/npe/ntw-npe-vpc-import/vpc_resources_vpc-006c00fc888b2750c_20260109123456.json (VPC: ntw-npe-vpc)
  2. env/dev/ntw-dev-vpc-import/vpc_resources_vpc-0abc123def456_20260109130000.json (VPC: ntw-dev-vpc)

Select file(s) [1-2] (comma-separated, press Enter for latest only): 1

============================================================
TFVARS GENERATION VALIDATION REPORT
============================================================
Total resources discovered: 87
Resources included in tfvars: 79
Resources skipped: 8
...
```

**Options:**
- Press **Enter** - Process latest (most recent) file only
- Enter **1** - Process first file
- Enter **1,2** - Process multiple files (comma-separated)

### 3. Generate Import Script (Interactive)

```bash
# Run WITHOUT path argument
python generate_import_commands.py

# The script will:
# - Automatically search env/*/*-import/ folders
# - List all import folders
# - Ask you to select which one to process
# - Generate import_all.sh script
# - Show validation report
```

**Interactive prompts:**
```
Select an import folder:
  1. env/npe/ntw-npe-vpc-import
  2. env/dev/ntw-dev-vpc-import

Enter number [1-2]: 1

============================================================
IMPORT SCRIPT VALIDATION REPORT
============================================================
Total importable resources in discovery JSON: 82
Resources that will get import commands: 74
...
```

### 4. Run Import

```bash
# Now execute the generated import script
cd <workspace-root>
terraform init -backend-config=env/npe/ntw-npe-vpc-import/backend-config -reconfigure
bash env/npe/ntw-npe-vpc-import/import_all.sh
```

## Complete Example Workflow

```bash
# 1. Discover VPC (interactive)
python discover_vpc_resources.py
# Select VPC by number when prompted

# 2. Generate tfvars (interactive)
python json_to_tfvars.py
# Press Enter to use latest, or select by number

# 3. Generate import script (interactive)
python generate_import_commands.py
# Select import folder by number

# 4. Review validation reports from steps 2 and 3
# Check coverage percentages and skipped resources

# 5. Run import
terraform init -backend-config=env/<env>/<vpc>-import/backend-config -reconfigure
bash env/<env>/<vpc>-import/import_all.sh

# 6. Verify
terraform plan -var-file=env/<env>/<vpc>-import/terraform.tfvars
```

## Manual Path Override (Optional)

If you prefer to specify paths manually, you can still do so:

```bash
# Specify discovery JSON directly
python json_to_tfvars.py env/npe/ntw-npe-vpc-import/vpc_resources_vpc-006c00fc888b2750c_20260109123456.json

# Specify import folder directory
python json_to_tfvars.py env/npe/ntw-npe-vpc-import/

# Specify import folder for import script
python generate_import_commands.py env/npe/ntw-npe-vpc-import/
```

## Tips

1. **Default behavior is smart**: If you don't provide a path, scripts will find and list available options

2. **Latest file selection**: Press Enter without typing a number to automatically use the most recent discovery file

3. **Multiple files**: You can process multiple discoveries at once by entering comma-separated numbers (e.g., `1,3,5`)

4. **Tab completion**: Use tab completion for manual paths if needed

5. **Validation reports**: Always review the validation reports before running import - they show exactly what will be managed

## No Manual Paths Needed!

**Just run the scripts without arguments and select interactively:**

```bash
# Step 1: Discover
python discover_vpc_resources.py

# Step 2: Generate tfvars (select by index)
python json_to_tfvars.py

# Step 3: Generate import script (select by index)
python generate_import_commands.py

# Step 4: Import
bash env/<selected-folder>/import_all.sh
```

Simple and interactive! 🎯

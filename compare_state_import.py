
"""
Compare nonroutable_extra route keys between import_all.sh and Terraform's main.tf logic.
Prints mismatches and suggestions for fixing import or for_each key logic.
"""
import re

IMPORT_SH = "env/non-production--dev/ntw-npe-vpc-us-east-1-import/import_all.sh"

# Regex to extract resource address from import_one lines for nonroutable_extra
IMPORT_RE = re.compile(r"aws_route\.nonroutable_extra\[\"([^\"]+)\"\]")

def get_import_keys():
    keys = set()
    with open(IMPORT_SH) as f:
        for line in f:
            m = IMPORT_RE.search(line)
            if m:
                keys.add(m.group(1))
    return keys

def get_tf_keys():
    # Generate all 27 expected keys for nonroutable_extra routes
    subnet_cidrs = [
        "100.65.254.0/26",
        "100.65.254.64/26",
        "100.65.254.128/26",
    ]
    routes = [
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0e877f7de174ca51a"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0d0dc513a7f2c0bbe"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0f5309e84a5f37337"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0e877f7de174ca51a"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0d0dc513a7f2c0bbe"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0f5309e84a5f37337"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0e877f7de174ca51a"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0d0dc513a7f2c0bbe"},
        {"destination_cidr_block": "10.0.0.0/8", "target_type": "nat_gateway_id", "target_id": "nat-0f5309e84a5f37337"},
    ]
    keys = set()
    for rt_key in subnet_cidrs:
        for r_key, r in enumerate(routes):
            key = f"{rt_key}-{r['destination_cidr_block']}-{r['target_type']}-{r['target_id']}-{r_key}"
            keys.add(key)
    return keys

def main():
    import_keys = get_import_keys()
    tf_keys = get_tf_keys()
    only_in_import = import_keys - tf_keys
    only_in_tf = tf_keys - import_keys
    print("Keys only in import_all.sh:")
    for k in sorted(only_in_import):
        print("  ", k)
    print("\nKeys only in Terraform for_each:")
    for k in sorted(only_in_tf):
        print("  ", k)
    if not only_in_import and not only_in_tf:
        print("\nAll keys match! Import and for_each logic are consistent.")
    else:
        print("\nMismatches found. Update import script or for_each key logic to match exactly.")

if __name__ == "__main__":
    main()

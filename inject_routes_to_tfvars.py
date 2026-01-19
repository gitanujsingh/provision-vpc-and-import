#!/usr/bin/env python3
"""
Script to inject discovered routes into each route table object in terraform.tfvars.
- Reads vpc_resources JSON for routes and tfvars for route tables.
- Adds a 'routes' key to each route table object with its respective routes.
- Outputs updated tfvars.
"""
import json
import re
import sys
from collections import defaultdict

# File paths (adjust if needed)
vpc_resources_path = "env/non-production--dev/ntw-npe-vpc-us-east-1-import/vpc_resources_vpc-0eaebaec4aefc3932_20260119_210147.json"
tfvars_path = "env/non-production--dev/ntw-npe-vpc-us-east-1-import/terraform.tfvars"
out_path = tfvars_path  # Overwrite the original tfvars file

# Load VPC resources JSON
def load_vpc_routes():
    with open(vpc_resources_path, "r") as f:
        data = json.load(f)
    routes = defaultdict(list)
    for route in data.get("routes", []):
        rt_id = route.get("route_table_id")
        if rt_id:
            routes[rt_id].append(route)
    return routes

def parse_tfvars_blocks(tfvars, block_name):
    """Extracts list of dicts for a given block name from tfvars text."""
    pattern = re.compile(rf"{block_name}\s*=\s*\[(.*?)\]\s*", re.DOTALL)
    match = pattern.search(tfvars)
    if not match:
        return [], None, None
    block_str = match.group(1)
    start, end = match.span(1)
    # Split block_str into dicts (naive, assumes no nested braces in values)
    dicts = re.findall(r"\{[^}]*\}", block_str, re.DOTALL)
    return dicts, start, end

def inject_routes(tfvars, block_name, routes_map):
    dicts, start, end = parse_tfvars_blocks(tfvars, block_name)
    if not dicts:
        return tfvars
    new_dicts = []
    for d in dicts:
        id_match = re.search(r'id\s*=\s*"?([\w-]+)"?', d)
        if not id_match:
            new_dicts.append(d)
            continue
        rt_id = id_match.group(1)
        routes = routes_map.get(rt_id, [])
        # Format routes as HCL list of maps
        if routes:
            routes_hcl = '[\n' + ',\n'.join([
                '    { ' + ', '.join(f'{k} = "{v}"' for k, v in r.items() if k != 'route_table_id') + ' }'
                for r in routes
            ]) + '\n  ]'
            # Remove any existing routes key
            d = re.sub(r'routes\s*=\s*\[.*?\],?', '', d, flags=re.DOTALL)
            # Add routes key at the end
            d = d.rstrip(' }') + f',\n    routes = {routes_hcl}\n  }}'
        new_dicts.append(d)
    # Replace the block in tfvars
    new_block = '\n'.join(new_dicts)
    tfvars_new = tfvars[:start] + new_block + tfvars[end:]
    return tfvars_new

def main():
    with open(tfvars_path, "r") as f:
        tfvars = f.read()
    routes_map = load_vpc_routes()
    for block in ["public_route_tables", "private_route_tables", "nonroutable_route_tables"]:
        tfvars = inject_routes(tfvars, block, routes_map)
    with open(out_path, "w") as f:
        f.write(tfvars)
    print(f"Routes injected. Output: {out_path}")

if __name__ == "__main__":
    main()

import json
import sys
import os
from pathlib import Path

# Find discovery JSON file
def find_discovery_json():
    """Find the most recent discovery JSON file in the current directory"""
    json_files = list(Path('.').glob('vpc-*-discovery.json'))
    if not json_files:
        print("ERROR: No discovery JSON file found (vpc-*-discovery.json)")
        print("Please run the discovery script first or specify the JSON file path")
        sys.exit(1)
    
    # Get most recent file
    latest = max(json_files, key=lambda p: p.stat().st_mtime)
    return str(latest)

# Allow override via command line argument
if len(sys.argv) > 1:
    json_file = sys.argv[1]
else:
    json_file = find_discovery_json()

print(f"Analyzing: {json_file}\n")

with open(json_file, 'r') as f:
    data = json.load(f)

print("="*60)
print("RESOURCE INVENTORY FROM DISCOVERY JSON")
print("="*60)

# VPC
vpc_count = 1 if data.get('vpc') else 0
print(f"VPC: {vpc_count}")

# Subnets
subnets = data.get('subnets', [])
public_subnets = [s for s in subnets if s.get('tier') == 'public']
private_subnets = [s for s in subnets if s.get('tier') == 'private']
nonroutable_subnets = [s for s in subnets if s.get('tier') == 'nonroutable']
print(f"Subnets: {len(subnets)}")
print(f"  - Public: {len(public_subnets)}")
print(f"  - Private: {len(private_subnets)}")
print(f"  - Nonroutable: {len(nonroutable_subnets)}")

# CIDR block associations
cidr_assocs = data.get('cidr_block_associations', [])
additional_cidrs = [c for c in cidr_assocs if not c.get('primary')]
print(f"Additional CIDR Blocks: {len(additional_cidrs)}")

# NACLs
nacls = data.get('network_acls', [])
default_nacl = [n for n in nacls if n.get('is_default')]
custom_nacls = [n for n in nacls if not n.get('is_default')]
print(f"Network ACLs: {len(custom_nacls)} (excluding {len(default_nacl)} default)")

# NACL Rules
nacl_rules = data.get('network_acl_rules', [])
# Exclude default NACL rules
default_nacl_ids = {n['id'] for n in default_nacl}
custom_nacl_rules = [r for r in nacl_rules if r.get('network_acl_id') not in default_nacl_ids]
print(f"NACL Rules: {len(custom_nacl_rules)} (custom only)")

# Route Tables
route_tables = data.get('route_tables', [])
main_rt = [rt for rt in route_tables if any(a.get('main') for a in rt.get('associations', []))]
custom_rts = [rt for rt in route_tables if not any(a.get('main') for a in rt.get('associations', []))]
print(f"Route Tables: {len(custom_rts)} (excluding {len(main_rt)} main)")

# Routes
routes = data.get('routes', [])
local_routes = [r for r in routes if r.get('GatewayId', '').startswith('local')]
default_routes = [r for r in routes if r.get('DestinationCidrBlock') == '0.0.0.0/0']
extra_routes = [r for r in routes if r.get('DestinationCidrBlock') != '0.0.0.0/0' and not r.get('GatewayId', '').startswith('local')]
print(f"Routes: {len(routes)}")
print(f"  - Local: {len(local_routes)}")
print(f"  - Default (0.0.0.0/0): {len(default_routes)}")
print(f"  - Extra routes: {len(extra_routes)}")

# NAT Gateways
nat_gws = data.get('nat_gateways', [])
public_nats = [n for n in nat_gws if n.get('connectivity_type') == 'public']
private_nats = [n for n in nat_gws if n.get('connectivity_type') == 'private']
print(f"NAT Gateways: {len(nat_gws)}")
print(f"  - Public (with EIP): {len(public_nats)}")
print(f"  - Private (no EIP): {len(private_nats)}")

# EIPs
eips = data.get('eips', [])
print(f"Elastic IPs: {len(eips)}")

# Internet Gateway
igw = data.get('internet_gateway')
igw_count = 1 if igw else 0
print(f"Internet Gateway: {igw_count}")

# VPC Endpoints
endpoints = data.get('vpc_endpoints', [])
gateway_endpoints = [e for e in endpoints if e.get('type') == 'Gateway']
interface_endpoints = [e for e in endpoints if e.get('type') == 'Interface']
print(f"VPC Endpoints: {len(endpoints)}")
print(f"  - Gateway: {len(gateway_endpoints)}")
print(f"  - Interface: {len(interface_endpoints)}")

# Security Groups
sgs = data.get('security_groups', [])
default_sg = [s for s in sgs if s.get('is_default')]
custom_sgs = [s for s in sgs if not s.get('is_default')]
print(f"Security Groups: {len(custom_sgs)} (excluding {len(default_sg)} default)")

# DHCP Options
dhcp = data.get('dhcp_options')
dhcp_count = 1 if dhcp else 0
print(f"DHCP Options: {dhcp_count}")

print("\n" + "="*60)
print("TERRAFORM IMPORTABLE RESOURCES")
print("="*60)

# Calculate what Terraform will import
total = 0

# VPC
total += vpc_count
print(f"VPC: {vpc_count}")

# Additional CIDR blocks
total += len(additional_cidrs)
print(f"CIDR Associations: {len(additional_cidrs)}")

# Subnets
total += len(subnets)
print(f"Subnets: {len(subnets)}")

# NACLs (custom only)
total += len(custom_nacls)
print(f"NACLs: {len(custom_nacls)}")

# Route Tables (custom only)
total += len(custom_rts)
print(f"Route Tables: {len(custom_rts)}")

# Route Table Associations (one per subnet)
rt_assocs = data.get('route_table_associations', [])
subnet_assocs = [a for a in rt_assocs if a.get('subnet_id')]
total += len(subnet_assocs)
print(f"Route Table Associations: {len(subnet_assocs)}")

# Default routes (one per route table with default route)
total += len(default_routes)
print(f"Default Routes (0.0.0.0/0): {len(default_routes)}")

# Extra routes
total += len(extra_routes)
print(f"Extra Routes: {len(extra_routes)}")

# NAT Gateways
total += len(nat_gws)
print(f"NAT Gateways: {len(nat_gws)}")

# EIPs (for public NATs)
total += len(eips)
print(f"EIPs: {len(eips)}")

# IGW
total += igw_count
print(f"Internet Gateway: {igw_count}")

# VPC Endpoints
total += len(endpoints)
print(f"VPC Endpoints: {len(endpoints)}")

# Security Groups (custom only)
total += len(custom_sgs)
print(f"Security Groups: {len(custom_sgs)}")

# DHCP Options
total += dhcp_count
print(f"DHCP Options: {dhcp_count}")

# DHCP Options Association
total += 1 if dhcp else 0
print(f"DHCP Options Association: {1 if dhcp else 0}")

print("="*60)
print(f"TOTAL IMPORTABLE RESOURCES: {total}")
print("="*60)
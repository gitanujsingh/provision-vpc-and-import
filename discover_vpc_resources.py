import boto3
import json
import sys
import datetime
import os
import argparse
import botocore


def _tag_value(tags, key: str):
    for tag in tags or []:
        if tag.get('Key') == key:
            v = (tag.get('Value') or '').strip()
            if v:
                return v
    for tag in tags or []:
        if (tag.get('Key') or '').lower() == key.lower():
            v = (tag.get('Value') or '').strip()
            if v:
                return v
    return None


def _infer_tier_from_subnet(subnet):
    tags = subnet.get('Tags', []) or []
    tier = _tag_value(tags, 'Tier')
    if tier:
        tier_l = tier.strip().lower()
        if tier_l in {'public', 'private', 'nonroutable'}:
            return tier_l
    name = (_tag_value(tags, 'Name') or '').lower()
    # Check for nonroutable variations (nr, nonroutable, non-routable)
    if 'nonroutable' in name or 'non-routable' in name or '-nr-' in name or name.endswith('-nr'):
        return 'nonroutable'
    # Check for public variations (pub, public)
    if 'public' in name or '-pub-' in name:
        return 'public'
    # Check for private variations (pvt, private, priv)
    if 'private' in name or '-pvt-' in name or '-priv-' in name:
        return 'private'
    return None


def _prompt_required(prompt: str) -> str:
    while True:
        try:
            v = input(prompt).strip()
        except EOFError:
            print(
                "ERROR: No interactive input available. Run via 'python discover_vpc_resources.py' or pass --account-id/--select flags.",
                file=sys.stderr,
            )
            sys.exit(1)
        if v:
            return v
        print("Value is required.")


def _prompt_optional(prompt: str, default: str) -> str:
    try:
        v = input(f"{prompt} (press Enter for '{default}'): ").strip()
    except EOFError:
        return default
    return v or default

def _parse_selection(selection: str, all_vpcs):
    selection = (selection or "").strip()
    if selection.lower() in {"", "all"}:
        return all_vpcs
    # allow comma-separated numbers (1-based) or VPC IDs
    by_id = {v["VpcId"]: v for v in all_vpcs}
    selected = []
    for part in selection.split(','):
        part = part.strip()
        if not part:
            continue
        if part.startswith('vpc-'):
            if part in by_id:
                selected.append(by_id[part])
            continue
        if part.isdigit():
            i = int(part)
            if 1 <= i <= len(all_vpcs):
                selected.append(all_vpcs[i-1])
    # de-dupe by VPC id
    out = []
    seen = set()
    for v in selected:
        if v["VpcId"] not in seen:
            seen.add(v["VpcId"])
            out.append(v)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover VPC resources and write JSON under env/<env>/<vpc-name>-import/",
    )
    parser.add_argument("--account-id", default=None, help="AWS account id (if omitted, will prompt)")
    parser.add_argument("--profile", default=None, help="AWS CLI profile name")
    parser.add_argument("--region", default=None, help="AWS region")
    parser.add_argument(
        "--select",
        default=None,
        help="Selection: 'all' or empty for all; otherwise comma-separated numbers (1-based) or VPC IDs (vpc-...).",
    )
    args = parser.parse_args()

    print("Starting VPC discovery...", flush=True)

    # Ask for account number (required per your request)
    account_id = args.account_id or _prompt_required("Enter AWS account ID: ")

    profile = args.profile if args.profile is not None else _prompt_optional("Enter AWS CLI profile name", "default")

    region_default = args.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    region = args.region if args.region is not None else _prompt_optional("Enter AWS region", region_default)

    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        sts = session.client('sts')
        actual_account_id = sts.get_caller_identity()['Account']
        if account_id and account_id != actual_account_id:
            print(
                f"Warning: The credentials for profile '{profile}' are for account {actual_account_id}, not {account_id}.",
                file=sys.stderr,
            )
    except botocore.exceptions.ProfileNotFound:
        print(f"AWS profile '{profile}' not found. Check your AWS CLI configuration.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error initializing AWS session: {e}", file=sys.stderr)
        return 1

    ec2 = session.client('ec2')
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    all_vpcs = ec2.describe_vpcs()['Vpcs']
    if not all_vpcs:
        print("No VPCs found in this account.")
        return 1

    print("Discovered VPCs:")
    for idx, vpc in enumerate(all_vpcs, 1):
        vpc_id = vpc['VpcId']
        vpc_name = next(
            (tag['Value'].strip() for tag in vpc.get('Tags', []) if tag['Key'].lower() == 'name' and tag['Value'].strip()),
            None,
        )
        print(f"[{idx}] {vpc_id} - {vpc_name if vpc_name else 'No Name'}")

    selection = args.select
    if selection is None:
        try:
            selection = input(
                "\nSelect a VPC by number (or comma-separated for multiple, or press Enter for all): "
            ).strip()
        except EOFError:
            selection = "all"

    vpcs = _parse_selection(selection, all_vpcs)
    if not vpcs:
        print("No valid VPCs selected.", file=sys.stderr)
        return 1

    import re
    for vpc in vpcs:
        vpc_id = vpc['VpcId']
        vpc_name = next(
            (tag['Value'].strip() for tag in vpc.get('Tags', []) if tag['Key'].lower() == 'name' and tag['Value'].strip()),
            None,
        )
        env_tag = _tag_value(vpc.get('Tags', []), 'environment') or 'dev'
        
        # Sanitize environment tag for folder name (Windows-safe)
        env_tag_safe = re.sub(r'[^a-zA-Z0-9_-]', '-', env_tag.lower())

        env_folder = os.path.join('env', env_tag_safe)
        os.makedirs(env_folder, exist_ok=True)

        if vpc_name:
            vpc_folder_base = re.sub(r'[^a-zA-Z0-9_-]', '-', vpc_name.lower())
        else:
            vpc_folder_base = vpc_id.lower()

        import_folder = os.path.join(env_folder, f"{vpc_folder_base}-import")
        os.makedirs(import_folder, exist_ok=True)
        output_file = os.path.join(import_folder, f"vpc_resources_{vpc_id}_{timestamp}.json")

        resources = {}
        resources['vpc'] = {
            'id': vpc_id,
            'tags': vpc.get('Tags', []),
            'cidr_block': vpc.get('CidrBlock', ''),
            'additional_cidrs': [assoc['CidrBlock'] for assoc in vpc.get('CidrBlockAssociationSet', []) if not assoc.get('Primary', True)]
        }
        
        # Add region to the discovery data
        resources['region'] = region

        # VPC CIDR Block Associations (for aws_vpc_ipv4_cidr_block_association)
        resources['cidr_block_associations'] = [
            {
                'association_id': assoc['AssociationId'],
                'cidr_block': assoc['CidrBlock'],
                'primary': assoc.get('Primary', False)
            }
            for assoc in vpc.get('CidrBlockAssociationSet', [])
        ]

        # Subnets
        subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['Subnets']
        resources['subnets'] = [
            {
                'id': s['SubnetId'],
                'tags': s.get('Tags', []),
                'cidr_block': s.get('CidrBlock', ''),
                'az': s.get('AvailabilityZone', ''),
                'tier': _infer_tier_from_subnet(s)
            }
            for s in subnets
        ]

        # DHCP Options and Association
        dhcp_assoc = vpc.get('DhcpOptionsId')
        dhcp_options = None
        if dhcp_assoc:
            try:
                dhcp_options = ec2.describe_dhcp_options(DhcpOptionsIds=[dhcp_assoc])['DhcpOptions'][0]
            except Exception:
                dhcp_options = None
        resources['dhcp_options'] = {
            'id': dhcp_assoc,
            'options': dhcp_options
        } if dhcp_assoc and dhcp_options else {}

        if dhcp_assoc:
            resources['dhcp_options_association'] = {
                'vpc_id': vpc_id,
                'dhcp_options_id': dhcp_assoc,
                'import_id': f"{vpc_id}/{dhcp_assoc}",
            }

        # Network ACLs
        nacls = ec2.describe_network_acls(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['NetworkAcls']
        nacl_list = []
        nacl_rules = []
        for nacl in nacls:
            nacl_entry = {
                'id': nacl['NetworkAclId'],
                'tags': nacl.get('Tags', []),
                'is_default': nacl.get('IsDefault', False),
                'vpc_id': nacl.get('VpcId'),
                'associations': nacl.get('Associations', [])
            }
            nacl_list.append(nacl_entry)
            for entry in nacl.get('Entries', []):
                rule = entry.copy()
                rule['network_acl_id'] = nacl['NetworkAclId']
                nacl_rules.append(rule)
        resources['network_acls'] = nacl_list
        resources['network_acl_rules'] = nacl_rules

        # Route Tables
        rts = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['RouteTables']
        resources['route_tables'] = [{'id': r['RouteTableId'], 'tags': r.get('Tags', []), 'associations': r.get('Associations', [])} for r in rts]

        # as for each and count not working 
        # i had to flatened the route table associations (for importing aws_route_table_association)
        
        rta_out = []
        for r in rts:
            for a in r.get('Associations', []) or []:
                rta_out.append({
                    'id': a.get('RouteTableAssociationId'),
                    'route_table_id': r.get('RouteTableId'),
                    'subnet_id': a.get('SubnetId'),
                    'main': a.get('Main', False),
                })
        resources['route_table_associations'] = [a for a in rta_out if a.get('id')]

        # Individual Routes (for aws_route)
        routes = []
        for r in rts:
            for route in r.get('Routes', []):
                route_entry = route.copy()
                route_entry['route_table_id'] = r['RouteTableId']
                routes.append(route_entry)
        resources['routes'] = routes

        # Default IPv4 routes (0.0.0.0/0) per route table (for importing aws_route)
        default_routes = []
        for r in rts:
            rt_id = r.get('RouteTableId')
            for route in r.get('Routes', []) or []:
                if route.get('DestinationCidrBlock') == '0.0.0.0/0':
                    default_routes.append({
                        'route_table_id': rt_id,
                        'destination_cidr_block': '0.0.0.0/0',
                        'gateway_id': route.get('GatewayId'),
                        'nat_gateway_id': route.get('NatGatewayId'),
                        'vpc_endpoint_id': route.get('VpcEndpointId'),
                        'transit_gateway_id': route.get('TransitGatewayId'),
                        'network_interface_id': route.get('NetworkInterfaceId'),
                        'instance_id': route.get('InstanceId'),
                        'state': route.get('State'),
                    })
        resources['default_routes'] = default_routes

        # Security Groups (with full ingress/egress rules)
        sgs = ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['SecurityGroups']
        resources['security_groups'] = [
            {
                'id': sg['GroupId'],
                'group_name': sg.get('GroupName'),
                'description': sg.get('Description'),
                'ingress': sg.get('IpPermissions', []),
                'egress': sg.get('IpPermissionsEgress', []),
                'tags': sg.get('Tags', [])
            }
            for sg in sgs
        ]

        # VPC Endpoints (with security group associations)
        endpoints = ec2.describe_vpc_endpoints(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['VpcEndpoints']
        resources['vpc_endpoints'] = [
            {
                'id': e['VpcEndpointId'],
                'service_name': e.get('ServiceName'),
                'type': e.get('VpcEndpointType'),
                'security_group_ids': e.get('Groups', []),
                'tags': e.get('Tags', [])
            }
            for e in endpoints
        ]

        # Internet Gateway
        igws = ec2.describe_internet_gateways(Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}])['InternetGateways']
        if igws:
            igw = igws[0]
            resources['internet_gateway'] = {
                'id': igw.get('InternetGatewayId'),
                'tags': igw.get('Tags', [])
            }

        # NAT Gateways (add to main JSON)
        nat_gws = ec2.describe_nat_gateways(Filter=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['NatGateways']
        resources['nat_gateways'] = [
            {
                'id': nat['NatGatewayId'],
                'subnet_id': nat.get('SubnetId'),
                'state': nat.get('State'),
                'tags': nat.get('Tags', []),
                'connectivity_type': nat.get('ConnectivityType'),
                'nat_gateway_addresses': nat.get('NatGatewayAddresses', [])
            }
            for nat in nat_gws
        ]

        # EIPs used by NAT gateways (for aws_eip resource)
        eips_out = []
        alloc_ids = sorted({addr.get('AllocationId') for nat in nat_gws for addr in nat.get('NatGatewayAddresses', []) if addr.get('AllocationId')})
        if alloc_ids:
            try:
                eips = ec2.describe_addresses(AllocationIds=alloc_ids)['Addresses']
                for eip in eips:
                    if eip.get('AllocationId'):
                        eips_out.append({
                            'id': eip['AllocationId'],
                            'public_ip': eip.get('PublicIp'),
                            'tags': eip.get('Tags', [])
                        })
            except Exception:
                pass
        resources['eips'] = eips_out

        # Main VPC resource JSON
        try:
            with open(output_file, 'w') as f:
                json.dump(resources, f, indent=2)
        except Exception as e:
            print(f"Error writing JSON: {e}")
            with open(output_file, 'a') as f:
                f.write('\n}')

        print(f"\nDiscovery complete for VPC {vpc_id}")
        print(f"Output: {output_file}")
        
        # Print resource summary
        _print_resource_summary(resources)

    return 0


def _print_resource_summary(resources: dict):
    """Print a summary of discovered resources"""
    print("\n" + "="*60)
    print("DISCOVERED RESOURCES SUMMARY")
    print("="*60)
    
    # VPC
    vpc_count = 1 if resources.get('vpc') else 0
    print(f"VPC: {vpc_count}")
    
    # Subnets
    subnets = resources.get('subnets', [])
    public_subnets = [s for s in subnets if s.get('tier') == 'public']
    private_subnets = [s for s in subnets if s.get('tier') == 'private']
    nonroutable_subnets = [s for s in subnets if s.get('tier') == 'nonroutable']
    print(f"Subnets: {len(subnets)} (Public: {len(public_subnets)}, Private: {len(private_subnets)}, Nonroutable: {len(nonroutable_subnets)})")
    
    # CIDR associations
    cidr_assocs = resources.get('cidr_block_associations', [])
    additional_cidrs = [c for c in cidr_assocs if not c.get('primary')]
    print(f"Additional CIDR Blocks: {len(additional_cidrs)}")
    
    # NACLs
    nacls = resources.get('network_acls', [])
    custom_nacls = [n for n in nacls if not n.get('is_default')]
    print(f"Network ACLs: {len(custom_nacls)}")
    
    # Route Tables
    route_tables = resources.get('route_tables', [])
    main_rt = [rt for rt in route_tables if any(a.get('main') for a in rt.get('associations', []))]
    custom_rts = [rt for rt in route_tables if not any(a.get('main') for a in rt.get('associations', []))]
    print(f"Route Tables: {len(custom_rts)}")
    
    # Routes
    routes = resources.get('routes', [])
    local_routes = [r for r in routes if r.get('GatewayId', '').startswith('local')]
    default_routes = [r for r in routes if r.get('DestinationCidrBlock') == '0.0.0.0/0']
    extra_routes = [r for r in routes if r.get('DestinationCidrBlock') != '0.0.0.0/0' and not r.get('GatewayId', '').startswith('local')]
    print(f"Routes: {len(routes)} (Default: {len(default_routes)}, Extra: {len(extra_routes)})")
    
    # NAT Gateways
    nat_gws = resources.get('nat_gateways', [])
    public_nats = [n for n in nat_gws if n.get('connectivity_type') == 'public']
    private_nats = [n for n in nat_gws if n.get('connectivity_type') == 'private']
    print(f"NAT Gateways: {len(nat_gws)} (Public: {len(public_nats)}, Private: {len(private_nats)})")
    
    # EIPs
    eips = resources.get('eips', [])
    print(f"Elastic IPs: {len(eips)}")
    
    # Internet Gateway
    igw = resources.get('internet_gateway')
    print(f"Internet Gateway: {1 if igw else 0}")
    
    # VPC Endpoints
    endpoints = resources.get('vpc_endpoints', [])
    gateway_eps = [e for e in endpoints if e.get('type') == 'Gateway']
    interface_eps = [e for e in endpoints if e.get('type') == 'Interface']
    print(f"VPC Endpoints: {len(endpoints)} (Gateway: {len(gateway_eps)}, Interface: {len(interface_eps)})")
    
    # Security Groups
    sgs = resources.get('security_groups', [])
    custom_sgs = [s for s in sgs if not s.get('is_default')]
    print(f"Security Groups: {len(custom_sgs)}")
    
    # DHCP Options
    dhcp = resources.get('dhcp_options')
    print(f"DHCP Options: {1 if dhcp else 0}")
    
    print("="*60)
    
    total = (vpc_count + len(additional_cidrs) + len(subnets) + len(custom_nacls) + 
             len(custom_rts) + len(subnets) + len(default_routes) + len(extra_routes) + 
             len(nat_gws) + len(eips) + (1 if igw else 0) + len(endpoints) + 
             len(custom_sgs) + (1 if dhcp else 0) + (1 if dhcp else 0))
    
    print(f"ESTIMATED TERRAFORM RESOURCES TO IMPORT: ~{total}")
    print("="*60 + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

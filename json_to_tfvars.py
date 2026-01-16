import argparse
import glob
import ipaddress
import json
import os
import re
import sys


def _tag_value(tags, key: str) -> str:
	for tag in tags or []:
		if tag.get("Key") == key:
			v = (tag.get("Value") or "").strip()
			if v:
				return v
	for tag in tags or []:
		if (tag.get("Key") or "").lower() == key.lower():
			v = (tag.get("Value") or "").strip()
			if v:
				return v
	return ""


def _infer_region(discovery: dict, fallback: str = "us-east-1") -> str:
	# First check if region is explicitly stored in discovery JSON
	stored_region = discovery.get("region")
	if stored_region:
		return stored_region
	
	# Prefer Region tag if present
	tags = (discovery.get("vpc") or {}).get("tags") or []
	region = _tag_value(tags, "Region")
	if region:
		return region
	
	# Infer from endpoint service names
	for ep in discovery.get("vpc_endpoints", []) or []:
		svc = (ep.get("service_name") or "")
		m = re.match(r"^com\\.amazonaws\\.([a-z0-9-]+)\\.", svc)
		if m:
			return m.group(1)
	return fallback


def _sorted_cidrs(cidrs):
	out = [c for c in cidrs if c]
	out = list(dict.fromkeys(out))
	try:
		out.sort(key=lambda c: (ipaddress.ip_network(c).version, int(ipaddress.ip_network(c).network_address), ipaddress.ip_network(c).prefixlen))
	except Exception:
		out.sort()
	return out


def _state_folder_name(import_folder: str, vpc_name_fallback: str) -> str:
	# Use the import folder name exactly (including -import) for backend key path.
	base = os.path.basename(os.path.normpath(import_folder))
	return (base or "").strip() or vpc_name_fallback


def _convert_sg_rule(rule: dict, rule_type: str) -> dict:
	"""Convert AWS IpPermission to Terraform security group rule format."""
	protocol = str(rule.get('IpProtocol', '-1'))
	from_port = rule.get('FromPort', 0)
	to_port = rule.get('ToPort', 0)
	
	# Handle protocol names
	if protocol == '-1':
		protocol = '-1'
		from_port = 0
		to_port = 0
	elif protocol == 'tcp':
		protocol = '6'
	elif protocol == 'udp':
		protocol = '17'
	elif protocol == 'icmp':
		protocol = '1'
	
	tf_rule = {
		'type': rule_type,
		'protocol': protocol,
		'from_port': from_port,
		'to_port': to_port,
	}
	
	# Handle CIDR blocks
	if rule.get('IpRanges'):
		for ip_range in rule['IpRanges']:
			if ip_range.get('CidrIp'):
				return {**tf_rule, 'cidr_blocks': [ip_range['CidrIp']], 'description': ip_range.get('Description', '')}
	
	# Handle IPv6 CIDR blocks
	if rule.get('Ipv6Ranges'):
		for ip_range in rule['Ipv6Ranges']:
			if ip_range.get('CidrIpv6'):
				return {**tf_rule, 'ipv6_cidr_blocks': [ip_range['CidrIpv6']], 'description': ip_range.get('Description', '')}
	
	# Handle security group references
	if rule.get('UserIdGroupPairs'):
		for pair in rule['UserIdGroupPairs']:
			if pair.get('GroupId'):
				return {**tf_rule, 'source_security_group_id': pair['GroupId'], 'description': pair.get('Description', '')}
	
	# Default to CIDR 0.0.0.0/0 if no source specified
	return {**tf_rule, 'cidr_blocks': ['0.0.0.0/0'], 'description': ''}


def _extract_security_groups(discovery: dict, vpc_endpoint_sg_ids: list = None) -> dict:
	"""Extract all security groups including default from discovery JSON for import."""
	sgs = discovery.get('security_groups', [])
	sg_map = {}
	vpc_endpoint_sg_ids = vpc_endpoint_sg_ids or []
	
	for sg in sgs:
		sg_name = sg.get('group_name', '')
		sg_id = sg.get('id', '')
		if not sg_id:
			continue
		
		# Extract tags
		tags = {}
		for tag in sg.get('tags', []):
			key = tag.get('Key', '')
			value = tag.get('Value', '')
			if key and key != 'Name':  # Name is handled separately in Terraform
				tags[key] = value
		
		name_tag = _tag_value(sg.get('tags', []), 'Name') or sg_name
		
		# Convert ingress/egress rules
		ingress_rules = []
		for rule in sg.get('ingress', []):
			converted = _convert_sg_rule(rule, 'ingress')
			if converted:
				ingress_rules.append(converted)
		
		egress_rules = []
		for rule in sg.get('egress', []):
			converted = _convert_sg_rule(rule, 'egress')
			if converted:
				egress_rules.append(converted)
		
		# Use a sanitized version of the SG name as the key
		key = re.sub(r'[^a-zA-Z0-9_-]', '_', sg_name)
		sg_map[key] = {
			'name': sg_name,
			'description': sg.get('description', ''),
			'ingress_rules': ingress_rules,
			'egress_rules': egress_rules,
			'tags': tags,
			'name_tag': name_tag,
		}
	
	return sg_map


def _extract_extra_routes(discovery: dict, route_tables: dict) -> dict:
	"""Extract non-default routes from route tables."""
	routes = discovery.get('routes', [])
	vpc_cidr = (discovery.get('vpc') or {}).get('cidr_block', '')
	
	extra_routes = {'public': [], 'private': [], 'nonroutable': []}
	
	for route in routes:
		dest = route.get('DestinationCidrBlock', '')
		rt_id = route.get('route_table_id', '')
		
		# Skip local routes
		if route.get('GatewayId', '').startswith('local'):
			continue
		
		# Skip default routes (handled separately)
		if dest == '0.0.0.0/0':
			continue
		
		# Skip if no destination
		if not dest:
			continue
		
		# Determine tier based on route table
		tier = route_tables.get(rt_id, 'private')
		
		# Determine target
		target_type = None
		target_id = None
		
		if route.get('GatewayId'):
			gw_id = route['GatewayId']
			if gw_id.startswith('igw-'):
				target_type = 'gateway_id'
				target_id = 'igw'  # Use symbolic reference
			else:
				target_type = 'gateway_id'
				target_id = gw_id
		elif route.get('NatGatewayId'):
			target_type = 'nat_gateway_id'
			target_id = route['NatGatewayId']
		elif route.get('VpcEndpointId'):
			target_type = 'vpc_endpoint_id'
			target_id = route['VpcEndpointId']
		elif route.get('TransitGatewayId'):
			target_type = 'transit_gateway_id'
			target_id = route['TransitGatewayId']
		elif route.get('NetworkInterfaceId'):
			target_type = 'network_interface_id'
			target_id = route['NetworkInterfaceId']
		elif route.get('InstanceId'):
			target_type = 'instance_id'
			target_id = route['InstanceId']
		
		if target_type and target_id:
			route_obj = {
				'destination_cidr_block': dest,
				'target_type': target_type,
				'target_id': target_id,
			}
			extra_routes[tier].append(route_obj)
	
	return extra_routes


def _build_route_table_tier_map(discovery: dict) -> dict:
	"""Build a map of route_table_id -> tier (public/private/nonroutable)."""
	rt_map = {}
	rtas = discovery.get('route_table_associations', [])
	subnets = discovery.get('subnets', [])
	
	# Build subnet_id -> tier map
	subnet_tier_map = {}
	for subnet in subnets:
		sid = subnet.get('id')
		tier = (subnet.get('tier') or 'private').lower()
		if sid:
			subnet_tier_map[sid] = tier
	
	# Map route tables to tiers based on subnet associations
	for rta in rtas:
		rt_id = rta.get('route_table_id')
		subnet_id = rta.get('subnet_id')
		if rt_id and subnet_id and subnet_id in subnet_tier_map:
			rt_map[rt_id] = subnet_tier_map[subnet_id]
	
	return rt_map


def _extract_vpc_endpoint_sgs(discovery: dict) -> list:
	"""Extract security group IDs used by VPC endpoints."""
	endpoints = discovery.get('vpc_endpoints', [])
	sg_ids = set()
	
	for ep in endpoints:
		# Only interface endpoints have security groups
		if ep.get('type') == 'Interface':
			for group in ep.get('security_group_ids', []):
				if isinstance(group, dict):
					gid = group.get('GroupId')
					if gid:
						sg_ids.add(gid)
				elif isinstance(group, str):
					sg_ids.add(group)
	
	return sorted(sg_ids)


def _extract_tfvars_values(discovery: dict, import_folder: str) -> dict:
		# DHCP Options
		dhcp_options = discovery.get("dhcp_options") or {}
		domain_name = dhcp_options.get("domain_name", "ec2.internal")
		domain_name_servers = dhcp_options.get("domain_name_servers", ["AmazonProvidedDNS"])
		ntp_servers = dhcp_options.get("ntp_servers", ["0.0.0.0"])
		netbios_name_servers = dhcp_options.get("netbios_name_servers", ["192.168.1.1"])
		netbios_node_type = dhcp_options.get("netbios_node_type", 2)
	vpc = discovery.get("vpc") or {}
	tags = vpc.get("tags") or []

	env = _tag_value(tags, "environment") or "dev"
	vpc_name_tag = _tag_value(tags, "Name") or _tag_value(tags, "Environment") or env
	region = _infer_region(discovery)

	vpc_cidr = vpc.get("cidr_block") or ""

	additional = []
	if isinstance(discovery.get("cidr_block_associations"), list):
		additional = [
			a.get("cidr_block")
			for a in discovery.get("cidr_block_associations") or []
			if a.get("cidr_block") and not a.get("primary")
		]
	else:
		additional = vpc.get("additional_cidrs") or []
	# Ensure the primary VPC CIDR is not treated as an additional CIDR.
	additional = [c for c in additional if c and c != vpc_cidr]
	additional = _sorted_cidrs(additional)

	subnets = discovery.get("subnets") or []
	public = _sorted_cidrs([s.get("cidr_block") for s in subnets if (s.get("tier") or "").lower() == "public"])
	private = _sorted_cidrs([s.get("cidr_block") for s in subnets if (s.get("tier") or "").lower() == "private"])
	nonroutable = _sorted_cidrs(
		[s.get("cidr_block") for s in subnets if (s.get("tier") or "").lower() == "nonroutable"]
	)

	azs = sorted({s.get("az") for s in subnets if s.get("az")})

	state_folder = _state_folder_name(import_folder, vpc_name_tag)

	return {
		"env": env,
		"region": region,
		"vpc_cidr": vpc_cidr,
		"additional_cidrs": additional,
		"public_subnet_cidrs": public,
		"private_subnet_cidrs": private,
		"nonroutable_subnet_cidrs": nonroutable,
		"azs": azs,
		"vpc_name": vpc_name_tag,
		"state_folder": state_folder,
		"domain_name": domain_name,
		"domain_name_servers": domain_name_servers,
		"ntp_servers": ntp_servers,
		"netbios_name_servers": netbios_name_servers,
		"netbios_node_type": netbios_node_type,
	}


def _find_by_tag_name(items, name_value: str):
	for it in items or []:
		if _tag_value(it.get("tags") or [], "Name") == name_value:
			return it
	return None


def _to_rule_obj(entry: dict) -> dict:
	# Map EC2 describe_network_acls Entry shape to our nacl_rules object type.
	rule_number = entry.get("RuleNumber")
	protocol = entry.get("Protocol")
	rule_action = entry.get("RuleAction")
	cidr_block = entry.get("CidrBlock")
	ipv6_cidr_block = entry.get("Ipv6CidrBlock")
	port_range = entry.get("PortRange") or {}
	from_port = port_range.get("From")
	to_port = port_range.get("To")
	# If no port range is present (e.g. protocol -1), keep 0/0.
	if from_port is None:
		from_port = 0
	if to_port is None:
		to_port = 0

	out = {
		"rule_number": int(rule_number) if rule_number is not None else 0,
		"protocol": str(protocol).strip() if protocol is not None else "-1",
		"rule_action": str(rule_action).strip() if rule_action is not None else "allow",
		"from_port": int(from_port),
		"to_port": int(to_port),
	}
	if cidr_block:
		out["cidr_block"] = cidr_block
	if ipv6_cidr_block:
		out["ipv6_cidr_block"] = ipv6_cidr_block
	return out


def _extract_nacl_rules(discovery: dict, vpc_name: str) -> dict:
	# Build nacl_rules from discovery so import can bring rules into state and avoid duplicate rule_number errors.
	nacls = discovery.get("network_acls") or []
	public_nacl_name = f"ntw-{vpc_name}-public-nacl"
	prn_nacl_name = f"ntw-{vpc_name}-private-nonroutable-nacl"
	public_nacl = _find_by_tag_name(nacls, public_nacl_name)
	prn_nacl = _find_by_tag_name(nacls, prn_nacl_name)
	public_id = (public_nacl or {}).get("id")
	prn_id = (prn_nacl or {}).get("id")

	rules = discovery.get("network_acl_rules") or []

	def keep_rule(e: dict) -> bool:
		rn = e.get("RuleNumber")
		try:
			rn = int(rn)
		except Exception:
			return False
		# Skip implicit default deny rules.
		if rn == 32767:
			return False
		return True

	public_ingress = []
	public_egress = []
	private_ingress = []
	private_egress = []

	for e in rules:
		if not keep_rule(e):
			continue
		nacl_id = e.get("network_acl_id")
		if not nacl_id:
			continue
		egress = bool(e.get("Egress"))
		obj = _to_rule_obj(e)
		if public_id and nacl_id == public_id:
			(public_egress if egress else public_ingress).append(obj)
		elif prn_id and nacl_id == prn_id:
			(private_egress if egress else private_ingress).append(obj)

	# Deterministic ordering (rule_number ascending)
	public_ingress.sort(key=lambda r: r.get("rule_number", 0))
	public_egress.sort(key=lambda r: r.get("rule_number", 0))
	private_ingress.sort(key=lambda r: r.get("rule_number", 0))
	private_egress.sort(key=lambda r: r.get("rule_number", 0))

	return {
		"public_ingress": public_ingress,
		"public_egress": public_egress,
		"private_ingress": private_ingress,
		"private_egress": private_egress,
	}


def _write_backend_config(out_dir: str, env: str, state_folder: str, region: str, bucket: str, prefix: str) -> str:
	# Match repo convention: envs/<env>/<vpcname>/terraform.tfstate
	# Sanitize env for S3 key (remove special chars like ::)
	env_safe = re.sub(r'[^a-zA-Z0-9_/-]', '-', env)
	key = f"{prefix}/{env_safe}/{state_folder}/terraform.tfstate"
	path = os.path.join(out_dir, "backend-config")
	with open(path, "w", newline="\n") as f:
		f.write(f'bucket = "{bucket}"\n')
		f.write(f'key    = "{key}"\n')
		f.write(f'region = "{region}"\n')
	return path


def _validate_tfvars_coverage(data: dict, vpc_endpoint_sg_ids: set) -> dict:
	"""Validate that all discovered resources will be included in tfvars."""
	validation = {
		'total_resources': 0,
		'included_resources': 0,
		'skipped_resources': [],
		'warnings': []
	}
	
	# VPC
	if data.get('vpc'):
		validation['total_resources'] += 1
		validation['included_resources'] += 1
	
	# CIDR associations
	cidrs = data.get('cidr_block_associations', [])
	validation['total_resources'] += len(cidrs)
	validation['included_resources'] += len(cidrs)
	
	# Subnets
	subnets = data.get('subnets', [])
	validation['total_resources'] += len(subnets)
	for s in subnets:
		tier = s.get('tier')
		if tier in ['public', 'private', 'nonroutable']:
			validation['included_resources'] += 1
		else:
			validation['skipped_resources'].append({
				'type': 'subnet',
				'id': s.get('id'),
				'reason': f"Unknown tier '{tier}' - must be public/private/nonroutable"
			})
	
	# Route tables
	rts = data.get('route_tables', [])
	validation['total_resources'] += len(rts)
	validation['included_resources'] += len(rts)
	
	# Routes
	routes = data.get('routes', [])
	for r in routes:
		validation['total_resources'] += 1
		dest = r.get('DestinationCidrBlock', '')
		if dest in ['0.0.0.0/0', ''] or r.get('GatewayId', '').startswith('local'):
			validation['skipped_resources'].append({
				'type': 'route',
				'dest': dest,
				'reason': 'Default route or local route (managed by modules)'
			})
		else:
			validation['included_resources'] += 1
	
	# Security Groups (including default)
	sgs = data.get('security_groups', [])
	validation['total_resources'] += len(sgs)
	validation['included_resources'] += len(sgs)
	
	# NAT Gateways
	nats = data.get('nat_gateways', [])
	validation['total_resources'] += len(nats)
	validation['included_resources'] += len(nats)
	
	# Internet Gateway
	if data.get('internet_gateway'):
		validation['total_resources'] += 1
		validation['included_resources'] += 1
	
	# VPC Endpoints
	endpoints = data.get('vpc_endpoints', [])
	validation['total_resources'] += len(endpoints)
	for ep in endpoints:
		if ep.get('type') == 'Gateway':
			validation['included_resources'] += 1
		elif ep.get('type') == 'Interface':
			validation['included_resources'] += 1
		else:
			validation['warnings'].append(f"Unknown endpoint type: {ep.get('type')}")
	
	# DHCP Options
	if data.get('dhcp_options'):
		validation['total_resources'] += 1
		validation['included_resources'] += 1
	
	# NACLs
	nacls = data.get('network_acls', [])
	validation['total_resources'] += len(nacls)
	for nacl in nacls:
		if nacl.get('is_default'):
			validation['skipped_resources'].append({
				'type': 'nacl',
				'id': nacl.get('id'),
				'reason': 'Default NACL (AWS managed, cannot be imported)'
			})
		else:
			validation['included_resources'] += 1
	
	return validation


def _write_tfvars(discovery_path: str, out_path: str) -> dict:
		# DHCP Options
		f.write("\n# DHCP Options\n")
		f.write(f"domain_name          = \"{values['domain_name']}\"\n")
		f.write(f"domain_name_servers  = {json.dumps(values['domain_name_servers'])}\n")
		f.write(f"ntp_servers          = {json.dumps(values['ntp_servers'])}\n")
		f.write(f"netbios_name_servers = {json.dumps(values['netbios_name_servers'])}\n")
		f.write(f"netbios_node_type    = {values['netbios_node_type']}\n")
	with open(discovery_path, "r") as f:
		data = json.load(f)

	out_dir = os.path.dirname(out_path)
	values = _extract_tfvars_values(data, out_dir)
	nacl_rules = _extract_nacl_rules(data, values["vpc_name"])
	
	# Extract VPC endpoint SG IDs first
	vpc_endpoint_sg_ids = _extract_vpc_endpoint_sgs(data)
	
	# Validate coverage
	validation = _validate_tfvars_coverage(data, vpc_endpoint_sg_ids)
	
	with open(out_path, "w", newline="\n") as f:
		f.write("base_tag = {\n")
		f.write(f"  Region      = \"{values['region']}\"\n")
		f.write("  application = \"ntw\"\n")
		f.write(f"  environment = \"{values['env']}\"\n")
		f.write('  "created by" = "Cloud Network Team"\n')
		f.write("}\n")

		f.write(f"environment = \"{values['env']}\"\n")
		f.write(f"region      = \"{values['region']}\"\n\n")
		f.write(f"vpc_cidr = \"{values['vpc_cidr']}\"\n\n")

		f.write("additional_cidrs = [\n")
		for c in values["additional_cidrs"]:
			f.write(f"  \"{c}\",\n")
		f.write("]\n\n")

		f.write("public_subnet_cidrs = [\n")
		for c in values["public_subnet_cidrs"]:
			f.write(f"  \"{c}\",\n")
		f.write("]\n\n")

		f.write("private_subnet_cidrs = [\n")
		for c in values["private_subnet_cidrs"]:
			f.write(f"  \"{c}\",\n")
		f.write("]\n\n")

		f.write("nonroutable_subnet_cidrs = [\n")
		for c in values["nonroutable_subnet_cidrs"]:
			f.write(f"  \"{c}\",\n")
		f.write("]\n\n")

		f.write("azs = [\n")
		for az in values["azs"]:
			f.write(f"  \"{az}\",\n")
		f.write("]\n\n")

		f.write(f"vpc_name = \"{values['vpc_name']}\"\n")

		# Provisioning toggles (so import + lifecycle can be controlled via tfvars)
		f.write("\n# Provisioning toggles\n")
		f.write("enable_additional_cidrs          = true\n")
		f.write("enable_public_subnets            = true\n")
		f.write("enable_private_subnets           = true\n")
		f.write("enable_nonroutable_subnets       = true\n")
		f.write("enable_gateways                  = true\n")
		f.write("enable_internet_gateway          = true\n")
		f.write("enable_public_nat_gateways       = true\n")
		f.write("enable_private_nat_gateways      = true\n")
		f.write("enable_public_route_table        = true\n")
		f.write("enable_private_route_tables      = true\n")
		f.write("enable_nonroutable_route_tables  = true\n")
		f.write("enable_nacls                     = true\n")
		f.write("enable_public_nacl               = true\n")
		f.write("enable_private_nonroutable_nacl  = true\n")
		f.write("enable_s3_gateway_endpoint       = true\n")
		f.write("enable_interface_endpoints       = true\n")
		
		# VPC endpoint security groups - set based on discovery
		endpoint_sgs = _extract_vpc_endpoint_sgs(data)
		if endpoint_sgs:
			f.write("enable_vpc_endpoints_sg          = false  # Using existing SGs\n")
			f.write(f"vpc_endpoints_security_group_ids = {json.dumps(endpoint_sgs)}\n")
		else:
			f.write("enable_vpc_endpoints_sg          = true\n")
			f.write("vpc_endpoints_security_group_ids = []\n")
		
		# Interface VPC Endpoints discovered from AWS
		interface_endpoints = {}
		for ep in data.get('vpc_endpoints', []):
			if ep.get('type') == 'Interface':
				svc_name = ep.get('service_name', '')
				if svc_name:
					# Extract service suffix (e.g., 'ec2' from 'com.amazonaws.us-east-1.ec2')
					service = svc_name.split('.')[-1]
					interface_endpoints[service] = {
						'private_dns_enabled': ep.get('private_dns_enabled', True),
						'tags': {}
					}
		
		if interface_endpoints:
			f.write("\n# Interface VPC Endpoints discovered from AWS\n")
			f.write("interface_vpc_endpoints = {\n")
			for service, config in sorted(interface_endpoints.items()):
				f.write(f"  \"{service}\" = {{\n")
				f.write(f"    private_dns_enabled = {str(config['private_dns_enabled']).lower()}\n")
				f.write(f"    tags                = {{}}\n")
				f.write(f"  }}\n")
			f.write("}\n")
		else:
			f.write("\n# No interface VPC endpoints discovered\n")
			f.write("interface_vpc_endpoints = {}\n")

		# NACL rules discovered from AWS (import-friendly)
		f.write("\n# NACL rules\n")
		f.write("nacl_rules = {\n")
		for key in ["public_ingress", "public_egress", "private_ingress", "private_egress"]:
			f.write(f"  {key} = [\n")
			for r in nacl_rules.get(key) or []:
				f.write("    {\n")
				f.write(f"      rule_number = {r['rule_number']}\n")
				f.write(f"      protocol    = \"{r['protocol']}\"\n")
				f.write(f"      rule_action = \"{r['rule_action']}\"\n")
				if "cidr_block" in r:
					f.write(f"      cidr_block  = \"{r['cidr_block']}\"\n")
				if "ipv6_cidr_block" in r:
					f.write(f"      ipv6_cidr_block = \"{r['ipv6_cidr_block']}\"\n")
				f.write(f"      from_port   = {r['from_port']}\n")
				f.write(f"      to_port     = {r['to_port']}\n")
				f.write("    },\n")
			f.write("  ]\n")
		f.write("}\n")

		# Security groups discovered from AWS (import ALL non-default SGs)
		security_groups = _extract_security_groups(data, vpc_endpoint_sg_ids=[])
		if security_groups:
			f.write("\n# Security groups discovered from VPC\n")
			f.write("extra_security_groups = {\n")
			for key, sg in security_groups.items():
				f.write(f"  \"{key}\" = {{\n")
				f.write(f"    name        = \"{sg['name']}\"\n")
				f.write(f"    description = \"{sg['description']}\"\n")
				
				# Ingress rules
				f.write("    ingress_rules = [\n")
				for rule in sg.get('ingress_rules', []):
					f.write("      {\n")
					f.write(f"        from_port   = {rule['from_port']}\n")
					f.write(f"        to_port     = {rule['to_port']}\n")
					f.write(f"        protocol    = \"{rule['protocol']}\"\n")
					if rule.get('cidr_blocks'):
						f.write(f"        cidr_blocks = {json.dumps(rule['cidr_blocks'])}\n")
					if rule.get('ipv6_cidr_blocks'):
						f.write(f"        ipv6_cidr_blocks = {json.dumps(rule['ipv6_cidr_blocks'])}\n")
					if rule.get('source_security_group_id'):
						f.write(f"        source_security_group_id = \"{rule['source_security_group_id']}\"\n")
					if rule.get('description'):
						f.write(f"        description = \"{rule['description']}\"\n")
					f.write("      },\n")
				f.write("    ]\n")
				
				# Egress rules
				f.write("    egress_rules = [\n")
				for rule in sg.get('egress_rules', []):
					f.write("      {\n")
					f.write(f"        from_port   = {rule['from_port']}\n")
					f.write(f"        to_port     = {rule['to_port']}\n")
					f.write(f"        protocol    = \"{rule['protocol']}\"\n")
					if rule.get('cidr_blocks'):
						f.write(f"        cidr_blocks = {json.dumps(rule['cidr_blocks'])}\n")
					if rule.get('ipv6_cidr_blocks'):
						f.write(f"        ipv6_cidr_blocks = {json.dumps(rule['ipv6_cidr_blocks'])}\n")
					if rule.get('source_security_group_id'):
						f.write(f"        source_security_group_id = \"{rule['source_security_group_id']}\"\n")
					if rule.get('description'):
						f.write(f"        description = \"{rule['description']}\"\n")
					f.write("      },\n")
				f.write("    ]\n")
				
				# Tags
				f.write("    tags = {\n")
				f.write(f"      Name = \"{sg['name_tag']}\"\n")
				for tag_key, tag_val in sg.get('tags', {}).items():
					# Quote tag keys if they contain spaces or special characters
					if ' ' in tag_key or not re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', tag_key):
						f.write(f"      \"{tag_key}\" = \"{tag_val}\"\n")
					else:
						f.write(f"      {tag_key} = \"{tag_val}\"\n")
				f.write("    }\n")
				
				f.write("  }\n")
			f.write("}\n")
		else:
			f.write("\n# No additional security groups discovered\n")
			f.write("extra_security_groups = {}\n")
		
		# Extra routes discovered from AWS
		rt_tier_map = _build_route_table_tier_map(data)
		extra_routes = _extract_extra_routes(data, rt_tier_map)
		
		f.write("\n# Extra routes discovered from route tables\n")
		for tier in ['public', 'private', 'nonroutable']:
			routes = extra_routes.get(tier, [])
			if routes:
				f.write(f"{tier}_extra_routes = [\n")
				for route in routes:
					f.write("  {\n")
					f.write(f"    destination_cidr_block = \"{route['destination_cidr_block']}\"\n")
					f.write(f"    target_type            = \"{route['target_type']}\"\n")
					f.write(f"    target_id              = \"{route['target_id']}\"\n")
					f.write("  },\n")
				f.write("]\n")
			else:
				f.write(f"{tier}_extra_routes = []\n")

	# Print validation report
	print("\n" + "="*60)
	print("TFVARS GENERATION VALIDATION REPORT")
	print("="*60)
	print(f"Total resources discovered: {validation['total_resources']}")
	print(f"Resources included in tfvars: {validation['included_resources']}")
	print(f"Resources skipped: {len(validation['skipped_resources'])}")
	
	if validation['skipped_resources']:
		print("\nSkipped Resources (with reasons):")
		for skip in validation['skipped_resources']:
			skip_type = skip.get('type', 'unknown')
			skip_id = skip.get('id', skip.get('dest', 'N/A'))
			reason = skip.get('reason', 'No reason provided')
			print(f"  • {skip_type}: {skip_id}")
			print(f"    Reason: {reason}")
	
	if validation['warnings']:
		print("\nWarnings:")
		for warn in validation['warnings']:
			print(f"  ⚠ {warn}")
	
	coverage_pct = (validation['included_resources'] / validation['total_resources'] * 100) if validation['total_resources'] > 0 else 0
	print(f"\nCoverage: {coverage_pct:.1f}% of discovered resources will be managed in Terraform")
	print("="*60 + "\n")

	return values


def main() -> int:
	parser = argparse.ArgumentParser(description="Generate terraform.tfvars under *-import folders from discovery JSON.")
	parser.add_argument(
		"path",
		nargs="?",
		default=None,
		help="Optional: import folder path (env/.../*-import) OR a discovery json file path. If omitted, prompts.",
	)
	parser.add_argument(
		"--backend-bucket",
		default="tmo-aws-tf-state-bucket",
		help="S3 bucket to use in backend-config (default: tmo-aws-tf-state-bucket).",
	)
	parser.add_argument(
		"--backend-prefix",
		default="envs",
		help="S3 key prefix to use (default: envs).",
	)
	args = parser.parse_args()

	target_dir = os.getcwd()
	json_files = glob.glob(os.path.join(target_dir, 'env', '*', '*-import', 'vpc_resources_vpc-*.json'))
	if not json_files:
		print("No vpc_resources_vpc-*.json file found in env/*/*-import/", file=sys.stderr)
		return 1

	selected_files = []
	if args.path:
		p = args.path
		if os.path.isdir(p):
			selected_files = sorted(glob.glob(os.path.join(p, 'vpc_resources_vpc-*.json')))
			if selected_files:
				selected_files = [selected_files[-1]]
		elif os.path.isfile(p) and os.path.basename(p).startswith('vpc_resources_vpc-'):
			selected_files = [p]
		else:
			print(f"Invalid path: {p}", file=sys.stderr)
			return 1
	else:
		print("Available discovery JSON files:")
		json_files = sorted(json_files)
		for idx, jf in enumerate(json_files, 1):
			try:
				with open(jf) as f:
					jdata = json.load(f)
				vpc_tags = (jdata.get("vpc") or {}).get("tags", [])
				vpc_name = _tag_value(vpc_tags, "Name") or (jdata.get("vpc") or {}).get("id", "?")
			except Exception:
				vpc_name = "?"
			print(f"  {idx}. {jf} (VPC: {vpc_name})")

		sel = input(f"Select file(s) [1-{len(json_files)}] (comma-separated, press Enter for latest only): ").strip()
		if not sel:
			selected_files = [json_files[-1]]
		else:
			indices = [int(i) for i in sel.split(',') if i.strip().isdigit()]
			selected_files = [json_files[i-1] for i in indices if 0 < i <= len(json_files)]

	if not selected_files:
		print("No valid discovery JSON selected.", file=sys.stderr)
		return 1

	for discovery_path in selected_files:
		out_tfvars = os.path.join(os.path.dirname(discovery_path), "terraform.tfvars")
		values = _write_tfvars(discovery_path, out_tfvars)
		print(f"Wrote: {out_tfvars}")

		out_dir = os.path.dirname(discovery_path)
		backend_path = _write_backend_config(
			out_dir=out_dir,
			env=values["env"],
			state_folder=values["state_folder"],
			region=values["region"],
			bucket=args.backend_bucket,
			prefix=args.backend_prefix,
		)
		print(f"Wrote: {backend_path}")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())

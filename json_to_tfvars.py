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
		print("[DEBUG] Entering _extract_tfvars_values")
		# Initialize extra routes to avoid UnboundLocalError
		public_extra_routes = []
		private_extra_routes = []
		nonroutable_extra_routes = []
		try:
			# Build full route table objects for tfvars
			required_fields = [
				'env', 'region', 'vpc_cidr', 'additional_cidrs', 'public_subnet_cidrs',
				'private_subnet_cidrs', 'nonroutable_subnet_cidrs', 'azs', 'vpc_name',
				'state_folder', 'public_route_tables', 'private_route_tables', 'nonroutable_route_tables',
				   'public_extra_routes', 'private_extra_routes',
				'public_route_table_id', 'private_route_table_ids', 'nonroutable_route_table_ids'
			]
			print(f"[DEBUG] route_tables count: {len(discovery.get('route_tables', []))}")
			print(f"[DEBUG] subnets count: {len(discovery.get('subnets', []))}")
			print(f"[DEBUG] route_table_associations count: {len(discovery.get('route_table_associations', []))}")
			public_route_tables = []
			private_route_tables = []
			nonroutable_route_tables = []
			# Build lookup maps for associations and subnets
			associations = {a['route_table_id']: a for a in discovery.get('route_table_associations', []) if 'route_table_id' in a}
			subnet_tiers = {s['id']: (s.get('tier') or '').lower() for s in discovery.get('subnets', []) if 'id' in s}
			vpc_id = (discovery.get('vpc') or {}).get('id', '')

			for rt in discovery.get('route_tables', []):
				assoc = associations.get(rt.get('id'), {})
				subnet_id = assoc.get('subnet_id', '')
				tier = subnet_tiers.get(subnet_id, '')
				guessed = False
				# fallback: if no subnet, try to infer from name
				if not tier:
					name = ''
					for tag in rt.get('tags', []):
						if tag.get('Key') == 'Name':
							name = tag.get('Value', '').lower()
					if 'public' in name:
						tier = 'public'
					elif 'nonroutable' in name or 'nr' in name:
						tier = 'nonroutable'
					elif 'private' in name or 'pvt' in name:
						tier = 'private'
					else:
						tier = 'private'  # default fallback
					guessed = True
				if guessed:
					print(f"[WARN] Route table {rt.get('id')} had no subnet association or tier, guessed tier as '{tier}' from name '{name}'", file=sys.stderr)
				obj = {
					'id': rt.get('id'),
					'name': next((tag.get('Value') for tag in rt.get('tags', []) if tag.get('Key') == 'Name'), ''),
					'tags': {tag.get('Key'): tag.get('Value') for tag in rt.get('tags', []) if tag.get('Key')},
					'tier': tier,
					'vpc_id': vpc_id,
					'subnet_id': subnet_id,
					'main': assoc.get('main', False),
				}
				# Always include the route table, even if tier was guessed
				if obj['tier'] == 'public':
					public_route_tables.append(obj)
				elif obj['tier'] == 'private':
					private_route_tables.append(obj)
				elif obj['tier'] == 'nonroutable':
					nonroutable_route_tables.append(obj)
				else:
					# If tier is still not recognized, default to private and warn
					print(f"[WARN] Route table {rt.get('id')} has unrecognized tier '{obj['tier']}', defaulting to 'private'", file=sys.stderr)
					private_route_tables.append(obj)
					# Extract extra routes for all tiers
					route_tables = {rt.get('id'): (rt.get('tier') or 'private').lower() for rt in discovery.get('route_tables', [])}
					extra_routes = _extract_extra_routes(discovery, route_tables)
					public_extra_routes = extra_routes.get('public', [])
					private_extra_routes = extra_routes.get('private', [])
					# nonroutable_extra_routes = extra_routes.get('nonroutable', [])
					nonroutable_extra_routes = extra_routes.get('nonroutable', [])

			# Route table ID mappings for reference
			public_route_table_id = ''
			private_route_table_ids = []
			nonroutable_route_table_ids = []
			for rt in discovery.get('route_tables', []):
				tier = (rt.get('tier') or 'private').lower()
				if tier == 'public':
					public_route_table_id = rt.get('id')
				elif tier == 'private':
					private_route_table_ids.append(rt.get('id'))
				elif tier == 'nonroutable':
					nonroutable_route_table_ids.append(rt.get('id'))
			# DHCP Options - extract from DhcpConfigurations if present
			dhcp_options = discovery.get("dhcp_options") or {}
			dhcp_conf = (dhcp_options.get("options") or {}).get("DhcpConfigurations") or []
			def get_dhcp_value(key, default=None, as_list=False):
				for conf in dhcp_conf:
					if conf.get("Key") == key:
						vals = [v.get("Value") for v in conf.get("Values", []) if v.get("Value") is not None]
						if as_list:
							return vals if vals else (default if default is not None else [])
						return vals[0] if vals else (default if default is not None else "")
				return default if default is not None else ([] if as_list else "")
			domain_name = get_dhcp_value("domain-name", "ec2.internal")
			domain_name_servers = get_dhcp_value("domain-name-servers", ["AmazonProvidedDNS"], as_list=True)
			ntp_servers = get_dhcp_value("ntp-servers", ["0.0.0.0"], as_list=True)
			netbios_name_servers = get_dhcp_value("netbios-name-servers", ["192.168.1.1"], as_list=True)
			netbios_node_type = get_dhcp_value("netbios-node-type", 2)
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

			result = {
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
				"public_extra_routes": public_extra_routes,
				"private_extra_routes": private_extra_routes,
				   "nonroutable_extra_routes": nonroutable_extra_routes,
				"public_route_table_id": public_route_table_id,
				"private_route_table_ids": private_route_table_ids,
				"nonroutable_route_table_ids": nonroutable_route_table_ids,
				"public_route_tables": public_route_tables,
				"private_route_tables": private_route_tables,
				"nonroutable_route_tables": nonroutable_route_tables,
			}
			# Check for missing required fields
			print(f"[DEBUG] Final tfvars result: {json.dumps(result, indent=2)[:1000]}...")
			missing = [k for k in required_fields if k not in result or result[k] is None]
			if missing:
				print(f"ERROR: Missing required fields in tfvars generation: {missing}", file=sys.stderr)
				print(f"Discovery input: {json.dumps(discovery)[:1000]}...", file=sys.stderr)
				raise ValueError(f"Missing required fields for tfvars: {missing}")
			print("[DEBUG] _extract_tfvars_values returning:", result)
			return result
		except Exception as e:
			print(f"[DEBUG] Exception in _extract_tfvars_values: {e}", file=sys.stderr)
			import traceback; traceback.print_exc()
			return {}


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
	# Try to find public and private NACLs by Name tag, fallback to first non-default NACLs if not found
	public_nacl_name = f"ntw-{vpc_name}-public-nacl"
	prn_nacl_name = f"ntw-{vpc_name}-private-nacl"
	public_nacl = _find_by_tag_name(nacls, public_nacl_name)
	prn_nacl = _find_by_tag_name(nacls, prn_nacl_name)
	# Fallback: use first non-default NACLs if name match fails
	if not public_nacl:
		public_nacl = next((n for n in nacls if not n.get("is_default")), None)
	if not prn_nacl:
		prn_nacl = next((n for n in nacls if not n.get("is_default") and n != public_nacl), None)
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
	print(f"[DEBUG] Entering _write_tfvars for {discovery_path}, out_path={out_path}")
	# Output full route table objects for all tiers
	print(f"[DEBUG] Entering _write_tfvars for {discovery_path}, out_path={out_path}")
	with open(discovery_path) as f_json:
		data = json.load(f_json)
	out_dir = os.path.dirname(out_path)
	values = _extract_tfvars_values(data, out_dir)
	print(f"[DEBUG] _extract_tfvars_values returned: {type(values)} {values if isinstance(values, dict) else ''}")
	# Return values after writing file
	if not values:
		print("ERROR: No values generated for tfvars.", file=sys.stderr)
		raise ValueError("No values generated for tfvars.")
	# Validate required keys for backend config
	required_keys = ["env", "state_folder", "region"]
	missing = [k for k in required_keys if k not in values or not values[k]]
	if missing:
		print(f"ERROR: Missing required backend keys: {missing}", file=sys.stderr)
		print(f"Values: {json.dumps(values)}", file=sys.stderr)
		raise ValueError(f"Missing required backend keys: {missing}")

	nacl_rules = _extract_nacl_rules(data, values["vpc_name"])
	vpc_endpoint_sg_ids = _extract_vpc_endpoint_sgs(data)
	validation = _validate_tfvars_coverage(data, vpc_endpoint_sg_ids)

	# Build a map of route_table_id -> list of routes
	routes_map = {}
	for route in data.get('routes', []):
		rt_id = route.get('route_table_id')
		if not rt_id:
			continue
		if rt_id not in routes_map:
			routes_map[rt_id] = []
		# Remove route_table_id from the route dict for tfvars output
		route_out = {k: v for k, v in route.items() if k != 'route_table_id'}
		routes_map[rt_id].append(route_out)

	def write_route_tables_block(name, tables):
		f.write(f"{name} = [\n")
		for rt in tables:
			f.write("  {\n")
			f.write(f"    id = \"{rt['id']}\"\n")
			f.write(f"    name = \"{rt['name']}\"\n")
			f.write(f"    vpc_id = \"{rt['vpc_id']}\"\n")
			f.write(f"    subnet_id = \"{rt.get('subnet_id', '')}\"\n")
			f.write(f"    tier = \"{rt['tier']}\"\n")
			f.write(f"    main = {str(rt.get('main', False)).lower()}\n")
			f.write("    tags = {\n")
			for k, v in (rt['tags'] or {}).items():
				f.write(f"      \"{k}\" = \"{v}\"\n")
			f.write("    }\n")
			# Inject routes if present
			routes = routes_map.get(rt['id'], [])
			if routes:
				f.write(",\n    routes = [\n")
				for r in routes:
					# Write each route as a map
					f.write("    { ")
					f.write(", ".join(f"{k} = \"{v}\"" for k, v in r.items()))
					f.write(" },\n")
				f.write("  ]\n")
			f.write("  },\n")
		f.write("]\n")
	
	with open(out_path, "w", newline="\n") as f:
		f.write("base_tag = {\n")
		f.write(f"  Region      = \"{values['region']}\"\n")
		f.write("  application = \"ntw\"\n")
		f.write(f"  environment = \"{values['env']}\"\n")
		f.write('  "created by" = "Cloud Network Team"\n')
		f.write("}\n")

		f.write(f"environment = \"{values['env']}\"\n")
		f.write(f"region      = \"{values['region']}\"\n\n")

		# Provisioning toggles (move here)
		f.write("# Provisioning toggles\n")
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

		f.write(f"\nvpc_cidr = \"{values['vpc_cidr']}\"\n\n")

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

		# Route table objects for all tiers
		f.write("\n# Route table objects for all tiers\n")
		write_route_tables_block("public_route_tables", values.get("public_route_tables", []))
		write_route_tables_block("private_route_tables", values.get("private_route_tables", []))
		write_route_tables_block("nonroutable_route_tables", values.get("nonroutable_route_tables", []))

		# DHCP Option Set values
		f.write("\n# DHCP Option Set\n")
		f.write("enable_dhcp_option_set = true\n")
		f.write(f"domain_name = \"{values['domain_name']}\"\n")
		f.write("domain_name_servers = [\n")
		for s in values["domain_name_servers"]:
			f.write(f"  \"{s}\",\n")
		f.write("]\n")
		f.write("ntp_servers = [\n")
		for s in values["ntp_servers"]:
			f.write(f"  \"{s}\",\n")
		f.write("]\n")
		f.write("netbios_name_servers = [\n")
		for s in values["netbios_name_servers"]:
			f.write(f"  \"{s}\",\n")
		f.write("]\n")
		f.write(f"netbios_node_type = {values['netbios_node_type']}\n")

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
			f.write("\n# Extra routes discovered from route tables\n")
			# Output all extra routes including nonroutable_extra_routes
			for tier in ["public", "private", "nonroutable"]:
				key = f"{tier}_extra_routes"
				if values.get(key):
					f.write(f"{key} = [\n")
					for route in values[key]:
						f.write("  {\n")
						f.write(f"    destination_cidr_block = \"{route['destination_cidr_block']}\"\n")
						f.write(f"    target_type            = \"{route['target_type']}\"\n")
						f.write(f"    target_id              = \"{route['target_id']}\"\n")
						f.write("  },\n")
					f.write("]\n")
				else:
					f.write(f"{key} = []\n")

			# Output route table ID mappings for reference
			f.write("\n# Route table IDs for reference\n")
			f.write(f"public_route_table_id = \"{values.get('public_route_table_id', '')}\"\n")
			f.write("private_route_table_ids = [\n")
			for rid in values.get('private_route_table_ids', []):
				f.write(f"  \"{rid}\",\n")
			f.write("]\n")
			f.write("nonroutable_route_table_ids = [\n")
			for rid in values.get('nonroutable_route_table_ids', []):
				f.write(f"  \"{rid}\",\n")
			f.write("]\n")

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
	print("[DEBUG] Entering main()")
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
	print(f"[DEBUG] CWD: {target_dir}")
	json_files = glob.glob(os.path.join(target_dir, 'env', '*', '*-import', 'vpc_resources_vpc-*.json'))
	print(f"[DEBUG] Found {len(json_files)} discovery JSON files")
	if not json_files:
		print("No vpc_resources_vpc-*.json file found in env/*/*-import/", file=sys.stderr)
		return 1

	selected_files = []
	if args.path:
		p = args.path
		print(f"[DEBUG] args.path provided: {p}")
		if os.path.isdir(p):
			selected_files = sorted(glob.glob(os.path.join(p, 'vpc_resources_vpc-*.json')))
			print(f"[DEBUG] Directory mode, found: {selected_files}")
			if selected_files:
				selected_files = [selected_files[-1]]
		elif os.path.isfile(p) and os.path.basename(p).startswith('vpc_resources_vpc-'):
			print(f"[DEBUG] File mode, using: {p}")
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

		# --- RAM resource logic: read ram_resource.json and filter vpc_match=true ---
		# This block should be inside the for discovery_path in selected_files loop
		# so move it below, not here
			print(f"  {idx}. {jf} (VPC: {vpc_name})")

		sel = input(f"Select file(s) [1-{len(json_files)}] (comma-separated, press Enter for latest only): ").strip()
		if not sel:
			selected_files = [json_files[-1]]
		else:
			indices = [int(i) for i in sel.split(',') if i.strip().isdigit()]
			selected_files = [json_files[i-1] for i in indices if 0 < i <= len(json_files)]

	print(f"[DEBUG] Selected files: {selected_files}")
	if not selected_files:
		print("No valid discovery JSON selected.", file=sys.stderr)
		return 1

	for discovery_path in selected_files:
		# Write main tfvars content
		out_tfvars = os.path.join(os.path.dirname(discovery_path), "terraform.tfvars")
		values = _write_tfvars(discovery_path, out_tfvars)
		print(f"Wrote: {out_tfvars}")

		# --- RAM resource logic: read ram_resource.json and filter vpc_match=true ---
		import_folder = os.path.dirname(discovery_path)
		ram_json_path = os.path.join(import_folder, "ram_resource.json")
		ram_resource_shares = []
		if os.path.isfile(ram_json_path):
			try:
				with open(ram_json_path) as ramf:
					ram_data = json.load(ramf)
				for share in ram_data.get("shares", []):
					filtered_resources = [r for r in share.get("resources", []) if r.get("vpc_match") is True]
					if filtered_resources:
						ram_resource_shares.append({
						"name": share.get("name", ""),
						"arn": share.get("arn", ""),
						"resources": filtered_resources
					})
			except Exception as e:
				print(f"[WARN] Failed to read RAM resources: {e}", file=sys.stderr)

		# Append RAM resource shares to tfvars if present
		if ram_resource_shares:
			with open(out_tfvars, "a", newline="\n") as f:
				f.write("\n# AWS RAM Resource Shares\n")
				f.write("ram_resource_shares = [\n")
				for share in ram_resource_shares:
					f.write("  {\n")
					f.write(f"    name = \"{share.get('name', '')}\"\n")
					f.write(f"    arn  = \"{share.get('arn', '')}\"\n")
					if share.get('resources'):
						f.write("    resources = [\n")
						for res in share['resources']:
							f.write(f"      {{ type = \"{res.get('type', '')}\", arn = \"{res.get('arn', '')}\", vpc_match = {str(res.get('vpc_match', False)).lower()} }},\n")
						f.write("    ]\n")
					else:
						f.write("    resources = []\n")
					f.write("  },\n")
				f.write("]\n")

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
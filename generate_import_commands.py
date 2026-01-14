import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple


def _tag_value(tags: List[Dict[str, Any]], key: str) -> str:
    for t in tags or []:
        if t.get("Key") == key:
            v = (t.get("Value") or "").strip()
            if v:
                return v
    for t in tags or []:
        if (t.get("Key") or "").lower() == key.lower():
            v = (t.get("Value") or "").strip()
            if v:
                return v
    return ""


def _load_latest_discovery_json(import_dir: str) -> str:
    paths = sorted(glob.glob(os.path.join(import_dir, "vpc_resources_vpc-*_*.json")))
    if not paths:
        raise FileNotFoundError(f"No discovery JSON found under {import_dir}")
    return paths[-1]


def _parse_tfvars(tfvars_path: str) -> Dict[str, Any]:
    if not os.path.isfile(tfvars_path):
        raise FileNotFoundError(tfvars_path)

    with open(tfvars_path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]

    def parse_string(name: str) -> Optional[str]:
        pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*\"([^\"]*)\"\s*$")
        for ln in lines:
            m = pattern.match(ln)
            if m:
                return m.group(1)
        return None

    def parse_list(name: str) -> List[str]:
        start_re = re.compile(rf"^\s*{re.escape(name)}\s*=\s*\[\s*$")
        end_re = re.compile(r"^\s*\]\s*$")
        in_list = False
        out: List[str] = []
        for ln in lines:
            if not in_list:
                if start_re.match(ln):
                    in_list = True
                continue
            if end_re.match(ln):
                break
            m = re.search(r"\"([^\"]+)\"", ln)
            if m:
                out.append(m.group(1))
        return out

    def parse_bool(name: str) -> Optional[bool]:
        pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*(true|false)\s*$")
        for ln in lines:
            m = pattern.match(ln)
            if m:
                return m.group(1) == "true"
        return None

    def parse_rule_numbers(list_name: str) -> List[int]:
        # Very small HCL-ish parser for:
        # nacl_rules = {
        #   <list_name> = [
        #     { rule_number = 100 ... },
        #   ]
        # }
        start_re = re.compile(rf"^\s*{re.escape(list_name)}\s*=\s*\[\s*$")
        end_re = re.compile(r"^\s*\]\s*,?\s*$")
        in_list = False
        out: List[int] = []
        for ln in lines:
            if not in_list:
                if start_re.match(ln):
                    in_list = True
                continue
            if end_re.match(ln):
                break
            m = re.search(r"\brule_number\s*=\s*(\d+)", ln)
            if m:
                try:
                    out.append(int(m.group(1)))
                except Exception:
                    pass
        # de-dupe while preserving order
        seen = set()
        uniq: List[int] = []
        for n in out:
            if n in seen:
                continue
            seen.add(n)
            uniq.append(n)
        return uniq

    def parse_security_groups() -> List[str]:
        # Parse security_groups map keys
        # security_groups = {
        #   "key1" = { ... }
        # }
        sg_re = re.compile(r'^\s*\"([^\"]+)\"\s*=\s*\{\s*$')
        in_sg_block = False
        sg_keys = []
        for ln in lines:
            if re.match(r'^\s*security_groups\s*=\s*\{\s*$', ln):
                in_sg_block = True
                continue
            if in_sg_block:
                if re.match(r'^\s*\}\s*$', ln):
                    break
                m = sg_re.match(ln)
                if m:
                    sg_keys.append(m.group(1))
        return sg_keys

    def parse_interface_vpc_endpoints() -> List[str]:
        # Parse interface_vpc_endpoints map keys
        # interface_vpc_endpoints = {
        #   "ec2" = { ... }
        #   "ssm" = { ... }
        # }
        key_re = re.compile(r'^\s*\"([^\"]+)\"\s*=\s*\{\s*$')
        in_ep_block = False
        brace_depth = 0
        ep_keys = []
        for ln in lines:
            if re.match(r'^\s*interface_vpc_endpoints\s*=\s*\{\s*$', ln):
                in_ep_block = True
                brace_depth = 1
                continue
            if in_ep_block:
                # Track opening braces
                if '{' in ln:
                    brace_depth += ln.count('{')
                # Track closing braces
                if '}' in ln:
                    brace_depth -= ln.count('}')
                # If we're back to depth 0, we've exited the main block
                if brace_depth == 0:
                    break
                # Extract endpoint key at depth 1
                m = key_re.match(ln)
                if m:
                    ep_keys.append(m.group(1))
        return ep_keys

    def parse_extra_routes(tier: str) -> List[Dict[str, str]]:
        # Parse <tier>_extra_routes list to get full route objects
        # public_extra_routes = [
        #   {
        #     destination_cidr_block = "1.2.3.0/24"
        #     target_type            = "transit_gateway_id"
        #     target_id              = "tgw-xxx"
        #   },
        # ]
        list_name = f"{tier}_extra_routes"
        start_re = re.compile(rf"^\s*{re.escape(list_name)}\s*=\s*\[\s*$")
        end_re = re.compile(r"^\s*\]\s*$")
        obj_start_re = re.compile(r'^\s*\{\s*$')
        obj_end_re = re.compile(r'^\s*\},?\s*$')
        in_list = False
        in_obj = False
        routes = []
        current_obj = {}
        
        for ln in lines:
            if not in_list:
                if start_re.match(ln):
                    in_list = True
                continue
            if end_re.match(ln):
                break
            if not in_obj:
                if obj_start_re.match(ln):
                    in_obj = True
                    current_obj = {}
                continue
            if obj_end_re.match(ln):
                if current_obj:
                    routes.append(current_obj)
                in_obj = False
                continue
            
            # Parse fields
            m = re.search(r'destination_cidr_block\s*=\s*\"([^\"]+)\"', ln)
            if m:
                current_obj['destination_cidr_block'] = m.group(1)
            m = re.search(r'target_type\s*=\s*\"([^\"]+)\"', ln)
            if m:
                current_obj['target_type'] = m.group(1)
            m = re.search(r'target_id\s*=\s*\"([^\"]+)\"', ln)
            if m:
                current_obj['target_id'] = m.group(1)
        
        return routes

    return {
        "environment": parse_string("environment"),
        "region": parse_string("region"),
        "vpc_name": parse_string("vpc_name"),
        "vpc_cidr": parse_string("vpc_cidr"),
        "additional_cidrs": parse_list("additional_cidrs"),
        "public_subnet_cidrs": parse_list("public_subnet_cidrs"),
        "private_subnet_cidrs": parse_list("private_subnet_cidrs"),
        "nonroutable_subnet_cidrs": parse_list("nonroutable_subnet_cidrs"),
        "azs": parse_list("azs"),
        # NACL rule numbers (only imports those that are declared in tfvars)
        "nacl_public_ingress_rule_numbers": parse_rule_numbers("public_ingress"),
        "nacl_public_egress_rule_numbers": parse_rule_numbers("public_egress"),
        "nacl_private_ingress_rule_numbers": parse_rule_numbers("private_ingress"),
        "nacl_private_egress_rule_numbers": parse_rule_numbers("private_egress"),
        # Extra resources
        "security_group_keys": parse_security_groups(),
        "public_extra_routes": parse_extra_routes("public"),
        "private_extra_routes": parse_extra_routes("private"),
        "nonroutable_extra_routes": parse_extra_routes("nonroutable"),
        # Boolean flags
        "enable_s3_gateway_endpoint": parse_bool("enable_s3_gateway_endpoint"),
        "enable_interface_endpoints": parse_bool("enable_interface_endpoints"),
        "enable_vpc_endpoints_sg": parse_bool("enable_vpc_endpoints_sg"),
        # VPC endpoints
        "interface_vpc_endpoints": parse_interface_vpc_endpoints(),
    }


def _normalize_protocol(proto: Any) -> str:
    # AWS may return protocol as str/int; terraform import id expects the raw protocol number string (e.g. "-1", "6").
    if proto is None:
        return "-1"
    return str(proto).strip()


def _validate_import_coverage(discovery: Dict[str, Any], tfv: Dict[str, Any]) -> Dict[str, Any]:
    """Validate that all importable resources from discovery JSON will get import commands."""
    validation = {
        'total_importable': 0,
        'will_import': 0,
        'skipped': [],
        'warnings': []
    }
    
    # VPC
    if discovery.get('vpc'):
        validation['total_importable'] += 1
        validation['will_import'] += 1
    
    # CIDR associations (non-primary)
    cidrs = [c for c in discovery.get('cidr_block_associations', []) if not c.get('primary')]
    validation['total_importable'] += len(cidrs)
    validation['will_import'] += len(cidrs)
    
    # Subnets
    subnets = discovery.get('subnets', [])
    for s in subnets:
        validation['total_importable'] += 1
        tier = (s.get('tier') or '').lower()
        if tier in ['public', 'private', 'nonroutable']:
            validation['will_import'] += 1
        else:
            validation['skipped'].append({
                'type': 'subnet',
                'id': s.get('id'),
                'reason': f"Unknown tier '{tier}'"
            })
    
    # Route tables
    rts = discovery.get('route_tables', [])
    validation['total_importable'] += len(rts)
    validation['will_import'] += len(rts)
    
    # Route table associations
    rtas = discovery.get('route_table_associations', [])
    for rta in rtas:
        validation['total_importable'] += 1
        if rta.get('main'):
            validation['skipped'].append({
                'type': 'route_table_association',
                'id': rta.get('id'),
                'reason': 'Main route table association (AWS managed)'
            })
        else:
            validation['will_import'] += 1
    
    # Security Groups
    sgs = discovery.get('security_groups', [])
    sg_keys_in_tfvars = set(tfv.get('security_group_keys', []))
    for sg in sgs:
        validation['total_importable'] += 1
        if sg.get('group_name') == 'default':
            validation['skipped'].append({
                'type': 'security_group',
                'id': sg.get('id'),
                'reason': 'Default VPC security group (AWS managed)'
            })
        elif not sg_keys_in_tfvars:
            validation['skipped'].append({
                'type': 'security_group',
                'id': sg.get('id'),
                'reason': 'Not found in tfvars extra_security_groups map'
            })
        else:
            validation['will_import'] += 1
    
    # NAT Gateways
    nats = [n for n in discovery.get('nat_gateways', []) if n.get('state') != 'deleted']
    validation['total_importable'] += len(nats)
    validation['will_import'] += len(nats)
    
    # Internet Gateway
    if discovery.get('internet_gateway'):
        validation['total_importable'] += 1
        validation['will_import'] += 1
    
    # VPC Endpoints
    endpoints = discovery.get('vpc_endpoints', [])
    validation['total_importable'] += len(endpoints)
    enable_s3 = tfv.get('enable_s3_gateway_endpoint', False)
    enable_interface = tfv.get('enable_interface_endpoints', False)
    for ep in endpoints:
        ep_type = ep.get('type', '')
        if ep_type == 'Gateway' and not enable_s3:
            validation['skipped'].append({
                'type': 'vpc_endpoint',
                'id': ep.get('id'),
                'reason': 'Gateway endpoint but enable_s3_gateway_endpoint=false in tfvars'
            })
        elif ep_type == 'Interface' and not enable_interface:
            validation['skipped'].append({
                'type': 'vpc_endpoint',
                'id': ep.get('id'),
                'reason': 'Interface endpoint but enable_interface_endpoints=false in tfvars'
            })
        else:
            validation['will_import'] += 1
    
    # DHCP Options
    if discovery.get('dhcp_options'):
        validation['total_importable'] += 2  # options + association
        validation['will_import'] += 2
    
    # NACLs
    nacls = discovery.get('network_acls', [])
    for nacl in nacls:
        validation['total_importable'] += 1
        if nacl.get('is_default'):
            validation['skipped'].append({
                'type': 'nacl',
                'id': nacl.get('id'),
                'reason': 'Default NACL (AWS managed, will cause import errors)'
            })
        else:
            validation['will_import'] += 1
    
    # Extra routes
    routes = discovery.get('routes', [])
    for r in routes:
        dest = r.get('DestinationCidrBlock', '')
        gw = r.get('GatewayId', '')
        if dest and dest != '0.0.0.0/0' and not gw.startswith('local'):
            validation['total_importable'] += 1
            # Check if in tfvars
            found_in_tfvars = False
            for tier in ['public', 'private', 'nonroutable']:
                tier_routes = tfv.get(f'{tier}_extra_routes', [])
                for route in tier_routes:
                    if route.get('destination_cidr_block') == dest:
                        found_in_tfvars = True
                        break
                if found_in_tfvars:
                    break
            if found_in_tfvars:
                validation['will_import'] += 1
            else:
                validation['skipped'].append({
                    'type': 'route',
                    'dest': dest,
                    'reason': 'Not found in tfvars extra_routes'
                })
    
    return validation


def _emit_nacl_rule_imports(
    lines: List[str],
    tfv: Dict[str, Any],
    module_prefix: str,
    nacl_id: str,
    nacl_rules: List[Dict[str, Any]],
    ingress_resource: str,
    egress_resource: str,
    allowed_ingress_rule_numbers: List[int],
    allowed_egress_rule_numbers: List[int],
) -> None:
    if not nacl_id:
        return

    ingress_set = set(allowed_ingress_rule_numbers or [])
    egress_set = set(allowed_egress_rule_numbers or [])

    for r in nacl_rules or []:
        if (r.get("network_acl_id") or "") != nacl_id:
            continue
        rule_number = r.get("RuleNumber")
        if rule_number is None:
            continue
        try:
            rule_number_i = int(rule_number)
        except Exception:
            continue

        # Skip the implicit AWS default deny rules.
        if rule_number_i == 32767:
            continue

        egress = bool(r.get("Egress"))
        if (not egress and rule_number_i not in ingress_set) or (egress and rule_number_i not in egress_set):
            continue

        protocol = _normalize_protocol(r.get("Protocol"))
        import_id = f"{nacl_id}:{rule_number_i}:{protocol}:{str(egress).lower()}"
        addr = (
            f"{module_prefix}.aws_network_acl_rule.{egress_resource}[\"{rule_number_i}\"]"
            if egress
            else f"{module_prefix}.aws_network_acl_rule.{ingress_resource}[\"{rule_number_i}\"]"
        )
        _emit_import(
            lines,
            tfv.get("_tfvars_path", ""),
            addr,
            import_id,
            f"NACL rule {rule_number_i} ({'egress' if egress else 'ingress'})",
        )


def _bash_quote_single(s: str) -> str:
    # Safe single-quote for bash: close, escape, reopen
    return "'" + s.replace("'", "'\\''") + "'"


def _posix_path(path: str) -> str:
    # When running on Windows, Python may emit backslashes; bash + terraform are happier with '/'.
    return (path or "").replace("\\", "/")


def _emit_import(lines: List[str], tfvars_path: str, addr: str, import_id: str, label: str) -> None:
    # Calls bash function import_one <label> <addr> <import_id>
    lines.append(
        "import_one "
        + _bash_quote_single(label)
        + " "
        + _bash_quote_single(addr)
        + " "
        + _bash_quote_single(import_id or "")
    )


def _find_by_tag_name(items: List[Dict[str, Any]], name_value: str) -> Optional[Dict[str, Any]]:
    for it in items or []:
        if _tag_value(it.get("tags") or [], "Name") == name_value:
            return it
    return None


def _extract_nat_key_from_name(name: str) -> Optional[Tuple[str, str]]:
    # nat-public-<n>-<cidr> or nat-private-<n>-<cidr>
    m = re.match(r"^(nat-(public|private))-\d+-(.+)$", name)
    if not m:
        return None
    kind = m.group(2)
    cidr = m.group(3)
    return kind, cidr


def generate(import_dir: str, tfvars_path: str, discovery_json_path: str, out_path: str) -> None:
    with open(discovery_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tfv = _parse_tfvars(tfvars_path)
    # stash for helpers that call _emit_import
    tfv["_tfvars_path"] = tfvars_path
    vpc_name = tfv.get("vpc_name") or _tag_value((data.get("vpc") or {}).get("tags") or [], "Name") or "vpc"

    # Validate import coverage
    validation = _validate_import_coverage(data, tfv)

    public_cidrs = tfv.get("public_subnet_cidrs") or []
    private_cidrs = tfv.get("private_subnet_cidrs") or []
    nonroutable_cidrs = tfv.get("nonroutable_subnet_cidrs") or []
    additional_cidrs = tfv.get("additional_cidrs") or []

    subnets = data.get("subnets") or []
    subnets_by_cidr = {s.get("cidr_block"): s for s in subnets if s.get("cidr_block") and s.get("id")}
    subnets_by_id = {s.get("id"): s for s in subnets if s.get("id")}

    cidr_assoc = {a.get("cidr_block"): a.get("association_id") for a in data.get("cidr_block_associations") or []}

    route_table_assocs = data.get("route_table_associations") or []

    # Route table ids by subnet CIDR using associations
    public_rt_id: Optional[str] = None
    private_rt_by_cidr: Dict[str, str] = {}
    nonroutable_rt_by_cidr: Dict[str, str] = {}

    for a in route_table_assocs:
        subnet_id = a.get("subnet_id")
        if not subnet_id:
            continue
        rt_id = a.get("route_table_id")
        s = subnets_by_id.get(subnet_id)
        if not s or not rt_id:
            continue
        cidr = s.get("cidr_block")
        tier = (s.get("tier") or "").lower()
        if tier == "public" and cidr in public_cidrs:
            public_rt_id = public_rt_id or rt_id
        elif tier == "private" and cidr in private_cidrs:
            private_rt_by_cidr[cidr] = rt_id
        elif tier == "nonroutable" and cidr in nonroutable_cidrs:
            nonroutable_rt_by_cidr[cidr] = rt_id

    # NACLs by Name tag - find by matching pattern (more flexible)
    # Look for NACLs with "public" and "private" in their names
    nacls = data.get("network_acls") or []
    custom_nacls = [n for n in nacls if not n.get("is_default")]
    
    public_nacl = None
    prn_nacl = None
    
    for nacl in custom_nacls:
        name = (_tag_value(nacl.get("tags") or [], "Name") or "").lower()
        if "public" in name and not "private" in name:
            public_nacl = nacl
        elif "private" in name or "nonroutable" in name:
            prn_nacl = nacl
    
    prn_nacl_id = (prn_nacl or {}).get("id") or ""

    # IGW
    igw_id = ((data.get("internet_gateway") or {}).get("id")) or ""

    # NATs + EIPs: Map NATs by checking each private/nonroutable subnet to find matching NAT
    # Public NATs (with EIP) should map to private subnets for Terraform module keys
    # Private NATs (no EIP) should map to nonroutable subnets for Terraform module keys
    nat_public_by_key: Dict[str, Dict[str, Any]] = {}
    nat_private_by_key: Dict[str, Dict[str, Any]] = {}
    eipalloc_by_key: Dict[str, str] = {}

    # Build lookup: NAT ID -> NAT data
    nats_by_id = {nat.get("id"): nat for nat in data.get("nat_gateways") or [] if nat.get("id")}
    
    # For each private subnet, find a public NAT (by checking route tables for NAT gateway ID)
    # For each nonroutable subnet, find a private NAT (by checking route tables for NAT gateway ID)
    routes = data.get("routes") or []
    
    # Map route_table_id -> NAT gateway ID used in that route table
    rt_nat_map: Dict[str, str] = {}
    for route in routes:
        rt_id = route.get("route_table_id")
        nat_id = route.get("NatGatewayId")
        if rt_id and nat_id and nat_id.startswith("nat-"):
            rt_nat_map[rt_id] = nat_id
    
    # Map private subnets to public NATs via route tables
    for cidr in private_cidrs:
        rt_id = private_rt_by_cidr.get(cidr)
        if not rt_id:
            continue
        nat_id = rt_nat_map.get(rt_id)
        if not nat_id:
            continue
        nat = nats_by_id.get(nat_id)
        if nat:
            nat_public_by_key[cidr] = nat
            # Get EIP allocation ID
            for addr in nat.get("nat_gateway_addresses") or []:
                alloc = addr.get("AllocationId")
                if alloc:
                    eipalloc_by_key[cidr] = alloc
                    break
    
    # Map nonroutable subnets to private NATs via route tables
    for cidr in nonroutable_cidrs:
        rt_id = nonroutable_rt_by_cidr.get(cidr)
        if not rt_id:
            continue
        nat_id = rt_nat_map.get(rt_id)
        if not nat_id:
            continue
        nat = nats_by_id.get(nat_id)
        if nat:
            nat_private_by_key[cidr] = nat

    # EIPs: in JSON, id is allocation id
    eips_by_alloc = {e.get("id"): e for e in data.get("eips") or [] if e.get("id")}

    # Security group: endpoints sg - find by matching "endpoint" in name
    sgs = data.get("security_groups") or []
    custom_sgs = [sg for sg in sgs if not sg.get("is_default")]
    
    endpoints_sg = None
    for sg in custom_sgs:
        name = (_tag_value(sg.get("tags") or [], "Name") or "").lower()
        if "endpoint" in name:
            endpoints_sg = sg
            break
    
    endpoints_sg_id = (endpoints_sg or {}).get("id") or ""

    # VPC endpoints by service suffix
    endpoints = data.get("vpc_endpoints") or []
    vpce_by_suffix: Dict[str, str] = {}
    for ep in endpoints:
        ep_id = ep.get("id")
        svc = (ep.get("service_name") or "")
        if not ep_id or not svc:
            continue
        # Extract service name from format: com.amazonaws.<region>.<service>
        service_suffix = svc.split(".")[-1]
        vpce_by_suffix[service_suffix] = ep_id

    # DHCP
    dhcp_id = (data.get("dhcp_options") or {}).get("id") or ""
    dhcp_assoc_id = (data.get("dhcp_options_association") or {}).get("import_id") or ""

    vpc_id = (data.get("vpc") or {}).get("id") or ""

    lines: List[str] = []
    lines.append("#!/usr/bin/env bash")
    # Don't use set -e: we want to continue importing even if one import fails.
    lines.append("set -u")
    lines.append("set -o pipefail")
    lines.append("\n# Auto-generated terraform import script")
    import_dir_posix = _posix_path(import_dir)
    discovery_posix = _posix_path(discovery_json_path)
    tfvars_posix = _posix_path(tfvars_path)

    lines.append(f"# import_dir: {import_dir_posix}")
    lines.append(f"# discovery_json: {discovery_posix}")
    lines.append(f"# tfvars: {tfvars_posix}\n")

    lines.append(f"TFVARS_FILE={_bash_quote_single(tfvars_posix)}")
    lines.append(f"DISCOVERY_JSON={_bash_quote_single(discovery_posix)}")

    backend_config = f"{import_dir_posix}/backend-config"
    lines.append(f"BACKEND_CONFIG={_bash_quote_single(backend_config)}")
    lines.append("\n")

    # Allow running the script from anywhere.
    lines.append('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"')
    lines.append('REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"')
    lines.append('cd "$REPO_ROOT"')
    lines.append("\n")

    # Log everything (stdout + stderr) to a timestamped log file under the import folder.
    lines.append('TS="$(date +%Y%m%d_%H%M%S)"')
    lines.append('LOG_FILE="$SCRIPT_DIR/import_${TS}.log"')
    lines.append('echo "Logging to: $LOG_FILE"')
    lines.append('exec > >(tee -a "$LOG_FILE") 2>&1')
    lines.append("\n")

    # Ensure we use the import backend before importing anything.
    lines.append('echo "Initializing Terraform backend (import backend-config)..."')
    lines.append("terraform init -reconfigure -backend-config=$BACKEND_CONFIG")
    lines.append("\n")

    lines.append("# Cache current state list (best effort)")
    lines.append('STATE_LIST_FILE="$SCRIPT_DIR/.state_list_${TS}.txt"')
    lines.append('terraform state list > "$STATE_LIST_FILE" 2>/dev/null || true')
    lines.append("\n")

    lines.append('IMPORTED=0')
    lines.append('SKIPPED=0')
    lines.append('FAILED=0')
    lines.append("\n")

    lines.append('import_one() {')
    lines.append('  local label="$1"')
    lines.append('  local addr="$2"')
    lines.append('  local id="$3"')
    lines.append('')
    lines.append('  if [[ -z "$id" ]]; then')
    lines.append('    echo "WARN: missing id for $label -> $addr"')
    lines.append('    FAILED=$((FAILED+1))')
    lines.append('    return 0')
    lines.append('  fi')
    lines.append('')
    lines.append('  if grep -Fxq -- "$addr" "$STATE_LIST_FILE" 2>/dev/null; then')
    lines.append('    echo "SKIP (already in state): $label -> $addr"')
    lines.append('    SKIPPED=$((SKIPPED+1))')
    lines.append('    return 0')
    lines.append('  fi')
    lines.append('')
    lines.append('  echo "IMPORT: $label -> $addr ($id)"')
    lines.append('  if terraform import -var-file="$TFVARS_FILE" "$addr" "$id"; then')
    lines.append('    echo "OK: $addr"')
    lines.append('    IMPORTED=$((IMPORTED+1))')
    lines.append('    echo "$addr" >> "$STATE_LIST_FILE"')
    lines.append('    return 0')
    lines.append('  else')
    lines.append('    echo "FAIL: $addr"')
    lines.append('    FAILED=$((FAILED+1))')
    lines.append('    return 0')
    lines.append('  fi')
    lines.append('}')
    lines.append("\n")

    # VPC
    _emit_import(lines, tfvars_posix, "module.vpc.aws_vpc.child_module", vpc_id, "VPC")

    # Additional CIDR associations
    for c in additional_cidrs:
        assoc_id = cidr_assoc.get(c) or ""
        addr = f'module.vpc.aws_vpc_ipv4_cidr_block_association.additional["{c}"]'
        _emit_import(lines, tfvars_posix, addr, assoc_id, f"VPC CIDR association {c}")

    # Subnets
    for cidr in public_cidrs:
        sid = (subnets_by_cidr.get(cidr) or {}).get("id") or ""
        addr = f'module.public_subnets["{cidr}"].aws_subnet.child_module'
        _emit_import(lines, tfvars_posix, addr, sid, f"public subnet {cidr}")

    for cidr in private_cidrs:
        sid = (subnets_by_cidr.get(cidr) or {}).get("id") or ""
        addr = f'module.private_subnets["{cidr}"].aws_subnet.child_module'
        _emit_import(lines, tfvars_posix, addr, sid, f"private subnet {cidr}")

    for cidr in nonroutable_cidrs:
        sid = (subnets_by_cidr.get(cidr) or {}).get("id") or ""
        addr = f'module.nonroutable_subnets["{cidr}"].aws_subnet.child_module'
        _emit_import(lines, tfvars_posix, addr, sid, f"nonroutable subnet {cidr}")

    # Gateways
    _emit_import(lines, tfvars_posix, "module.gateways.aws_internet_gateway.igw[0]", igw_id, "internet gateway")

    for cidr in sorted(private_cidrs):
        nat = nat_public_by_key.get(cidr) or {}
        nid = nat.get("id") or ""
        addr = f'module.gateways.aws_nat_gateway.public["{cidr}"]'
        _emit_import(lines, tfvars_posix, addr, nid, f"public NAT {cidr}")

        alloc = eipalloc_by_key.get(cidr) or ""
        if alloc and alloc not in eips_by_alloc:
            # still import by allocation id, even if not in eips list
            pass
        eip_addr = f'module.gateways.aws_eip.nat_eip["{cidr}"]'
        _emit_import(lines, tfvars_posix, eip_addr, alloc, f"EIP for public NAT {cidr}")

    for cidr in sorted(nonroutable_cidrs):
        nat = nat_private_by_key.get(cidr) or {}
        nid = nat.get("id") or ""
        addr = f'module.gateways.aws_nat_gateway.private["{cidr}"]'
        _emit_import(lines, tfvars_posix, addr, nid, f"private NAT {cidr}")

    # Route tables
    if public_rt_id:
        _emit_import(lines, tfvars_posix, "module.public_route_table[0].aws_route_table.this", public_rt_id, "public route table")

    for cidr in private_cidrs:
        rt_id = private_rt_by_cidr.get(cidr) or ""
        addr = f'module.private_route_tables["{cidr}"].aws_route_table.this'
        _emit_import(lines, tfvars_posix, addr, rt_id, f"private route table {cidr}")

    for cidr in nonroutable_cidrs:
        rt_id = nonroutable_rt_by_cidr.get(cidr) or ""
        addr = f'module.nonroutable_route_tables["{cidr}"].aws_route_table.this'
        _emit_import(lines, tfvars_posix, addr, rt_id, f"nonroutable route table {cidr}")

    # Route table associations
    if public_rt_id:
        # association idx is based on values(module.public_subnets) which is sorted by subnet keys (cidr strings)
        ordered_public = sorted(public_cidrs)
        for idx, cidr in enumerate(ordered_public):
            subnet_id = (subnets_by_cidr.get(cidr) or {}).get("id")
            assoc_id = ""
            if subnet_id:
                # aws_route_table_association import id is subnet-id/route-table-id (NOT rtbassoc-*)
                assoc_id = f"{subnet_id}/{public_rt_id}"
            addr = f'module.public_route_table[0].aws_route_table_association.this["{idx}"]'
            _emit_import(lines, tfvars_posix, addr, assoc_id, f"public rtb association {cidr}")

    for cidr in private_cidrs:
        rt_id = private_rt_by_cidr.get(cidr)
        subnet_id = (subnets_by_cidr.get(cidr) or {}).get("id")
        assoc_id = ""
        if rt_id and subnet_id:
            # aws_route_table_association import id is subnet-id/route-table-id (NOT rtbassoc-*)
            assoc_id = f"{subnet_id}/{rt_id}"
        addr = f'module.private_route_tables["{cidr}"].aws_route_table_association.this["0"]'
        _emit_import(lines, tfvars_posix, addr, assoc_id, f"private rtb association {cidr}")

    for cidr in nonroutable_cidrs:
        rt_id = nonroutable_rt_by_cidr.get(cidr)
        subnet_id = (subnets_by_cidr.get(cidr) or {}).get("id")
        assoc_id = ""
        if rt_id and subnet_id:
            # aws_route_table_association import id is subnet-id/route-table-id (NOT rtbassoc-*)
            assoc_id = f"{subnet_id}/{rt_id}"
        addr = f'module.nonroutable_route_tables["{cidr}"].aws_route_table_association.this["0"]'
        _emit_import(lines, tfvars_posix, addr, assoc_id, f"nonroutable rtb association {cidr}")

    # Routes (explicit resources in root)
    if public_rt_id:
        _emit_import(lines, tfvars_posix, 'aws_route.public_default["default"]', f"{public_rt_id}_0.0.0.0/0", "public default route")

    for cidr in private_cidrs:
        rt_id = private_rt_by_cidr.get(cidr) or ""
        addr = f'aws_route.private_default["{cidr}"]'
        rid = f"{rt_id}_0.0.0.0/0" if rt_id else ""
        _emit_import(lines, tfvars_posix, addr, rid, f"private default route {cidr}")

    for cidr in nonroutable_cidrs:
        rt_id = nonroutable_rt_by_cidr.get(cidr) or ""
        addr = f'aws_route.nonroutable_default["{cidr}"]'
        rid = f"{rt_id}_0.0.0.0/0" if rt_id else ""
        _emit_import(lines, tfvars_posix, addr, rid, f"nonroutable default route {cidr}")

    # NACLs
    public_nacl_id = (public_nacl or {}).get("id") or ""
    _emit_import(lines, tfvars_posix, "module.nacls.aws_network_acl.public[0]", public_nacl_id, "public NACL")
    _emit_import(
        lines,
        tfvars_posix,
        "module.nacls.aws_network_acl.private_nonroutable[0]",
        prn_nacl_id,
        "private+nonroutable NACL",
    )

    # NACL rules: import only those declared in tfvars (by rule_number)
    nacl_rules = data.get("network_acl_rules") or []
    _emit_nacl_rule_imports(
        lines,
        tfv,
        module_prefix="module.nacls",
        nacl_id=public_nacl_id,
        nacl_rules=nacl_rules,
        ingress_resource="public_ingress",
        egress_resource="public_egress",
        allowed_ingress_rule_numbers=tfv.get("nacl_public_ingress_rule_numbers") or [],
        allowed_egress_rule_numbers=tfv.get("nacl_public_egress_rule_numbers") or [],
    )
    _emit_nacl_rule_imports(
        lines,
        tfv,
        module_prefix="module.nacls",
        nacl_id=prn_nacl_id,
        nacl_rules=nacl_rules,
        ingress_resource="private_ingress",
        egress_resource="private_egress",
        allowed_ingress_rule_numbers=tfv.get("nacl_private_ingress_rule_numbers") or [],
        allowed_egress_rule_numbers=tfv.get("nacl_private_egress_rule_numbers") or [],
    )

    # DHCP
    _emit_import(lines, tfvars_posix, "module.dhcp_options.aws_vpc_dhcp_options.this", dhcp_id, "DHCP options")
    # aws_vpc_dhcp_options_association import id is the VPC id (NOT vpc-id/dopt-id)
    _emit_import(
        lines,
        tfvars_posix,
        "module.dhcp_options.aws_vpc_dhcp_options_association.this",
        vpc_id or dhcp_assoc_id,
        "DHCP association",
    )

    # Security group for endpoints - only import if module will be created
    # Module count condition: enable_interface_endpoints && enable_vpc_endpoints_sg && length(vpc_endpoints_security_group_ids) == 0
    enable_interface_endpoints = tfv.get("enable_interface_endpoints", False)
    enable_vpc_endpoints_sg = tfv.get("enable_vpc_endpoints_sg", False)
    vpc_endpoints_sg_ids = tfv.get("vpc_endpoints_security_group_ids", [])
    
    if enable_interface_endpoints and enable_vpc_endpoints_sg and len(vpc_endpoints_sg_ids) == 0 and endpoints_sg_id:
        _emit_import(lines, tfvars_posix, "module.vpc_endpoints_sg[0].aws_security_group.this", endpoints_sg_id, "VPC endpoints security group")

    # Extra security groups from tfvars (map of name -> sg definition)
    extra_sgs = tfv.get("extra_security_groups", {})
    sgs_by_name = {sg.get("group_name"): sg for sg in data.get("security_groups", []) if sg.get("group_name")}
    
    for sg_key, sg_config in extra_sgs.items():
        # Match by the name in the tfvars configuration
        sg_name = sg_config.get("name", "")
        sg = sgs_by_name.get(sg_name)
        if sg:
            sg_id = sg.get("id", "")
            addr = f'module.extra_security_groups["{sg_key}"].aws_security_group.this'
            _emit_import(lines, tfvars_posix, addr, sg_id, f"security group {sg_name}")

    # VPC endpoints
    _emit_import(lines, tfvars_posix, "module.s3_vpc_endpoint[0].aws_vpc_endpoint.this", vpce_by_suffix.get("s3") or "", "S3 VPC endpoint")
    
    # Interface VPC endpoints (dynamic based on tfvars)
    interface_endpoints = tfv.get("interface_vpc_endpoints", [])
    for service in interface_endpoints:
        vpce_id = vpce_by_suffix.get(service, "")
        if vpce_id:
            addr = f'module.interface_vpc_endpoints["{service}"].aws_vpc_endpoint.this'
            _emit_import(lines, tfvars_posix, addr, vpce_id, f"{service.upper()} VPC endpoint")


    # Extra routes from tfvars
    routes_by_rt_dest = {}
    for route in data.get("routes", []):
        rt_id = route.get("route_table_id", "")
        dest = route.get("DestinationCidrBlock", "")
        if rt_id and dest:
            routes_by_rt_dest[f"{rt_id}_{dest}"] = route

    # Public extra routes
    for idx, route in enumerate(tfv.get("public_extra_routes", [])):
        dest_cidr = route.get('destination_cidr_block', '')
        target_type = route.get('target_type', '')
        target_id = route.get('target_id', '')
        if public_rt_id and dest_cidr and target_type and target_id:
            # Key format in main.tf: "${r.destination_cidr_block}-${r.target_type}-${r.target_id}-${idx}"
            route_key = f"{dest_cidr}-{target_type}-{target_id}-{idx}"
            addr = f'aws_route.public_extra["{route_key}"]'
            rid = f"{public_rt_id}_{dest_cidr}"
            _emit_import(lines, tfvars_posix, addr, rid, f"public extra route {dest_cidr}")

    # Private extra routes
    for idx, route in enumerate(tfv.get("private_extra_routes", [])):
        dest_cidr = route.get('destination_cidr_block', '')
        target_type = route.get('target_type', '')
        target_id = route.get('target_id', '')
        if not (dest_cidr and target_type and target_id):
            continue
        # Key format in main.tf for private routes: "${rt_key}-${r_key}"
        # where r_key = "${r.destination_cidr_block}-${r.target_type}-${r.target_id}-${idx}"
        route_key = f"{dest_cidr}-{target_type}-{target_id}-{idx}"
        for cidr in private_cidrs:
            rt_id = private_rt_by_cidr.get(cidr, "")
            if rt_id:
                combined_key = f"{cidr}-{route_key}"
                addr = f'aws_route.private_extra["{combined_key}"]'
                rid = f"{rt_id}_{dest_cidr}"
                _emit_import(lines, tfvars_posix, addr, rid, f"private extra route {cidr} -> {dest_cidr}")

    # Nonroutable extra routes
    for idx, route in enumerate(tfv.get("nonroutable_extra_routes", [])):
        dest_cidr = route.get('destination_cidr_block', '')
        target_type = route.get('target_type', '')
        target_id = route.get('target_id', '')
        if not (dest_cidr and target_type and target_id):
            continue
        # Key format in main.tf for nonroutable routes: "${rt_key}-${r_key}"
        # where r_key = "${r.destination_cidr_block}-${r.target_type}-${r.target_id}-${idx}"
        route_key = f"{dest_cidr}-{target_type}-{target_id}-{idx}"
        for cidr in nonroutable_cidrs:
            rt_id = nonroutable_rt_by_cidr.get(cidr, "")
            if rt_id:
                combined_key = f"{cidr}-{route_key}"
                addr = f'aws_route.nonroutable_extra["{combined_key}"]'
                rid = f"{rt_id}_{dest_cidr}"
                _emit_import(lines, tfvars_posix, addr, rid, f"nonroutable extra route {cidr} -> {dest_cidr}")

    lines.append("\necho \"Done.\"\n")
    lines.append('echo "Summary: imported=$IMPORTED skipped=$SKIPPED failed=$FAILED"')
    lines.append('echo "Log: $LOG_FILE"')
    lines.append('echo ""')
    lines.append('echo "Resource summary (from terraform state):"')
    lines.append('VPC_ID=""')
    lines.append('VPC_ID=$(terraform state show -no-color module.vpc.aws_vpc.child_module 2>/dev/null | awk -F" = " \'/^[[:space:]]*id[[:space:]]*=[[:space:]]*/{gsub(/"/,"",$2); print $2; exit}\' || true)')
    lines.append('echo "VPC: ${VPC_ID:-unknown}"')
    lines.append('')
    lines.append('STATE_UNIQ_FILE="$SCRIPT_DIR/.state_list_${TS}.uniq.txt"')
    lines.append('sort -u "$STATE_LIST_FILE" > "$STATE_UNIQ_FILE" 2>/dev/null || cp "$STATE_LIST_FILE" "$STATE_UNIQ_FILE"')
    lines.append('')
    lines.append('count_re() {')
    lines.append('  local re="$1"')
    lines.append('  grep -cE "$re" "$STATE_UNIQ_FILE" 2>/dev/null || echo 0')
    lines.append('}')
    lines.append('')
    lines.append(r'SUBNETS=$(count_re "^module\\.(public|private|nonroutable)_subnets\\[\\\".*\\\"\\]\\.aws_subnet\\.child_module$")')
    lines.append(r'NACLS=$(count_re "^module\\.nacls\\.aws_network_acl\\..+$")')
    lines.append(r'ROUTE_TABLES=$(count_re "^module\\.(public_route_table\\[0\\]|private_route_tables\\[\\\".*\\\"\\]|nonroutable_route_tables\\[\\\".*\\\"\\])\\.aws_route_table\\.this$")')
    lines.append(r'NAT_GWS=$(count_re "^module\\.gateways\\.aws_nat_gateway\\.(public|private)\\[\\\".*\\\"\\]$")')
    lines.append(r'EIPS=$(count_re "^module\\.gateways\\.aws_eip\\.nat_eip\\[\\\".*\\\"\\]$")')
    lines.append(r'IGW_COUNT=$(count_re "^module\\.gateways\\.aws_internet_gateway\\.igw\\[0\\]$")')
    lines.append(r'VPCE=$(count_re "^module\\.(s3_vpc_endpoint\\[0\\]|interface_vpc_endpoints\\[\\\".*\\\"\\])\\.aws_vpc_endpoint\\.this$")')
    lines.append(r'SGS=$(count_re "^module\\.(vpc_endpoints_sg\\[0\\]|extra_security_groups\\[\\\".*\\\"\\])\\.aws_security_group\\.this$")')
    lines.append(r'DHCP_COUNT=$(count_re "^module\\.dhcp_options\\.aws_vpc_dhcp_options\\.this$")')
    lines.append(r'ROUTES=$(count_re "^aws_route\\.(public|private|nonroutable)_(default|extra)\\[.*\\]$")')
    lines.append('')
    lines.append('echo "Subnets: $SUBNETS"')
    lines.append('echo "NACLs: $NACLS"')
    lines.append('echo "Route Tables: $ROUTE_TABLES"')
    lines.append('echo "Routes: $ROUTES"')
    lines.append('echo "NAT Gateways: $NAT_GWS"')
    lines.append('echo "EIPs: $EIPS"')
    lines.append('if [[ "$IGW_COUNT" -gt 0 ]]; then echo "IGW: yes"; else echo "IGW: no"; fi')
    lines.append('echo "VPC Endpoints: $VPCE"')
    lines.append('echo "Security Groups: $SGS"')
    lines.append('if [[ "$DHCP_COUNT" -gt 0 ]]; then echo "DHCP Options: yes"; else echo "DHCP Options: no"; fi')
    lines.append('echo "====================="')
    lines.append('TOTAL_IMPORTED=$((IMPORTED+SKIPPED))')
    lines.append('echo "Total resource imported: $TOTAL_IMPORTED"')
    lines.append('if [[ "$FAILED" -ne 0 ]]; then exit 1; fi')

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    
    # Print validation report
    print("\n" + "="*60)
    print("IMPORT SCRIPT VALIDATION REPORT")
    print("="*60)
    print(f"Total importable resources in discovery JSON: {validation['total_importable']}")
    print(f"Resources that will get import commands: {validation['will_import']}")
    print(f"Resources skipped: {len(validation['skipped'])}")
    
    if validation['skipped']:
        print("\nSkipped Resources (will NOT be imported):")
        for skip in validation['skipped']:
            skip_type = skip.get('type', 'unknown')
            skip_id = skip.get('id', skip.get('dest', 'N/A'))
            reason = skip.get('reason', 'No reason provided')
            print(f"  • {skip_type}: {skip_id}")
            print(f"    Reason: {reason}")
    
    if validation['warnings']:
        print("\nWarnings:")
        for warn in validation['warnings']:
            print(f"  ⚠ {warn}")
    
    coverage_pct = (validation['will_import'] / validation['total_importable'] * 100) if validation['total_importable'] > 0 else 0
    print(f"\nImport Coverage: {coverage_pct:.1f}% of discovered resources will be imported")
    print("="*60 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate terraform import script from discovery JSON + terraform.tfvars")
    parser.add_argument(
        "import_dir",
        nargs="?",
        default=None,
        help="Import directory (env/<env>/<vpc>-import). If omitted, lists env/*/*-import.",
    )
    parser.add_argument("--tfvars", default=None, help="Path to terraform.tfvars (default: <import_dir>/terraform.tfvars)")
    parser.add_argument("--json", dest="json_path", default=None, help="Discovery JSON path (default: latest under import_dir)")
    parser.add_argument("--out", default=None, help="Output script path (default: <import_dir>/import_all.sh)")
    args = parser.parse_args()

    if not args.import_dir:
        candidates = sorted(glob.glob(os.path.join("env", "*", "*-import")))
        if not candidates:
            print("No import folders found under env/*/*-import", file=sys.stderr)
            return 1
        print("Select an import folder:")
        for i, c in enumerate(candidates, 1):
            print(f"  {i}. {c}")
        sel = input(f"Enter number [1-{len(candidates)}]: ").strip()
        try:
            idx = int(sel)
            args.import_dir = candidates[idx - 1]
        except Exception:
            print("Invalid selection", file=sys.stderr)
            return 1

    import_dir = args.import_dir
    tfvars_path = args.tfvars or os.path.join(import_dir, "terraform.tfvars")
    discovery_json_path = args.json_path or _load_latest_discovery_json(import_dir)
    out_path = args.out or os.path.join(import_dir, "import_all.sh")

    generate(import_dir, tfvars_path, discovery_json_path, out_path)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

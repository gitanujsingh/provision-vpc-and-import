#!/usr/bin/env python3
import boto3, json, sys, re, os
from datetime import datetime
from pathlib import Path

def get_tag(res, keys):
    for tag in res.get('Tags', []):
        if tag['Key'].lower() in [k.lower() for k in keys]:
            return tag['Value']
    return None

def sanitize_env(name):
    s = name.replace('::', '--')
    return re.sub(r'[^a-zA-Z0-9\-_]', '-', s).lower()

def run_discovery(ec2, ram, vpc_info, region, acc_id):
    vid, vname, venv = vpc_info['id'], vpc_info['name'], vpc_info['env']
    f_path = Path("env") / venv / f"{vname}-import"
    f_path.mkdir(parents=True, exist_ok=True)

    out = {
        "metadata": {"acc": acc_id, "reg": region, "env": venv, "vpc": vid, "time": datetime.now().isoformat()},
        "shares": []
    }

    print(f"\n[DEBUG] Analyzing RAM Shares for VPC: {vid}")

    try:
        # 1. Get all resource shares owned by this account
        pager = ram.get_paginator("get_resource_shares")
        for page in pager.paginate(resourceOwner='SELF'):
            for sh in page.get("resourceShares", []):
                s_arn = sh['resourceShareArn']
                s_data = {
                    "name": sh['name'],
                    "arn": s_arn,
                    "status": sh.get('status'),
                    "allowExternalPrincipals": sh.get('allowExternalPrincipals'),
                    "resources": [],
                    "principals": []
                }

                # 2. Get the actual resources in this share
                res_pager = ram.get_paginator("list_resources")
                for r_page in res_pager.paginate(resourceOwner='SELF', resourceShareArns=[s_arn]):
                    for r in r_page.get("resources", []):
                        r_arn = r['arn']
                        r_type = r['type']
                        match = False

                        # Check if resource is a subnet belonging to our target VPC
                        if "subnet" in r_type.lower():
                            try:
                                sub_id = r_arn.split('/')[-1]
                                v_chk = ec2.describe_subnets(SubnetIds=[sub_id])['Subnets'][0]['VpcId']
                                if v_chk == vid: match = True
                            except: pass
                        
                        s_data["resources"].append({
                            "arn": r_arn,
                            "type": r_type,
                            "vpc_match": match,
                            "last_updated": r.get('lastUpdatedTime').isoformat() if r.get('lastUpdatedTime') else None
                        })

                # 3. Get the principals (Accounts/OUs) this is shared with
                p_pager = ram.get_paginator("list_principals")
                for p_page in p_pager.paginate(resourceOwner='SELF', resourceShareArns=[s_arn]):
                    for p in p_page.get("principals", []):
                        s_data["principals"].append(p['id'])

                # Only add the share if it contains resources matching our VPC 
                # OR if it's a TGW share which is often relevant to the network stack
                if any(res['vpc_match'] for res in s_data['resources']) or "tgw" in sh['name'].lower():
                    out["shares"].append(s_data)

        final_file = f_path / "ram_resource.json"
        with open(final_file, "w") as f:
            json.dump(out, f, indent=4)
        print(f" Full details saved to: {final_file.as_posix()}")

    except Exception as e:
        print(f" Error during RAM discovery: {e}")

def main():
    print("--- AWS RAM Detailed Discovery ---")
    acc = input(" AWS Account ID: ").strip()
    reg = input(" AWS Region [us-east-1]: ").strip() or "us-east-1"
    
    sess = boto3.Session(region_name=reg)
    ec2, ram = sess.client("ec2"), sess.client("ram")

    vpcs = []
    print(f"\nListing VPCs in {reg}...")
    all_vpcs = ec2.describe_vpcs()['Vpcs']
    for i, v in enumerate(all_vpcs):
        name = get_tag(v, ['Name']) or "unnamed"
        env_raw = get_tag(v, ['Environment', 'Env', 'env']) or "unknown"
        vpcs.append({'id': v['VpcId'], 'name': name, 'env': sanitize_env(env_raw)})
        print(f"{i} | {v['VpcId']} | {env_raw} | {name}")

    sel = input("\n Enter Index (or Enter for ALL): ").strip()
    try:
        targets = vpcs if sel == "" else [vpcs[int(sel)]]
        for t in targets:
            run_discovery(ec2, ram, t, reg, acc)
    except (ValueError, IndexError):
        print(" Invalid selection.")

if __name__ == "__main__":
    main()
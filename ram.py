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
    # Matches your specific requirement: Non-production::Dev -> non-production--dev
    s = name.replace('::', '--')
    return re.sub(r'[^a-zA-Z0-9\-_]', '-', s).lower()

def run_discovery(ec2, ram, vpc_info, region, acc_id):
    vid, vname, venv = vpc_info['id'], vpc_info['name'], vpc_info['env']
    
    # Path: env/non-production--dev/ntw-npe-vpc-us-east-1-import/
    f_path = Path("env") / venv / f"{vname}-import"
    f_path.mkdir(parents=True, exist_ok=True)

    out = {
        "metadata": {"acc": acc_id, "reg": region, "env": venv, "vpc": vid, "time": datetime.now().isoformat()},
        "shares": []
    }

    try:
        for page in ram.get_paginator("get_resource_shares").paginate(resourceOwner='SELF'):
            for sh in page.get("resourceShares", []):
                s_data = {"name": sh['name'], "arn": sh['resourceShareArn'], "resources": []}
                for r_page in ram.get_paginator("list_resources").paginate(resourceOwner='SELF', resourceShareArns=[sh['resourceShareArn']]):
                    for r in r_page.get("resources", []):
                        rt = r.get("resourceType", "")
                        if not rt: continue
                        match = False
                        if "subnet" in rt.lower():
                            try:
                                v_chk = ec2.describe_subnets(SubnetIds=[r['arn'].split('/')[-1]])['Subnets'][0]['VpcId']
                                if v_chk == vid: match = True
                            except: pass
                        s_data["resources"].append({"type": rt, "arn": r['arn'], "vpc_match": match})
                out["shares"].append(s_data)

        final_file = f_path / "ram_resource.json"
        with open(final_file, "w") as f:
            json.dump(out, f, indent=4)
        print(f" Saved to: {final_file.as_posix()}")
    except Exception as e:
        print(f" Error: {e}")

def main():
    print("--- AWS RAM Tool ---")
    acc = input("AWS Account ID: ").strip()
    reg = input(" AWSRegion [us-east-1]: ").strip() or "us-east-1"
    
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
    targets = vpcs if sel == "" else [vpcs[int(sel)]]
    
    for t in targets:
        run_discovery(ec2, ram, t, reg, acc)

if __name__ == "__main__":
    main()